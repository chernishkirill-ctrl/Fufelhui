from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Config


@dataclass
class AppContext:
    """Общие зависимости, доступные в хендлерах как `ctx`."""

    bot: Bot
    config: Config
    session_factory: async_sessionmaker[AsyncSession]
    bot_username: str = ""

    def deep_link(self, payload: str) -> str:
        return f"https://t.me/{self.bot_username}?start={payload}"

    def booking_link(self, property_id: int) -> str:
        """Ссылка «Записаться на просмотр».

        Если зарегистрирован Mini App — открывается компактное всплывающее окно с формой
        прямо поверх канала; иначе — переписка с ботом (анкета в чате).
        """
        short_name = self.config.webapp_short_name
        if short_name and self.bot_username:
            return f"https://t.me/{self.bot_username}/{short_name}?startapp=lead_{property_id}&mode=compact"
        return self.deep_link(f"lead_{property_id}")

    def public_url(self, path: str) -> str | None:
        base = self.config.webhook_base_url
        return f"{base}{path}" if base else None
