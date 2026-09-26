"""Фильтры ролей. Используются для маршрутизации; сами обработчики дополнительно проверяют права к объекту."""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from bot.services.auth import Actor


class IsOwner(BaseFilter):
    async def __call__(self, event: TelegramObject, actor: Actor) -> bool:
        return actor.is_owner


class IsStaff(BaseFilter):
    """Владелец или активный риелтор."""

    async def __call__(self, event: TelegramObject, actor: Actor) -> bool:
        return actor.is_staff


class IsRealtor(BaseFilter):
    async def __call__(self, event: TelegramObject, actor: Actor) -> bool:
        return actor.is_realtor
