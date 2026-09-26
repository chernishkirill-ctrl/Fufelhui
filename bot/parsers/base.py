"""Базовый интерфейс парсеров объявлений и общие эвристики.

Чтобы добавить новый источник:
  1. создайте класс-наследник BaseParser в bot/parsers/<site>.py;
  2. задайте name и domains, реализуйте parse(url, html);
  3. добавьте экземпляр в PARSERS в bot/services/parser_service.py (перед GenericParser).
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from bot.utils.text import detect_currency, parse_decimal


class ParseError(Exception):
    """Ошибка получения/разбора объявления — показывается пользователю как понятное сообщение."""


@dataclass
class ParsedProperty:
    source_url: str
    source_name: str
    external_id: str | None = None
    title: str | None = None
    description: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    offer_type: str | None = None  # "sale" | "rent"
    property_type: str | None = None
    city: str | None = None
    district: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    rooms: int | None = None
    area: Decimal | None = None
    floor: int | None = None
    floors_total: int | None = None
    photos: list[str] = field(default_factory=list)
    features: dict[str, str] = field(default_factory=dict)
    owner_name: str | None = None
    owner_phone: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_empty(self) -> bool:
        return not (self.title or self.description or self.price or self.photos)


class BaseParser(ABC):
    name: str = "base"
    domains: tuple[str, ...] = ()

    def can_handle(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in self.domains)

    @abstractmethod
    def parse(self, url: str, html: str) -> ParsedProperty:
        raise NotImplementedError


# ---------------- общие помощники ----------------

def soup_of(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001 - lxml может отсутствовать
        return BeautifulSoup(html, "html.parser")


def meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def json_ld(soup: BeautifulSoup) -> list[dict]:
    items: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                items.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)
    return items


def absolutize(base: str, src: str | None) -> str | None:
    if not src:
        return None
    src = src.strip().split(" ")[0]
    if src.startswith("data:"):
        return None
    if src.startswith("//"):
        return "https:" + src
    return urljoin(base, src)


_BAD_IMG = ("logo", "icon", "avatar", "sprite", "spinner", "placeholder", ".svg", "banner", "flag", "static/media")


def good_image(url: str) -> bool:
    low = url.lower()
    return low.startswith("http") and not any(b in low for b in _BAD_IMG)


def unique(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def page_text(soup: BeautifulSoup) -> str:
    clone = soup_of(str(soup))
    for tag in clone(["script", "style", "noscript", "nav", "header", "footer", "svg"]):
        tag.decompose()
    return re.sub(r"\n\s*\n+", "\n", clone.get_text("\n", strip=True))


# ---------------- эвристики по тексту (ru/uk) ----------------

_ROOMS_RE = [
    re.compile(r"(\d{1,2})\s*[-‑]?\s*(?:х\s*)?(?:комн|кімн|кім\.|к\.|к\b|room)", re.IGNORECASE),
    re.compile(r"(?:комнат|кімнат)[а-яіїє]*\s*[:\-]?\s*(\d{1,2})", re.IGNORECASE),
]
_WORD_ROOMS = {
    "одноком": 1, "однокім": 1, "1-ком": 1, "двухком": 2, "двоком": 2, "двокім": 2, "трехком": 3, "трёхком": 3,
    "трикім": 3, "четырехком": 4, "чотирикім": 4, "гостинк": 1, "смарт": 1,
}
_AREA_RE = [
    re.compile(r"(?:общая|загальна)\s+площадь?[а-яіїє]*\s*[:\-]?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м|m²|sq\.?\s*m)", re.IGNORECASE),
]
_FLOOR_RE = [
    re.compile(r"(?:этаж|поверх)\s*[:\-]?\s*(\d{1,2})\s*(?:/|из|з|of)\s*(\d{1,2})", re.IGNORECASE),
    re.compile(r"(\d{1,2})\s*(?:/|из|з)\s*(\d{1,2})\s*(?:эт|пов|поверх)", re.IGNORECASE),
    re.compile(r"(\d{1,2})\s*(?:-?й|-?ий)?\s*(?:этаж|поверх)\s*(?:из|з)\s*(\d{1,2})", re.IGNORECASE),
]
_FLOOR_SINGLE_RE = re.compile(r"(?:этаж|поверх)\s*[:\-]?\s*(\d{1,2})\b", re.IGNORECASE)
_FLOORS_TOTAL_RE = re.compile(r"(?:этажность|поверховість|этажей в доме|поверхів)\s*[:\-]?\s*(\d{1,2})", re.IGNORECASE)
_PRICE_RE = re.compile(
    r"(\$\s*\d[\d\s .,]*\d|\d[\d\s .,]*\d\s*(?:\$|usd|грн|₴|uah|€|eur|у\.\s*е\.))",
    re.IGNORECASE,
)


def guess_rooms(text: str) -> int | None:
    for rx in _ROOMS_RE:
        m = rx.search(text)
        if m and 0 < int(m.group(1)) < 20:
            return int(m.group(1))
    low = text.lower()
    for word, n in _WORD_ROOMS.items():
        if word in low:
            return n
    return None


def guess_area(text: str) -> Decimal | None:
    for rx in _AREA_RE:
        m = rx.search(text)
        if m:
            value = parse_decimal(m.group(1))
            if value and 5 <= value <= 100000:
                return value
    return None


def guess_floor(text: str) -> tuple[int | None, int | None]:
    for rx in _FLOOR_RE:
        m = rx.search(text)
        if m:
            floor, total = int(m.group(1)), int(m.group(2))
            if 0 <= floor <= total <= 200:
                return floor, total
    floor = total = None
    m = _FLOOR_SINGLE_RE.search(text)
    if m:
        floor = int(m.group(1))
    m = _FLOORS_TOTAL_RE.search(text)
    if m:
        total = int(m.group(1))
    return floor, total


def guess_price(text: str) -> tuple[Decimal | None, str | None]:
    for m in _PRICE_RE.finditer(text):
        raw = m.group(1)
        value = parse_decimal(raw)
        if value and value >= 50:
            return value, detect_currency(raw, "USD")
    return None, None


def guess_offer_type(url: str, text: str = "") -> str | None:
    low = (url + " " + text[:300]).lower()
    if any(k in low for k in ("arenda", "orenda", "rent", "аренд", "оренд", "posutochno", "dolgosrochn")):
        return "rent"
    if any(k in low for k in ("prodazha", "prodaja", "prodazh", "sale", "продаж", "продам")):
        return "sale"
    return None


def guess_property_type(url: str, text: str = "") -> str | None:
    low = (url + " " + text[:300]).lower()
    for keys, label in (
        (("kvartir", "kvartira", "квартир"), "Квартира"),
        (("dom", "budin", "будин", "дом"), "Дом"),
        (("komnat", "kimnat", "комнат", "кімнат"), "Комната"),
        (("kommerch", "komerc", "office", "офис", "комерц", "коммерч"), "Коммерция"),
        (("uchastok", "zemel", "ділянк", "участок"), "Участок"),
    ):
        if any(k in low for k in keys):
            return label
    return None


def fill_from_text(p: ParsedProperty, text: str) -> None:
    """Заполняет пустые поля эвристиками по тексту."""
    if p.rooms is None:
        p.rooms = guess_rooms(text)
    if p.area is None:
        p.area = guess_area(text)
    if p.floor is None or p.floors_total is None:
        floor, total = guess_floor(text)
        p.floor = p.floor if p.floor is not None else floor
        p.floors_total = p.floors_total if p.floors_total is not None else total
    if p.price is None:
        p.price, cur = guess_price(text)
        p.currency = p.currency or cur


def from_json_ld(p: ParsedProperty, items: list[dict], base_url: str) -> None:
    for item in items:
        types = item.get("@type")
        types = types if isinstance(types, list) else [types]
        types = {str(t).lower() for t in types if t}
        offers = item.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if types & {"product", "offer", "residence", "apartment", "house", "singlefamilyresidence", "realestatelisting", "accommodation"} or offers:
            p.title = p.title or item.get("name")
            p.description = p.description or item.get("description")
            if isinstance(offers, dict) and p.price is None:
                p.price = parse_decimal(str(offers.get("price") or offers.get("lowPrice") or "")) or None
                p.currency = p.currency or (offers.get("priceCurrency") or None)
            images = item.get("image")
            if isinstance(images, str):
                images = [images]
            if isinstance(images, list):
                for img in images:
                    src = img.get("url") if isinstance(img, dict) else img
                    src = absolutize(base_url, src)
                    if src:
                        p.photos.append(src)
            geo = item.get("geo")
            if isinstance(geo, dict) and p.latitude is None:
                try:
                    p.latitude, p.longitude = float(geo["latitude"]), float(geo["longitude"])
                except (KeyError, TypeError, ValueError):
                    pass
            addr = item.get("address")
            if isinstance(addr, dict):
                p.city = p.city or addr.get("addressLocality")
                p.address = p.address or addr.get("streetAddress")
            floor_size = item.get("floorSize")
            if isinstance(floor_size, dict) and p.area is None:
                p.area = parse_decimal(str(floor_size.get("value") or ""))
            rooms = item.get("numberOfRooms")
            if rooms and p.rooms is None:
                try:
                    p.rooms = int(float(rooms))
                except (TypeError, ValueError):
                    pass
