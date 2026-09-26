"""Помощники интерфейса: единый способ показать экран из сообщения или из нажатия кнопки."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup

logger = logging.getLogger(__name__)

ACCESS_DENIED = "Доступ запрещен."


async def show(
    event: Message | CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
    *,
    new: bool = False,
) -> Message | None:
    """Для callback — редактируем текущее сообщение (или шлем новое), для message — отвечаем."""
    if isinstance(event, CallbackQuery):
        msg = event.message
        if not new and isinstance(msg, Message) and msg.text is not None and (
            reply_markup is None or isinstance(reply_markup, InlineKeyboardMarkup)
        ):
            try:
                edited = await msg.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
                return edited if isinstance(edited, Message) else msg
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc):
                    return msg
                logger.debug("edit_text failed: %s", exc)
        if isinstance(msg, Message):
            return await msg.answer(text, reply_markup=reply_markup, disable_web_page_preview=True)
        return await event.bot.send_message(event.from_user.id, text, reply_markup=reply_markup, disable_web_page_preview=True)
    return await event.answer(text, reply_markup=reply_markup, disable_web_page_preview=True)


async def deny(event: Message | CallbackQuery) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer(ACCESS_DENIED, show_alert=True)
    else:
        await event.answer(ACCESS_DENIED)


async def ack(event: Message | CallbackQuery, text: str | None = None, alert: bool = False) -> None:
    if isinstance(event, CallbackQuery):
        try:
            await event.answer(text, show_alert=alert)
        except TelegramBadRequest:
            pass  # query устарел
