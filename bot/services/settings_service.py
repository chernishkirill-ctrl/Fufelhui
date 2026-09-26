"""Runtime-настройки CRM (переключатели в разделе ⚙️) + разрешение ID тем (env имеет приоритет)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Config
from bot.database.repositories import settings as settings_repo

# key -> (заголовок, значение по умолчанию)
TOGGLES: dict[str, tuple[str, bool]] = {
    "notify_owner_leads": ("Уведомлять меня о новых заявках", True),
    "notify_owner_deals": ("Уведомлять меня о сделках", True),
    "notify_owner_properties": ("Уведомлять меня о новых объектах", False),
    "post_base_cards": ("Карточки объектов в тему «База»", True),
    "auto_publish": ("Автопубликация новых объектов в канал", False),
    "realtor_can_publish": ("Риелторы могут публиковать свои объекты", True),
    "unpublish_on_archive": ("Снимать публикацию при архивации", True),
    "publish_album": ("Публиковать альбомом (до 10 фото)", False),
    "create_telegraph": ("Создавать Telegraph-страницу объекта", True),
    "reports_enabled": ("Ежедневные запросы отчетов", True),
    "report_summary": ("Сводка по отчетам мне после дедлайна", True),
    "realtors_see_leaderboard": ("Риелторы видят общий рейтинг сделок", False),
}

TOPIC_KEYS = {
    "base": "BASE_TOPIC_ID",
    "archive": "ARCHIVE_TOPIC_ID",
    "deals": "DEALS_TOPIC_ID",
    "chat": "CHAT_TOPIC_ID",
    "leads": "LEADS_TOPIC_ID",
}

TOPIC_TITLES = {
    "chat": "💬 Чат",
    "base": "🏠 База",
    "archive": "📦 Архив",
    "deals": "🤝 Сделки",
    "leads": "📋 Заявки",
}


@dataclass
class RuntimeSettings:
    toggles: dict[str, bool]
    report_time: str
    topics: dict[str, int | None]
    work_chat_id: int | None

    def __getattr__(self, item: str) -> bool:
        toggles = self.__dict__.get("toggles", {})
        if item in toggles:
            return toggles[item]
        raise AttributeError(item)


def _to_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw == "1"


def _to_int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


async def load(session: AsyncSession, config: Config) -> RuntimeSettings:
    values = await settings_repo.get_all(session)
    toggles = {k: _to_bool(values.get(f"toggle:{k}"), default) for k, (_, default) in TOGGLES.items()}
    env_topics = {
        "base": config.base_topic_id,
        "archive": config.archive_topic_id,
        "deals": config.deals_topic_id,
        "chat": config.chat_topic_id,
        "leads": config.leads_topic_id,
    }
    topics = {k: env_topics[k] if env_topics[k] is not None else _to_int(values.get(f"topic:{k}")) for k in env_topics}
    work_chat_id = config.work_chat_id if config.work_chat_id is not None else _to_int(values.get("work_chat_id"))
    return RuntimeSettings(
        toggles=toggles,
        report_time=values.get("report_time") or config.report_time,
        topics=topics,
        work_chat_id=work_chat_id,
    )


async def set_toggle(session: AsyncSession, key: str, value: bool) -> None:
    if key not in TOGGLES:
        raise KeyError(key)
    await settings_repo.set_value(session, f"toggle:{key}", "1" if value else "0")


async def set_report_time(session: AsyncSession, hhmm: str) -> None:
    await settings_repo.set_value(session, "report_time", hhmm)


async def set_topic(session: AsyncSession, key: str, thread_id: int) -> None:
    await settings_repo.set_value(session, f"topic:{key}", str(thread_id))


async def set_work_chat(session: AsyncSession, chat_id: int) -> None:
    await settings_repo.set_value(session, "work_chat_id", str(chat_id))
