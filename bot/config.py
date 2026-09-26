"""Конфигурация приложения. Все секреты и ID читаются из переменных окружения."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Переменная окружения {name} должна быть целым числом, получено: {raw!r}") from exc


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "да"}


_DB_URL_RE = re.compile(r"(postgres(?:ql)?(?:\+\w+)?|sqlite(?:\+\w+)?)://[^\s'\"`<>]+", re.IGNORECASE)


def normalize_database_url(url: str) -> str:
    """Приводит DATABASE_URL к виду для SQLAlchemy async.

    Render выдает postgres://... или postgresql://..., а нужен postgresql+asyncpg://...
    При копировании с телефона в значение часто попадают пробелы, кавычки, невидимые символы,
    префикс «DATABASE_URL=» или вся PSQL-команда — вытаскиваем из строки сам адрес.
    """
    raw = url or ""
    match = _DB_URL_RE.search(raw)
    if not match:
        raise RuntimeError(describe_bad_database_url(raw))
    url = match.group(0).rstrip(".,;)")
    scheme, rest = url.split("://", 1)
    scheme = scheme.lower()
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    return f"{scheme}://{rest}"


def describe_bad_database_url(raw: str) -> str:
    """Понятная ошибка без вывода пароля."""
    value = raw.strip()
    if not value:
        return "DATABASE_URL пустой. Вставьте Internal Database URL базы PostgreSQL."
    hint = ""
    low = value.lower()
    if low.startswith("pgpassword") or low.startswith("psql"):
        hint = " Похоже, вставлена строка «PSQL Command» — нужна строка «Internal Database URL»."
    elif low.startswith("dpg-"):
        hint = " Похоже, вставлен только «Hostname» — нужна строка «Internal Database URL»."
    elif "://" not in value:
        hint = " В значении нет «postgresql://» — скопируйте «Internal Database URL» целиком."
    return (
        f"DATABASE_URL не похож на адрес PostgreSQL (длина {len(value)}, начинается с «{value[:6]}…»)."
        + hint
    )


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    database_url: str

    work_chat_id: int | None = None
    public_channel_id: int | str | None = None
    base_topic_id: int | None = None
    archive_topic_id: int | None = None
    deals_topic_id: int | None = None
    chat_topic_id: int | None = None
    leads_topic_id: int | None = None

    telegraph_token: str | None = None
    agency_name: str = "Агентство недвижимости"
    agency_hashtag: str | None = None
    default_city: str = ""
    default_currency: str = "USD"
    timezone: str = "Europe/Kyiv"
    report_time: str = "22:00"

    webhook_base_url: str | None = None
    # Короткое имя Mini App из @BotFather (/newapp) — форма записи на просмотр во всплывающем окне
    webapp_short_name: str | None = None
    webhook_path: str = "/telegram/webhook"
    webhook_secret: str | None = None
    cron_secret: str | None = None
    port: int = 8080
    use_webhook: bool = False

    log_level: str = "INFO"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _channel_id(raw: str) -> int | str | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("@"):
        return raw
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("PUBLIC_CHANNEL_ID должен быть числом (-100...) или @username канала") from exc


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")
    owner_id = _int("OWNER_ID")
    if not owner_id:
        raise RuntimeError("Не задана переменная окружения OWNER_ID (Telegram user ID владельца)")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Не задана переменная окружения DATABASE_URL")

    # Render автоматически выставляет RENDER_EXTERNAL_URL для web-сервисов
    webhook_base = (os.getenv("WEBHOOK_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    mode = os.getenv("BOT_MODE", "").strip().lower()
    if mode == "polling":
        use_webhook = False
    elif mode == "webhook":
        if not webhook_base:
            raise RuntimeError("BOT_MODE=webhook требует WEBHOOK_BASE_URL (или RENDER_EXTERNAL_URL)")
        use_webhook = True
    else:
        use_webhook = bool(webhook_base)

    webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip() or None
    if use_webhook and not webhook_secret:
        # Секрет выводится из токена, чтобы не хранить его отдельно; Telegram допускает [A-Za-z0-9_-]
        import hashlib

        webhook_secret = hashlib.sha256(("wh:" + token).encode()).hexdigest()[:48]

    return Config(
        bot_token=token,
        owner_id=owner_id,
        database_url=normalize_database_url(database_url),
        work_chat_id=_int("WORK_CHAT_ID"),
        public_channel_id=_channel_id(os.getenv("PUBLIC_CHANNEL_ID", "")),
        base_topic_id=_int("BASE_TOPIC_ID"),
        archive_topic_id=_int("ARCHIVE_TOPIC_ID"),
        deals_topic_id=_int("DEALS_TOPIC_ID"),
        chat_topic_id=_int("CHAT_TOPIC_ID"),
        leads_topic_id=_int("LEADS_TOPIC_ID"),
        telegraph_token=os.getenv("TELEGRAPH_TOKEN", "").strip() or None,
        agency_name=os.getenv("AGENCY_NAME", "").strip() or "Агентство недвижимости",
        agency_hashtag=os.getenv("AGENCY_HASHTAG", "").strip().lstrip("#") or None,
        default_city=os.getenv("DEFAULT_CITY", "").strip(),
        default_currency=(os.getenv("DEFAULT_CURRENCY", "").strip() or "USD").upper(),
        timezone=os.getenv("TIMEZONE", "").strip() or "Europe/Kyiv",
        report_time=os.getenv("REPORT_TIME", "").strip() or "22:00",
        webhook_base_url=webhook_base or None,
        webapp_short_name=os.getenv("WEBAPP_SHORT_NAME", "").strip().strip("/") or None,
        webhook_path=os.getenv("WEBHOOK_PATH", "").strip() or "/telegram/webhook",
        webhook_secret=webhook_secret,
        cron_secret=os.getenv("CRON_SECRET", "").strip() or None,
        port=_int("PORT", 8080) or 8080,
        use_webhook=use_webhook,
        log_level=(os.getenv("LOG_LEVEL", "").strip() or "INFO").upper(),
    )
