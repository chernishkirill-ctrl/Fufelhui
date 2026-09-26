"""Сценарии над объектом, объединяющие БД и Telegram (рабочий чат, канал, уведомления)."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import ARCHIVE_STATUSES, Property, PropertyStatus
from bot.services import cards, property_service, publication_service, workchat_service
from bot.services.auth import Actor, can_publish_property
from bot.services.context import AppContext
from bot.services.settings_service import RuntimeSettings
from bot.utils.text import esc

logger = logging.getLogger(__name__)


async def after_created(ctx: AppContext, session: AsyncSession, rs: RuntimeSettings, actor: Actor, prop: Property) -> str | None:
    """Карточка в «База», уведомление владельцу, автопубликация. Возвращает ошибку публикации, если была."""
    await workchat_service.post_base_card(ctx, rs, prop)
    if not actor.is_owner and rs.notify_owner_properties:
        await workchat_service.notify_owner(
            ctx, f"➕ {esc(actor.user.display_name if actor.user else '')} добавил объект\n{esc(cards.short_line(prop))}"
        )
    error = None
    if rs.auto_publish and ctx.config.public_channel_id and can_publish_property(actor, prop, rs.realtor_can_publish):
        try:
            await publication_service.publish(ctx, session, rs, prop, actor.user_id)
            await workchat_service.refresh_base_card(ctx, rs, prop)
        except publication_service.PublicationError as exc:
            error = str(exc)
    return error


async def apply_status(
    ctx: AppContext, session: AsyncSession, rs: RuntimeSettings, actor: Actor, prop: Property, status: PropertyStatus
) -> None:
    """Меняет статус и выполняет побочные эффекты архива/возврата."""
    was_archived = prop.is_archived
    if status == PropertyStatus.PUBLISHED and not prop.public_message_id:
        # «Опубликован» ставится только реальной публикацией
        await publication_service.publish(ctx, session, rs, prop, actor.user_id)
        await workchat_service.refresh_base_card(ctx, rs, prop)
        return
    await property_service.change_status(session, prop, status, actor.user_id)
    if status in ARCHIVE_STATUSES and not was_archived:
        if prop.public_message_id and rs.unpublish_on_archive:
            await publication_service.unpublish(ctx, session, prop, actor.user_id, record=True)
            prop.status = status  # unpublish мог вернуть статус в active
        await workchat_service.post_archive_card(ctx, rs, prop)
    elif was_archived and status not in ARCHIVE_STATUSES:
        await workchat_service.mark_unarchived(ctx, rs, prop)
    elif status in (PropertyStatus.ACTIVE, PropertyStatus.DRAFT) and prop.public_message_id:
        # Ручной перевод в active/draft означает «снять с публикации»; reserved оставляет пост в канале
        await publication_service.unpublish(ctx, session, prop, actor.user_id, record=True)
    await session.flush()
    await workchat_service.refresh_base_card(ctx, rs, prop)
