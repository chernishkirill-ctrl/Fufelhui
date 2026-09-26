"""Приём заявки клиента: сохранение в БД и отправка в рабочую группу (общая логика для бота и Mini App)."""
from __future__ import annotations

import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Lead, Property
from bot.keyboards.callbacks import LeadCB
from bot.services import cards, lead_service, workchat_service
from bot.services.context import AppContext
from bot.services.settings_service import RuntimeSettings
from bot.utils.text import mask_phone

logger = logging.getLogger(__name__)


async def submit_lead(
    ctx: AppContext,
    session: AsyncSession,
    rs: RuntimeSettings,
    prop: Property,
    *,
    client_telegram_id: int | None,
    client_username: str | None,
    client_name: str | None,
    phone: str | None,
    preferred_time: str | None = None,
    comment: str | None = None,
    source: str = "bot",
) -> Lead:
    """Создает заявку (с антиспамом), фиксирует ее в БД и публикует в рабочей группе.

    LeadError пробрасывается вызывающему — там показывается понятное сообщение клиенту.
    """
    lead = await lead_service.create_lead(
        session,
        property_id=prop.id,
        client_telegram_id=client_telegram_id,
        client_username=client_username,
        client_name=client_name,
        phone=phone,
        preferred_time=preferred_time,
        comment=comment,
    )
    # Сохраняем заявку до отправки в Telegram, чтобы она не потерялась при сбое сети
    await session.commit()
    await session.refresh(lead)
    logger.info("Заявка %s (%s): тел=%s", lead.code, source, mask_phone(lead.phone))

    posted = await workchat_service.post_lead(ctx, rs, lead)
    if rs.notify_owner_leads or not posted:
        await workchat_service.notify_owner(
            ctx,
            "📋 <b>Новая заявка</b>\n\n" + cards.lead_private_card(lead, ctx.config.tz)
            + ("" if posted else "\n\n⚠️ Не удалось отправить в рабочий чат — проверьте настройки."),
            InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Открыть заявку", callback_data=LeadCB(action="open", id=lead.id).pack())]]
            ),
        )
    await session.commit()
    return lead
