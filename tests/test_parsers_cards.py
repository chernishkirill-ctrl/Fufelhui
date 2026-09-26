"""Парсеры (на сохраненной разметке) и приватность публичных материалов."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from bot.database.models import OfferType, Property, PropertyStatus
from bot.parsers.base import ParseError, guess_floor, guess_price, guess_rooms
from bot.parsers.domria import DomRiaParser
from bot.parsers.generic import GenericParser
from bot.parsers.olx import OLXParser
from bot.services import cards, parser_service
from bot.utils.text import clean_public_text, find_phone, parse_money

OLX_STATE = {
    "ad": {
        "ad": {
            "id": 889123,
            "title": "Продам 1-комнатную квартиру, ж/м Победа",
            "description": "Уютная квартира.<br />Звоните +380 67 123 45 67, Иван. Telegram @ivan_owner",
            "price": {"regularPrice": {"value": 45000, "currencyCode": "USD"}},
            "photos": [
                {"link": "https://ireland.apollo.olxcdn.com/v1/files/abc/image;s={width}x{height}"},
                {"link": "https://ireland.apollo.olxcdn.com/v1/files/def/image;s={width}x{height}"},
            ],
            "params": [
                {"key": "number_of_rooms_string", "name": "Количество комнат", "value": "1 комната", "normalizedValue": "odnokomnatnye"},
                {"key": "total_area", "name": "Общая площадь", "value": "42 м²", "normalizedValue": "42"},
                {"key": "floor", "name": "Этаж", "value": "5", "normalizedValue": "5"},
                {"key": "total_floors", "name": "Этажность", "value": "9", "normalizedValue": "9"},
            ],
            "location": {"cityName": "Днепр", "districtName": "Соборный"},
            "map": {"lat": 48.4647, "lon": 35.0462},
            "category": {"type": "real_estate_sale"},
            "contact": {"name": "Иван"},
        }
    }
}


def olx_html() -> str:
    encoded = json.dumps(json.dumps(OLX_STATE, ensure_ascii=False), ensure_ascii=False)
    return f"<html><head><title>OLX</title></head><body><script>window.__PRERENDERED_STATE__= {encoded};</script></body></html>"


def test_olx_parser_state():
    url = "https://www.olx.ua/d/uk/obyavlenie/prodam-kvartiru-IDabcD1.html"
    parser = parser_service.pick_parser(url)
    assert isinstance(parser, OLXParser)
    p = parser_service.postprocess(parser.parse(url, olx_html()))
    assert p.title.startswith("Продам 1-комнатную")
    assert p.price == Decimal("45000") and p.currency == "USD"
    assert p.rooms == 1 and p.area == Decimal("42") and p.floor == 5 and p.floors_total == 9
    assert p.city == "Днепр" and p.district == "Соборный"
    assert p.latitude == pytest.approx(48.4647)
    assert p.offer_type == "sale"
    assert len(p.photos) == 2 and "{width}" not in p.photos[0]
    # контакты вынесены в приватные поля и вычищены из описания
    assert p.owner_phone and "123 45 67" in p.owner_phone
    assert "+380" not in p.description and "@ivan_owner" not in p.description
    assert p.owner_name == "Иван"


def test_olx_parser_html_fallback():
    html = """
    <html><head><meta property="og:image" content="https://ireland.apollo.olxcdn.com/v1/files/x/image"></head><body>
    <h4 data-cy="ad_title">Сдам 2к квартиру в центре</h4>
    <div data-testid="ad-price-container"><h3>12 000 грн.</h3></div>
    <div data-cy="ad_description"><div>Квартира 56 м2, 3/9 этаж. Тел 0671234567</div></div>
    </body></html>"""
    p = OLXParser().parse("https://www.olx.ua/d/uk/obyavlenie/arenda-IDxyz.html", html)
    assert p.title == "Сдам 2к квартиру в центре"
    assert p.price == Decimal("12000") and p.currency == "UAH"
    assert p.rooms == 2 and p.area == Decimal("56") and (p.floor, p.floors_total) == (3, 9)
    assert p.offer_type == "rent"


def test_domria_parser():
    html = """
    <html><head>
    <meta property="og:image" content="https://cdn.riastatic.com/photosnew/dom/photo/prodazha-kvartira__12345m.jpg">
    <script type="application/ld+json">{"@type": "Product", "name": "Продаж 3-кімнатної квартири, вул. Січеславська, Соборний, Дніпро",
      "description": "Простора квартира, 78 м², поверх 4 з 10", "offers": {"price": "98000", "priceCurrency": "USD"}}</script>
    </head><body><h1>Продаж 3-кімнатної квартири, вул. Січеславська, Соборний, Дніпро</h1>
    <img src="https://cdn.riastatic.com/photosnew/dom/photo/prodazha-kvartira__12346b.jpg"></body></html>"""
    url = "https://dom.ria.com/uk/realty-prodaja-kvartira-dnepr-12345678.html"
    parser = parser_service.pick_parser(url)
    assert isinstance(parser, DomRiaParser)
    p = parser.parse(url, html)
    assert p.external_id == "12345678"
    assert p.price == Decimal("98000") and p.currency == "USD"
    assert p.rooms == 3 and p.area == Decimal("78") and (p.floor, p.floors_total) == (4, 10)
    assert p.city == "Дніпро" and p.district == "Соборний" and p.address == "вул. Січеславська"
    assert p.offer_type == "sale" and p.property_type == "Квартира"
    assert all(x.endswith("xl.jpg") for x in p.photos) and len(p.photos) == 2


def test_generic_parser_opengraph():
    html = """<html><head><meta property="og:title" content="Дом 120 м2 под Днепром">
    <meta property="og:description" content="Продается дом 120 м², цена $85 000">
    <meta property="og:image" content="/img/house.jpg"></head><body><p>Hello</p></body></html>"""
    url = "https://example-realty.ua/sale/dom/1"
    p = GenericParser().parse(url, html)
    assert p.title.startswith("Дом") and p.price == Decimal("85000") and p.area == Decimal("120")
    assert p.photos == ["https://example-realty.ua/img/house.jpg"]
    assert p.source_name == "example-realty.ua"


def test_heuristics():
    assert guess_rooms("Сдам двухкомнатную квартиру") == 2
    assert guess_rooms("3-кімн. квартира") == 3
    assert guess_floor("этаж 7 из 16") == (7, 16)
    assert guess_price("Цена: 1 250 000 грн")[0] == Decimal("1250000")
    assert parse_money("$45 000") == (Decimal("45000"), "USD")
    assert parse_money("1500 eur") == (Decimal("1500"), "EUR")
    assert find_phone("пишите +38 (067) 123-45-67") is not None
    assert "067" not in clean_public_text("звоните 067 123 45 67 или https://t.me/x @agent_name")


async def test_ssrf_protection():
    with pytest.raises(ParseError):
        await parser_service.validate_url("http://127.0.0.1/admin")
    with pytest.raises(ParseError):
        await parser_service.validate_url("ftp://example.com/x")
    with pytest.raises(ParseError):
        await parser_service.validate_url("http://localhost:8080/")


async def test_import_error_message(monkeypatch):
    async def boom(url):
        raise ParseError(parser_service.USER_ERROR)

    monkeypatch.setattr(parser_service, "fetch_html", boom)
    with pytest.raises(ParseError) as exc:
        await parser_service.import_from_url("https://www.olx.ua/x")
    assert "добавьте объект вручную" in str(exc.value)


async def test_import_empty_page(monkeypatch):
    async def empty(url):
        return "<html><body></body></html>"

    monkeypatch.setattr(parser_service, "fetch_html", empty)
    with pytest.raises(ParseError):
        await parser_service.import_from_url("https://unknown.example/x")


def sample_property() -> Property:
    prop = Property(
        id=125, offer_type=OfferType.SALE, property_type="Квартира", rooms=1, area=Decimal("42"), floor=5, floors_total=9,
        price=Decimal("45000"), currency="USD", city="Днепр", district="Слобожанский", address="ул. Тайная, 7",
        description="Светлая квартира. Звоните собственнику +380671112233", owner_name="Петр", owner_phone="+380671112233",
        internal_comment="торг до 43к", status=PropertyStatus.ACTIVE, latitude=48.4647, longitude=35.0462, photos=[],
    )
    return prop


def test_public_caption_has_no_private_data():
    prop = sample_property()
    text = cards.public_caption(prop, "Nestima")
    assert "OBJ-000125" in text and "$45 000" in text and "#Слобожанский" in text
    for secret in ("Петр", "+380671112233", "торг", "Тайная", "комисси"):
        assert secret not in text
    assert len(text) <= 1024


def test_telegraph_and_map_privacy():
    prop = sample_property()
    dump = json.dumps(cards.telegraph_nodes(prop, ["https://x/1.jpg"]), ensure_ascii=False)
    for secret in ("Петр", "+380671112233", "торг", "Тайная"):
        assert secret not in dump
    url = cards.map_url(prop)
    assert "48.4650" in url and "35.0450" in url  # координаты округлены
    prop.show_exact_location = True
    assert "48.4647" in cards.map_url(prop)
    prop.latitude = prop.longitude = None
    prop.show_exact_location = False
    assert "%D0%A2%D0%B0%D0%B9%D0%BD%D0%B0%D1%8F" not in cards.map_url(prop)  # «Тайная» не попадает в ссылку


def test_base_card_format():
    prop = sample_property()
    text = cards.base_card(prop)
    assert "🏠 <b>Объект OBJ-000125</b>" in text
    assert "1-комнатная квартира" in text and "Площадь: 42 м²" in text and "Цена: $45 000" in text
    assert "+380" not in text
