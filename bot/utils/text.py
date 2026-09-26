"""Форматирование и очистка текста."""
from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "UAH": "₴"}

_PHONE_RE = re.compile(r"(?<![\w/])\+?\d[\d\s\-().]{7,}\d(?![\w/])")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_TG_RE = re.compile(r"(?<![\w.])@[A-Za-z][A-Za-z0-9_]{3,}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def esc(value: object | None) -> str:
    return html.escape(str(value)) if value is not None else ""


def count_digits(s: str) -> int:
    return sum(ch.isdigit() for ch in s)


def find_phone(text: str | None) -> str | None:
    if not text:
        return None
    for m in _PHONE_RE.finditer(text):
        if 9 <= count_digits(m.group(0)) <= 15:
            return m.group(0).strip()
    return None


_CONTACT_WORDS = re.compile(
    r"звон|телефон|\bтел\b|\bтел\.|telegram|телеграм|viber|вайбер|whatsapp|ватсап|пишите|напишите|"
    r"контакт|дзвон|пишіть|напишіть|собственник|власник|хозя|господар|агентств|риелтор|ріелтор|комисси|комісі",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def clean_public_text(text: str | None, limit: int | None = None, remove: list[str | None] | None = None) -> str:
    """Готовит текст для публичных материалов: убирает телефоны, ссылки, e-mail, Telegram-юзернеймы,
    предложения с контактами/упоминанием собственника и комиссии, а также явно переданные имена (remove)."""
    if not text:
        return ""

    def _phone(m: re.Match) -> str:
        return "" if 9 <= count_digits(m.group(0)) <= 15 else m.group(0)

    kept_lines = []
    for line in text.splitlines():
        sentences = [x for x in _SENTENCE_SPLIT.split(line) if not _CONTACT_WORDS.search(x)]
        kept_lines.append(" ".join(sentences))
    text = "\n".join(kept_lines)
    for word in remove or []:
        if word and len(word.strip()) >= 2:
            text = re.sub(re.escape(word.strip()), "", text, flags=re.IGNORECASE)
    text = _EMAIL_RE.sub("", text)
    text = _URL_RE.sub("", text)
    text = _PHONE_RE.sub(_phone, text)
    text = _TG_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;:])\1+", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines()).strip()
    if limit and len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = cut.rstrip(",.;:- ") + "…"
    return text


def mask_phone(phone: str | None) -> str:
    """Для логов: +380****1234."""
    if not phone:
        return "-"
    digits = re.sub(r"\D", "", phone)
    return f"***{digits[-2:]}" if len(digits) >= 2 else "***"


def parse_decimal(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(" ", "").replace(" ", "")
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif s.count(",") == 1 and len(s.split(",")[1]) <= 2:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    if s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def detect_currency(raw: str | None, default: str = "USD") -> str:
    if not raw:
        return default
    low = raw.lower()
    if "$" in low or "usd" in low or "дол" in low or "у.е" in low:
        return "USD"
    if "€" in low or "eur" in low or "евро" in low or "євро" in low:
        return "EUR"
    if "₴" in low or "грн" in low or "uah" in low or "гривн" in low:
        return "UAH"
    return default


def parse_money(raw: str, default_currency: str = "USD") -> tuple[Decimal, str] | None:
    """«45000», «$45 000», «45 000 грн», «1500 eur» -> (Decimal, 'USD'/'UAH'/'EUR')."""
    amount = parse_decimal(raw)
    if amount is None or amount < 0:
        return None
    return amount, detect_currency(raw, default_currency)


def fmt_number(value: Decimal | float | int | None) -> str:
    if value is None:
        return "—"
    d = Decimal(str(value))
    if d == d.to_integral_value():
        return f"{int(d):,}".replace(",", " ")
    return f"{d:,.2f}".replace(",", " ")


def fmt_money(value: Decimal | float | int | None, currency: str | None = "USD") -> str:
    if value is None:
        return "—"
    currency = (currency or "USD").upper()
    num = fmt_number(value)
    sym = CURRENCY_SYMBOLS.get(currency)
    if currency == "USD":
        return f"${num}"
    if sym:
        return f"{num} {sym}"
    return f"{num} {currency}"


def fmt_money_map(totals: dict[str, Decimal]) -> str:
    if not totals:
        return fmt_money(0, "USD")
    return " · ".join(fmt_money(v, c) for c, v in sorted(totals.items()))


def hashtag(value: str | None) -> str:
    if not value:
        return ""
    tag = re.sub(r"[^\w]", "", value.replace("-", "_").replace(" ", "_"))
    tag = re.sub(r"_+", "_", tag).strip("_")
    return f"#{tag}" if tag else ""


def parse_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    m = re.search(r"-?\d+", str(raw))
    return int(m.group(0)) if m else None


def truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
