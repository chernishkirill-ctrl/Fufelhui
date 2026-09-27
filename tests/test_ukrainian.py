"""Всё, что видит клиент, — на украинском (без русских букв ы/э/ъ/ё и русских слов интерфейса)."""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from bot.database.models import OfferType, Property, PropertyStatus
from bot.handlers import public
from bot.services import cards
from bot.utils import uk

RUSSIAN = re.compile(r"[ыЫэЭъЪёЁ]|\b(?:Записаться|просмотр|Цена|Площадь|Этаж|комнатная|аренда|Описание|карте)\b")


def rent_flat() -> Property:
    return Property(
        id=7, offer_type=OfferType.RENT, property_type="Квартира", rooms=2, area=Decimal("54"), floor=3,
        floors_total=9, price=Decimal("12000"), currency="UAH", city="Днепр", district="Шевченковский",
        status=PropertyStatus.ACTIVE, photos=[],
    )


def test_places_and_types():
    assert uk.place("Днепр") == "Дніпро"
    assert uk.place("Соборный район") == "Соборний район"
    assert uk.place("Победа") == "Перемога"
    assert uk.place("Новокодакский") == "Новокодацький"
    assert uk.place("Какой-то Заводской") == "Какой-то Заводський"  # незнакомое — по окончанию
    assert uk.rooms_title(1, "Квартира") == "1-кімнатна квартира"
    assert uk.rooms_title(None, "Коммерция") == "Комерційне приміщення"
    assert uk.rooms_title(3, "Дом") == "Будинок, 3 кімн."


def test_channel_post_is_ukrainian():
    prop = rent_flat()
    text = cards.public_post_text(prop, None, "https://telegra.ph/x")
    assert "2-кімнатна квартира</b> · оренда" in text and "12 000 ₴/міс" in text
    assert "Шевченківський район, Дніпро" in text and "#оренда" in text
    assert not RUSSIAN.search(text), text
    caption = cards.public_caption(prop)
    assert caption.startswith("<b>Оренда:") and not RUSSIAN.search(caption), caption


def test_telegraph_is_ukrainian():
    prop = rent_flat()
    dump = json.dumps(cards.telegraph_nodes(prop, [], booking_url="https://t.me/b/app"), ensure_ascii=False)
    for word in ("Ціна", "Розташування", "Кімнат", "Площа", "Поверх", "Записатися на перегляд", "Залишити заявку"):
        assert word in dump
    assert not RUSSIAN.search(dump), dump
    assert "Шевченківський" in cards.map_url(prop) or "%D0%A8" in cards.map_url(prop)


def test_client_help_and_mini_app_are_ukrainian():
    assert not RUSSIAN.search(public.CLIENT_HELP)
    page = Path("bot/webapp_static/lead.html").read_text(encoding="utf-8")
    visible = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    assert 'lang="uk"' in page and "Надіслати заявку" in visible
    assert not RUSSIAN.search(visible)
