"""Безопасные обертки над Telegram API: ошибки логируются и не роняют процесс."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def safe_call(what: str, func: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T | None:
    """Вызывает метод Bot API. RetryAfter — ждем и повторяем один раз; сеть — одна повторная попытка."""
    for attempt in range(2):
        try:
            return await func(*args, **kwargs)
        except TelegramRetryAfter as exc:
            if attempt == 0 and exc.retry_after <= 30:
                await asyncio.sleep(exc.retry_after)
                continue
            logger.warning("Telegram API [%s]: flood control, retry_after=%s", what, exc.retry_after)
            return None
        except TelegramNetworkError as exc:
            if attempt == 0:
                await asyncio.sleep(1.5)
                continue
            logger.warning("Telegram API [%s]: сетевая ошибка: %s", what, exc)
            return None
        except TelegramAPIError as exc:
            msg = str(exc)
            if "message is not modified" in msg:
                return None
            logger.warning("Telegram API [%s]: %s", what, msg)
            return None
    return None


def private_link(chat_id: int | str, message_id: int) -> str:
    """Ссылка на сообщение в канале/супергруппе."""
    if isinstance(chat_id, str) and chat_id.startswith("@"):
        return f"https://t.me/{chat_id[1:]}/{message_id}"
    raw = str(chat_id)
    internal = raw[4:] if raw.startswith("-100") else raw.lstrip("-")
    return f"https://t.me/c/{internal}/{message_id}"


def topic_link(chat_id: int, thread_id: int | None, message_id: int) -> str:
    raw = str(chat_id)
    internal = raw[4:] if raw.startswith("-100") else raw.lstrip("-")
    if thread_id:
        return f"https://t.me/c/{internal}/{thread_id}/{message_id}"
    return f"https://t.me/c/{internal}/{message_id}"
