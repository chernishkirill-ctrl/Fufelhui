"""Импорт объекта по ссылке: проверка URL, загрузка страницы, выбор парсера, очистка данных."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

import httpx

from bot.parsers.base import BaseParser, ParsedProperty, ParseError
from bot.parsers.domria import DomRiaParser
from bot.parsers.generic import GenericParser
from bot.parsers.olx import OLXParser
from bot.utils.text import clean_public_text, find_phone

logger = logging.getLogger(__name__)

# Порядок важен: специализированные парсеры раньше универсального
PARSERS: list[BaseParser] = [OLXParser(), DomRiaParser(), GenericParser()]

MAX_BYTES = 6 * 1024 * 1024
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7",
}

USER_ERROR = (
    "Не удалось автоматически получить данные по этой ссылке. "
    "Попробуйте другую ссылку или добавьте объект вручную."
)

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Районы (Днепр) для случаев, когда парсер не нашел район. Ключ — фрагмент текста в нижнем регистре.
KNOWN_DISTRICTS = {
    "соборн": "Соборный", "шевченк": "Шевченковский", "центральн": "Центральный", "чечелів": "Чечеловский",
    "чечелов": "Чечеловский", "новокодак": "Новокодакский", "амур": "Амур-Нижнеднепровский",
    "самарськ": "Самарский", "самарск": "Самарский", "індустріальн": "Индустриальный", "индустриальн": "Индустриальный",
    "слобожанськ": "Слобожанский", "слобожанск": "Слобожанский", "перемог": "Победа", "победа": "Победа",
    "тополь": "Тополь", "тополя": "Тополь", "парус": "Парус", "лівий берег": "Левый берег", "левый берег": "Левый берег",
    "гагаріна": "Гагарина", "гагарина": "Гагарина", "лоц": "Лоцманка", "сокол": "Сокол",
}


def extract_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(").,;") if m else None


def pick_parser(url: str) -> BaseParser:
    for parser in PARSERS:
        if parser.can_handle(url):
            return parser
    return PARSERS[-1]


def _is_public_host(host: str) -> bool:
    """Защита от SSRF: запрещаем запросы к localhost/внутренним сетям."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


async def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ParseError("Ссылка должна начинаться с http:// или https://")
    if parsed.port not in (None, 80, 443):
        raise ParseError("Неподдерживаемый адрес ссылки")
    ok = await asyncio.to_thread(_is_public_host, parsed.hostname)
    if not ok:
        raise ParseError("Этот адрес недоступен для импорта")
    return url


async def fetch_html(url: str) -> str:
    await validate_url(url)
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=False) as client:
            current = url
            for _ in range(5):
                async with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise ParseError(USER_ERROR)
                        current = str(resp.url.join(location))
                        await validate_url(current)
                        continue
                    if resp.status_code in (401, 403, 429, 503):
                        logger.warning("Импорт: сайт %s отклонил запрос (HTTP %s)", urlparse(url).hostname, resp.status_code)
                        raise ParseError(
                            "Сайт заблокировал автоматическое получение данных. "
                            "Попробуйте позже, другую ссылку или добавьте объект вручную."
                        )
                    if resp.status_code == 404:
                        raise ParseError("Объявление не найдено (удалено или ссылка неверная).")
                    if resp.status_code >= 400:
                        logger.warning("Импорт: HTTP %s для %s", resp.status_code, urlparse(url).hostname)
                        raise ParseError(USER_ERROR)
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ParseError("Страница слишком большая для импорта.")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    encoding = resp.encoding or "utf-8"
                    return body.decode(encoding, errors="replace")
            raise ParseError(USER_ERROR)
    except ParseError:
        raise
    except httpx.HTTPError as exc:
        logger.warning("Импорт: сетевая ошибка %s для %s", type(exc).__name__, urlparse(url).hostname)
        raise ParseError(USER_ERROR) from exc


def postprocess(p: ParsedProperty, default_city: str = "", default_currency: str = "USD") -> ParsedProperty:
    """Очистка: телефон собственника — в приватное поле, из описания убираем контакты/ссылки."""
    raw_desc = p.description or ""
    if not p.owner_phone:
        p.owner_phone = find_phone(raw_desc)
    p.description = clean_public_text(raw_desc, limit=3500, remove=[p.owner_name]) or None
    p.title = clean_public_text(p.title, limit=200) or None
    p.currency = (p.currency or default_currency or "USD").upper()
    if p.currency in {"ГРН", "UAH", "₴"}:
        p.currency = "UAH"
    if p.currency not in {"USD", "EUR", "UAH"}:
        p.currency = default_currency
    if not p.city and default_city:
        p.city = default_city
    if not p.district:
        haystack = " ".join(filter(None, [p.title, p.address, raw_desc])).lower()
        for key, name in KNOWN_DISTRICTS.items():
            if key in haystack:
                p.district = name
                break
    if p.address:
        p.address = p.address[:250]
    p.photos = [x for x in p.photos if x.startswith("http")][:20]
    return p


async def import_from_url(url: str, *, default_city: str = "", default_currency: str = "USD") -> ParsedProperty:
    html = await fetch_html(url)
    parser = pick_parser(url)
    try:
        parsed = parser.parse(url, html)
    except Exception as exc:  # noqa: BLE001 - любой сбой парсера не должен ронять бота
        logger.exception("Импорт: ошибка парсера %s", parser.name)
        raise ParseError(USER_ERROR) from exc
    if parsed.is_empty:
        logger.info("Импорт: парсер %s не нашел данных (%s)", parser.name, urlparse(url).hostname)
        raise ParseError(USER_ERROR)
    parsed = postprocess(parsed, default_city, default_currency)
    logger.info(
        "Импорт: %s, фото=%s, цена=%s, комнат=%s", parser.name, len(parsed.photos), bool(parsed.price), parsed.rooms
    )
    return parsed
