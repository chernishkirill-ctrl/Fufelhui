"""Парсер OLX (olx.ua и др.). Основной источник данных — встроенный JSON window.__PRERENDERED_STATE__,
с откатом на JSON-LD / HTML-разметку, если структура страницы изменилась."""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from bot.parsers.base import (
    BaseParser,
    ParsedProperty,
    absolutize,
    fill_from_text,
    from_json_ld,
    good_image,
    guess_offer_type,
    guess_price,
    guess_property_type,
    json_ld,
    meta,
    soup_of,
    unique,
)
from bot.utils.text import parse_decimal, parse_int

logger = logging.getLogger(__name__)

_STATE_RE = re.compile(r"window\.__PRERENDERED_STATE__\s*=\s*(\"(?:[^\"\\]|\\.)*\"|\{.*?\});", re.DOTALL)

# Ключи/названия параметров OLX
_ROOM_KEYS = ("number_of_rooms", "rooms")
_AREA_KEYS = ("total_area", "area")
_FLOOR_KEYS = ("floor",)
_TOTAL_FLOORS_KEYS = ("total_floors", "floors_total", "number_of_floors")


def _extract_state(html: str) -> dict | None:
    m = _STATE_RE.search(html)
    if not m:
        return None
    raw = m.group(1)
    try:
        if raw.startswith('"'):
            raw = json.loads(raw)  # строка с экранированным JSON
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("OLX: не удалось разобрать __PRERENDERED_STATE__")
        return None


def _param_value(param: dict) -> str:
    return str(param.get("normalizedValue") or param.get("value") or "")


def _match_param(params: list[dict], keys: tuple[str, ...], names: tuple[str, ...]) -> str | None:
    for param in params:
        key = str(param.get("key") or "").lower()
        name = str(param.get("name") or "").lower()
        if any(key.startswith(k) for k in keys) or any(n in name for n in names):
            return _param_value(param)
    return None


def _rooms_from_value(value: str | None) -> int | None:
    if not value:
        return None
    num = parse_int(value)
    if num:
        return num
    words = {"odno": 1, "dvuh": 2, "dvo": 2, "treh": 3, "tryoh": 3, "try": 3, "chetyreh": 4, "chotyr": 4, "pyat": 5}
    low = value.lower()
    for w, n in words.items():
        if low.startswith(w):
            return n
    return None


class OLXParser(BaseParser):
    name = "OLX"
    domains = ("olx.ua", "olx.pl", "olx.kz", "olx.uz", "olx.pt", "olx.ro", "olx.bg")

    def parse(self, url: str, html: str) -> ParsedProperty:
        p = ParsedProperty(source_url=url, source_name=self.name)
        m = re.search(r"-ID([A-Za-z0-9]+)\.html", url)
        if m:
            p.external_id = m.group(1)

        state = _extract_state(html)
        ad: dict[str, Any] | None = None
        if state:
            ad = (state.get("ad") or {}).get("ad") or state.get("ad")
        if isinstance(ad, dict) and ad.get("title"):
            self._from_state(p, ad)

        soup = soup_of(html)
        from_json_ld(p, json_ld(soup), url)
        if not p.title:
            node = soup.select_one("[data-cy=ad_title], [data-testid=ad_title] h4, h1, h4")
            p.title = node.get_text(strip=True) if node else meta(soup, "og:title")
        if not p.description:
            node = soup.select_one("[data-cy=ad_description] div, [data-cy=ad_description], [data-testid=ad_description]")
            p.description = node.get_text("\n", strip=True) if node else meta(soup, "og:description")
        if p.price is None:
            node = soup.select_one("[data-testid=ad-price-container] h3, [data-testid=ad-price-container]")
            if node:
                txt = node.get_text(" ", strip=True)
                p.price, p.currency = guess_price(txt)
        if not p.photos:
            for img in soup.select("[data-testid=swiper-image], [data-testid=image-galery-container] img, img"):
                src = absolutize(url, img.get("src") or img.get("data-src"))
                if src and good_image(src) and "apollo" in src:
                    p.photos.append(src)
            og = meta(soup, "og:image")
            if og:
                p.photos.insert(0, og)
        if not p.features:
            for li in soup.select("[data-testid=ad-parameters-container] p, [data-cy=ad-parameters] li"):
                txt = li.get_text(" ", strip=True)
                if ":" in txt:
                    k, v = txt.split(":", 1)
                    p.features[k.strip()] = v.strip()

        p.photos = unique(p.photos)[:20]
        text = "\n".join(filter(None, [p.title, p.description, "\n".join(f"{k}: {v}" for k, v in p.features.items())]))
        fill_from_text(p, text)
        path = urlparse(url).path
        p.offer_type = p.offer_type or guess_offer_type(path, p.title or "")
        p.property_type = p.property_type or guess_property_type(path, p.title or "")
        return p

    def _from_state(self, p: ParsedProperty, ad: dict[str, Any]) -> None:
        p.external_id = str(ad.get("id") or p.external_id or "") or None
        p.title = ad.get("title")
        desc = ad.get("description") or ""
        p.description = re.sub(r"<br\s*/?>", "\n", desc).replace("&nbsp;", " ")
        p.description = re.sub(r"<[^>]+>", "", p.description).strip()

        price = ad.get("price") or {}
        regular = price.get("regularPrice") or {}
        if regular.get("value") is not None:
            p.price = parse_decimal(str(regular.get("value")))
            p.currency = regular.get("currencyCode") or None

        for photo in ad.get("photos") or []:
            src = photo if isinstance(photo, str) else (photo.get("link") or photo.get("url") if isinstance(photo, dict) else None)
            if src:
                src = src.replace("{width}", "1280").replace("{height}", "960")
                p.photos.append(src)

        location = ad.get("location") or {}
        p.city = location.get("cityName") or None
        p.district = location.get("districtName") or None
        geo = ad.get("map") or {}
        try:
            if geo.get("lat") and geo.get("lon"):
                p.latitude, p.longitude = float(geo["lat"]), float(geo["lon"])
        except (TypeError, ValueError):
            pass

        params = ad.get("params") or []
        for param in params:
            name = param.get("name")
            if name:
                p.features[str(name)] = str(param.get("value") or param.get("normalizedValue") or "")
        p.rooms = _rooms_from_value(_match_param(params, _ROOM_KEYS, ("комнат", "кімнат")))
        area = _match_param(params, _AREA_KEYS, ("общая площадь", "загальна площа"))
        p.area = parse_decimal(area) if area else None
        floor = _match_param(params, _FLOOR_KEYS, ("этаж", "поверх"))
        total = _match_param(params, _TOTAL_FLOORS_KEYS, ("этажность", "поверховість"))
        # «Этажность» содержит «этаж», поэтому сначала ищем точное совпадение ключа
        for param in params:
            key = str(param.get("key") or "").lower()
            if key == "floor":
                floor = _param_value(param)
            elif key in _TOTAL_FLOORS_KEYS:
                total = _param_value(param)
        p.floor = parse_int(floor) if floor else None
        p.floors_total = parse_int(total) if total else None

        category = ad.get("category") or {}
        ctype = str(category.get("type") or "").lower()
        if "rent" in ctype or "arenda" in ctype:
            p.offer_type = "rent"
        elif "sale" in ctype or "prodazha" in ctype:
            p.offer_type = "sale"
        contact = ad.get("contact") or {}
        user = ad.get("user") or {}
        p.owner_name = contact.get("name") or user.get("name") or None
