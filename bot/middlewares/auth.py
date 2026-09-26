"""Middleware: сессия БД на каждый апдейт + определение пользователя и его роли по telegram_id."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User as TgUser
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

from bot.database.repositories import users as users_repo
from bot.services import settings_service
from bot.services.auth import Actor
from bot.services.context import AppContext

logger = logging.getLogger(__name__)

DB_DOWN_TEXT = "⚠️ База данных временно недоступна. Попробуйте через минуту — данные не потеряны."


class DbSessionMiddleware(BaseMiddleware):
    """Открывает транзакцию на апдейт: commit при успехе, rollback при ошибке."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Обычная переписка в рабочем чате (тема «Чат») не обрабатывается ботом и не трогает БД
        if isinstance(event, Message) and event.chat.type != "private" and not (event.text or "").startswith("/"):
            return None
        try:
            async with self.ctx.session_factory() as session:
                data["session"] = session
                data["ctx"] = self.ctx
                tg_user: TgUser | None = data.get("event_from_user")
                data["actor"] = await self._resolve_actor(session, tg_user)
                data["rs"] = await settings_service.load(session, self.ctx.config)
                try:
                    result = await handler(event, data)
                    await session.commit()
                    return result
                except Exception:
                    await session.rollback()
                    raise
        except (OperationalError, DBAPIError) as exc:
            logger.error("Ошибка БД: %s", type(exc).__name__)
            await self._notify_db_down(event)
            return None
        except SQLAlchemyError:
            logger.exception("Ошибка SQLAlchemy")
            await self._notify_db_down(event)
            return None

    async def _resolve_actor(self, session, tg_user: TgUser | None) -> Actor:
        if tg_user is None:
            return Actor(telegram_id=0, user=None, is_owner=False)
        if tg_user.id == self.ctx.config.owner_id:
            user = await users_repo.ensure_owner(session, tg_user.id, tg_user.username, tg_user.first_name)
            users_repo.touch_profile(user, tg_user.username, tg_user.first_name)
            return Actor(telegram_id=tg_user.id, user=user, is_owner=True)
        user = await users_repo.get_by_telegram_id(session, tg_user.id)
        if user is not None:
            users_repo.touch_profile(user, tg_user.username, tg_user.first_name)
        return Actor(telegram_id=tg_user.id, user=user, is_owner=False)

    @staticmethod
    async def _notify_db_down(event: TelegramObject) -> None:
        try:
            if isinstance(event, Message) and event.chat.type == "private":
                await event.answer(DB_DOWN_TEXT)
            elif isinstance(event, CallbackQuery):
                await event.answer(DB_DOWN_TEXT, show_alert=True)
        except Exception:  # noqa: BLE001
            pass
