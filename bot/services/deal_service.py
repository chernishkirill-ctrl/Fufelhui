"""Фиксация и учет сделок."""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import (
    ARCHIVE_STATUSES,
    Deal,
    DealStatus,
    DealType,
    Lead,
    LeadStatus,
    Property,
    PropertyStatus,
    utcnow,
)
from bot.services import lead_service, property_service
from bot.services.property_service import PAGE_SIZE, add_history

logger = logging.getLogger(__name__)

DEAL_TYPE_LABELS = {DealType.SALE: "продажа", DealType.RENT: "аренда", DealType.OTHER: "другое"}
DEAL_STATUS_LABELS = {DealStatus.CONFIRMED: "✅ Подтверждена", DealStatus.CANCELLED: "❌ Отменена"}

# Какой статус получает объект после сделки
DEAL_PROPERTY_STATUS = {DealType.SALE: PropertyStatus.SOLD, DealType.RENT: PropertyStatus.RENTED}


class DealError(Exception):
    pass


async def create_deal(
    session: AsyncSession,
    *,
    prop: Property,
    realtor_id: int,
    deal_type: DealType,
    amount: Decimal,
    commission: Decimal,
    currency: str,
    deal_date: date,
    comment: str | None,
    lead_id: int | None,
    created_by_id: int | None,
) -> tuple[Deal, PropertyStatus | None]:
    """Создает сделку, переводит объект в статус sold/rented, закрывает связанную заявку.

    Возвращает (сделка, прежний статус объекта или None, если статус не менялся).
    """
    if amount < 0 or commission < 0:
        raise DealError("Суммы не могут быть отрицательными")
    deal = Deal(
        property_id=prop.id,
        realtor_id=realtor_id,
        client_id=lead_id,
        deal_type=deal_type,
        deal_amount=amount,
        commission=commission,
        currency=currency,
        deal_date=deal_date,
        comment=comment,
        status=DealStatus.CONFIRMED,
        created_by_id=created_by_id,
    )
    session.add(deal)
    await session.flush()
    await add_history(session, prop.id, "deal", created_by_id, "deal", None, f"{deal.code}: {DEAL_TYPE_LABELS[deal_type]}")

    old_status: PropertyStatus | None = None
    target = DEAL_PROPERTY_STATUS.get(deal_type)
    if target and prop.status != target:
        old_status = await property_service.change_status(session, prop, target, created_by_id)

    if lead_id:
        lead = await session.get(Lead, lead_id)
        if lead and lead.status not in (LeadStatus.CLOSED, LeadStatus.CANCELLED):
            await lead_service.set_status(session, lead, LeadStatus.CLOSED, created_by_id)
    await session.flush()
    await session.refresh(deal)
    logger.info("Зафиксирована сделка %s по объекту %s (realtor user_id=%s)", deal.code, prop.code, realtor_id)
    return deal, old_status


async def cancel_deal(session: AsyncSession, deal: Deal, by_user_id: int | None, restore_property: bool) -> None:
    if deal.status == DealStatus.CANCELLED:
        raise DealError("Сделка уже отменена")
    deal.status = DealStatus.CANCELLED
    deal.updated_at = utcnow()
    if deal.property_id:
        await add_history(session, deal.property_id, "deal_cancelled", by_user_id, "deal", deal.code, None)
        if restore_property:
            prop = await session.get(Property, deal.property_id)
            if prop and prop.status in ARCHIVE_STATUSES:
                await property_service.change_status(session, prop, PropertyStatus.ACTIVE, by_user_id)
    logger.info("Сделка %s отменена", deal.code)


DEAL_EDIT_FIELDS = {
    "amount": "Сумма сделки",
    "commission": "Комиссия",
    "comment": "Комментарий",
    "date": "Дата",
}


async def get_deal(session: AsyncSession, deal_id: int) -> Deal | None:
    return await session.get(Deal, deal_id)


async def list_deals(
    session: AsyncSession, *, realtor_id: int | None = None, page: int = 0, include_cancelled: bool = True
) -> tuple[list[Deal], int]:
    conditions = []
    if realtor_id is not None:
        conditions.append(Deal.realtor_id == realtor_id)
    if not include_cancelled:
        conditions.append(Deal.status == DealStatus.CONFIRMED)
    total = int(await session.scalar(select(func.count(Deal.id)).where(*conditions)) or 0)
    rows = (
        await session.scalars(
            select(Deal).where(*conditions).order_by(Deal.deal_date.desc(), Deal.id.desc()).limit(PAGE_SIZE).offset(page * PAGE_SIZE)
        )
    ).unique().all()
    return list(rows), total
