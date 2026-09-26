"""Парсер DOM.RIA (dom.ria.com). Данные берутся из JSON-LD, OpenGraph и блока характеристик страницы."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from bot.parsers.base import (
    BaseParser,
    ParsedProperty,
    absolutize,
    fill_from_text,
    from_json_ld,
    guess_offer_type,
    guess_property_type,
    json_ld,
    meta,
    page_text,
    soup_of,
    unique,
)

_PHOTO_RE = re.compile(r"https?://cdn\.riastatic\.com/photos(?:new)?/dom/photo/[^\"'\s)]+?\.(?:jpe?g|webp)", re.IGNORECASE)


class DomRiaParser(BaseParser):
    name = "DOM.RIA"
    domains = ("dom.ria.com",)

    def parse(self, url: str, html: str) -> ParsedProperty:
        p = ParsedProperty(source_url=url, source_name=self.name)
        m = re.search(r"-(\d{5,})\.html", url)
        if m:
            p.external_id = m.group(1)
        soup = soup_of(html)
        from_json_ld(p, json_ld(soup), url)

        if not p.title:
            h1 = soup.find("h1")
            p.title = h1.get_text(" ", strip=True) if h1 else meta(soup, "og:title")
        if not p.description:
            node = soup.select_one("#descriptionBlock, [class*=description], .boxed .text")
            p.description = node.get_text("\n", strip=True) if node else meta(soup, "og:description", "description")

        # Характеристики: списки «ключ — значение»
        for li in soup.select("#description li, .main-list li, ul.unstyle li, [class*=params] li"):
            label = li.select_one(".label, .argument, span:first-child")
            value = li.select_one(".indent, .boxed, span:last-child")
            if label and value and label is not value:
                k, v = label.get_text(" ", strip=True), value.get_text(" ", strip=True)
                if k and v and len(k) < 60:
                    p.features[k.rstrip(":")] = v

        og = absolutize(url, meta(soup, "og:image"))
        if og:
            p.photos.insert(0, og)
        p.photos.extend(_PHOTO_RE.findall(html))
        # Предпочитаем крупные версии фото (…xl.jpg / …fl.jpg), убирая дубли разных размеров
        normalized = []
        for src in p.photos:
            src = re.sub(r"(\d+)(?:s|m|b|f|fl|xg|xl)\.(jpe?g|webp)$", r"\1xl.\2", src)
            normalized.append(src)
        p.photos = unique(normalized)[:20]

        h = soup.select_one("h1")
        header = h.get_text(" ", strip=True) if h else ""
        text = "\n".join(
            filter(None, [p.title, header, "\n".join(f"{k}: {v}" for k, v in p.features.items()), p.description, page_text(soup)[:15000]])
        )
        fill_from_text(p, text)

        # Адрес/район обычно в заголовке: «Продаж 2-кімнатної квартири, вул. ..., Соборний, Дніпро»
        if p.title and "," in p.title:
            parts = [x.strip() for x in p.title.split(",") if x.strip()]
            if len(parts) >= 3:
                p.address = p.address or parts[1]
                p.district = p.district or (parts[-2] if len(parts) >= 4 else None)
                p.city = p.city or parts[-1]
        path = urlparse(url).path
        p.offer_type = p.offer_type or guess_offer_type(path, p.title or "")
        p.property_type = p.property_type or guess_property_type(path, p.title or "")
        return p
