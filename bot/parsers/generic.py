"""Универсальный парсер: JSON-LD, OpenGraph и текстовые эвристики. Используется для неизвестных сайтов."""
from __future__ import annotations

from urllib.parse import urlparse

from bot.parsers.base import (
    BaseParser,
    ParsedProperty,
    absolutize,
    fill_from_text,
    from_json_ld,
    good_image,
    guess_offer_type,
    guess_property_type,
    json_ld,
    meta,
    page_text,
    soup_of,
    unique,
)


class GenericParser(BaseParser):
    name = "web"
    domains = ()

    def can_handle(self, url: str) -> bool:
        return True

    def parse(self, url: str, html: str) -> ParsedProperty:
        soup = soup_of(html)
        host = (urlparse(url).hostname or "").removeprefix("www.")
        p = ParsedProperty(source_url=url, source_name=host or self.name)
        from_json_ld(p, json_ld(soup), url)

        p.title = p.title or meta(soup, "og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else None)
        og_desc = meta(soup, "og:description", "description", "twitter:description")

        desc_block = None
        for selector in ("[itemprop=description]", "[data-cy=ad_description]", "[class*=description]", "article"):
            desc_block = soup.select_one(selector)
            if desc_block and len(desc_block.get_text(strip=True)) > 40:
                break
            desc_block = None
        if desc_block:
            p.description = p.description or desc_block.get_text("\n", strip=True)
        p.description = p.description or og_desc

        og_img = absolutize(url, meta(soup, "og:image", "twitter:image"))
        if og_img:
            p.photos.insert(0, og_img)
        for img in soup.find_all("img"):
            src = absolutize(url, img.get("data-src") or img.get("src") or img.get("data-lazy"))
            if src and good_image(src):
                width = img.get("width")
                if width and str(width).isdigit() and int(width) < 200:
                    continue
                p.photos.append(src)
        p.photos = unique([x for x in p.photos if good_image(x)])[:20]

        text = "\n".join(filter(None, [p.title, p.description, page_text(soup)[:20000]]))
        fill_from_text(p, text)
        p.offer_type = p.offer_type or guess_offer_type(urlparse(url).path, text)
        p.property_type = p.property_type or guess_property_type(urlparse(url).path, p.title or "")
        return p
