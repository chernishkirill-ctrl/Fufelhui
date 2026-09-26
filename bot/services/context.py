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
