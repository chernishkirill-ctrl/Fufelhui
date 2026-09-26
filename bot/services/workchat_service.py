"""Интеграция с рабочим чатом (супергруппа с Topics): карточки в «База», «Архив», «Заявки», «Сделки».

Тема «Чат» остается для живого общения — бот туда ничего не пишет.
"""
from __future__ import annotations

import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import Deal, Lead, Property
from bot.keyboards.callbacks import LeadCB
from bot.services import cards
from bot.services.context import AppContext
from bot.services.settings_service import RuntimeSettings
from bot.utils.telegram import private_link, safe_call

logger = logging.getLogger(__name__)


def public_post_url(ctx: AppContext, prop: Property) -> str | None:
    if prop.public_message_id and ctx.config.public_channel_id:
        return private_link(ctx.config.public_channel_id, prop.public_message_id)
    return None


def base_card_keyboard(ctx: AppContext, prop: Property) -> InlineKeyboardMarkup:
    # Кнопки ведут в личный чат с ботом: там права проверяются по telegram_id
    rows = [[InlineKeyboardButton(text="Открыть объект", url=ctx.deep_link(f"obj_{prop.id}"))]]
    url = public_post_url(ctx, prop)
    if url:
        rows[0].append(InlineKeyboardButton(text="Открыть публикацию", url=url))
    if not prop.is_archived:
        rows.append(
            [
                InlineKeyboardButton(text="Изменить", url=ctx.deep_link(f"edit_{prop.id}")),
                InlineKeyboardButton(text="Архив", url=ctx.deep_link(f"arch_{prop.id}")),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chat(rs: RuntimeSettings) -> int | None:
    return rs.work_chat_id


async def post_base_card(ctx: AppContext, rs: RuntimeSettings, prop: Property) -> None:
    chat = _chat(rs)
    if not chat or not rs.post_base_cards:
        return
    msg = await safe_call(
        "base_card",
        ctx.bot.send_message,
        chat_id=chat,
        message_thread_id=rs.topics.get("base"),
        text=cards.base_card(prop, public_post_url(ctx, prop)),
        reply_markup=base_card_keyboard(ctx, prop),
        disable_web_page_preview=True,
    )
    if msg:
        prop.base_message_id = msg.message_id


async def refresh_base_card(ctx: AppContext, rs: RuntimeSettings, prop: Property) -> None:
    chat = _chat(rs)
    if not chat:
        return
    if not prop.base_message_id:
        if not prop.is_archived:
            await post_base_card(ctx, rs, prop)
        return
    text = cards.base_card(prop, public_post_url(ctx, prop))
    if prop.is_archived:
        text = "📦 <i>Перенесен в архив</i>\n\n" + text
    await safe_call(
        "base_card_edit",
        ctx.bot.edit_message_text,
        chat_id=chat,
        message_id=prop.base_message_id,
        text=text,
        reply_markup=base_card_keyboard(ctx, prop),
        disable_web_page_preview=True,
    )


async def post_archive_card(ctx: AppContext, rs: RuntimeSettings, prop: Property) -> None:
    chat = _chat(rs)
    if not chat:
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть объект", url=ctx.deep_link(f"obj_{prop.id}"))]]
    )
    msg = await safe_call(
        "archive_card",
        ctx.bot.send_message,
        chat_id=chat,
        message_thread_id=rs.topics.get("archive"),
        text=cards.archive_card(prop, ctx.config.tz),
        reply_markup=kb,
    )
    if msg:
        prop.archive_message_id = msg.message_id


async def mark_unarchived(ctx: AppContext, rs: RuntimeSettings, prop: Property) -> None:
    chat = _chat(rs)
    if chat and prop.archive_message_id:
        await safe_call(
            "archive_card_edit",
            ctx.bot.edit_message_text,
            chat_id=chat,
            message_id=prop.archive_message_id,
            text=f"↩️ <i>{prop.code} возвращен из архива</i>",
        )
        prop.archive_message_id = None


async def delete_cards(ctx: AppContext, rs: RuntimeSettings, prop: Property) -> None:
    chat = _chat(rs)
    if not chat:
        return
    for mid in (prop.base_message_id, prop.archive_message_id):
        if mid:
            await safe_call("card_delete", ctx.bot.delete_message, chat_id=chat, message_id=mid)


def lead_take_keyboard(lead: Lead) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🙋 Взять клиента", callback_data=LeadCB(action="take", id=lead.id).pack())]]
    )


async def post_lead(ctx: AppContext, rs: RuntimeSettings, lead: Lead) -> bool:
    chat = _chat(rs)
    if not chat:
        return False
    topic = rs.topics.get("leads") or rs.topics.get("base")
    msg = await safe_call(
        "lead_card",
        ctx.bot.send_message,
        chat_id=chat,
        message_thread_id=topic,
        text=cards.lead_group_card(lead),
        reply_markup=lead_take_keyboard(lead),
    )
    if msg:
        lead.work_message_id = msg.message_id
        return True
    return False


async def mark_lead_taken(ctx: AppContext, rs: RuntimeSettings, lead: Lead) -> None:
    chat = _chat(rs)
    if chat and lead.work_message_id:
        await safe_call(
            "lead_card_edit",
            ctx.bot.edit_message_text,
            chat_id=chat,
            message_id=lead.work_message_id,
            text=cards.lead_taken_group_card(lead),
            reply_markup=None,
        )


async def post_deal(ctx: AppContext, rs: RuntimeSettings, deal: Deal) -> None:
    chat = _chat(rs)
    if not chat:
        return
    msg = await safe_call(
        "deal_card",
        ctx.bot.send_message,
        chat_id=chat,
        message_thread_id=rs.topics.get("deals"),
        text=cards.deal_group_card(deal),
    )
    if msg:
        deal.deals_message_id = msg.message_id


async def mark_deal_cancelled(ctx: AppContext, rs: RuntimeSettings, deal: Deal) -> None:
    chat = _chat(rs)
    if chat and deal.deals_message_id:
        await safe_call(
            "deal_card_edit",
            ctx.bot.edit_message_text,
            chat_id=chat,
            message_id=deal.deals_message_id,
            text=f"<s>Сделка {deal.code}</s>\n❌ Сделка отменена администратором.",
        )


async def notify_owner(ctx: AppContext, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    await safe_call(
        "notify_owner",
        ctx.bot.send_message,
        chat_id=ctx.config.owner_id,
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
