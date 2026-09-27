"""Заявки клиентов и механизм «первый риелтор забрал клиента»."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import OPEN_LEAD_STATUSES, Lead, LeadStatus, Property, User, utcnow
from bot.services.property_service import PAGE_SIZE, add_history

logger = logging.getLogger(__name__)

LEAD_STATUS_LABELS = {
    LeadStatus.NEW: "🆕 Новая",
    LeadStatus.ASSIGNED: "✅ Принята",
    LeadStatus.IN_PROGRESS: "🔄 В работе",
    LeadStatus.VIEWING_SCHEDULED: "📅 Просмотр назначен",
    LeadStatus.VIEWING_DONE: "👀 Просмотр состоялся",
    LeadStatus.CLOSED: "🏁 Закрыта",
    LeadStatus.CANCELLED: "❌ Отменена",
}

# Разрешенные переходы для риелтора (владелец может ставить любой статус)
REALTOR_TRANSITIONS = {
    LeadStatus.ASSIGNED: [LeadStatus.IN_PROGRESS, LeadStatus.VIEWING_SCHEDULED, LeadStatus.CANCELLED],
    LeadStatus.IN_PROGRESS: [LeadStatus.VIEWING_SCHEDULED, LeadStatus.CLOSED, LeadStatus.CANCELLED],
    LeadStatus.VIEWING_SCHEDULED: [LeadStatus.VIEWING_DONE, LeadStatus.IN_PROGRESS, LeadStatus.CANCELLED],
    LeadStatus.VIEWING_DONE: [LeadStatus.VIEWING_SCHEDULED, LeadStatus.CLOSED, LeadStatus.CANCELLED],
    LeadStatus.CLOSED: [LeadStatus.IN_PROGRESS],
    LeadStatus.CANCELLED: [LeadStatus.IN_PROGRESS],
    LeadStatus.NEW: [],
}

MAX_OPEN_LEADS_PER_CLIENT = 5


class LeadError(Exception):
    """Текст ошибки показывается клиенту — поэтому на украинском."""


async def create_lead(
    session: AsyncSession,
    *,
    property_id: int | None,
    client_telegram_id: int | None,
    client_username: str | None,
    client_name: str | None,
    phone: str | None,
    preferred_time: str | None,
    comment: str | None,
) -> Lead:
    if client_telegram_id is not None:
        # Защита от спама и дублей
        dup = await session.scalar(
            select(Lead.id).where(
                Lead.client_telegram_id == client_telegram_id,
                Lead.property_id == property_id,
                Lead.status.in_(OPEN_LEAD_STATUSES),
            )
        )
        if dup:
            raise LeadError("У вас вже є активна заявка на цей об'єкт. Рієлтор незабаром зв'яжеться з вами.")
        recent = await session.scalar(
            select(func.count(Lead.id)).where(
                Lead.client_telegram_id == client_telegram_id,
                Lead.created_at >= utcnow() - timedelta(days=1),
            )
        )
        if (recent or 0) >= MAX_OPEN_LEADS_PER_CLIENT:
            raise LeadError("Забагато заявок за добу. Спробуйте пізніше.")
    lead = Lead(
        property_id=property_id,
        client_telegram_id=client_telegram_id,
        client_username=client_username,
        client_name=client_name,
        phone=phone,
        preferred_time=preferred_time,
        comment=comment,
        status=LeadStatus.NEW,
    )
    session.add(lead)
    await session.flush()
    if property_id:
        await add_history(session, property_id, "lead_created", None, "lead", None, lead.code)
    await session.refresh(lead)
    logger.info("Создана заявка %s по объекту id=%s", lead.code, property_id)
    return lead


@dataclass
class TakeResult:
    ok: bool
    lead: Lead | None
    reason: str = ""


async def take_lead(session: AsyncSession, lead_id: int, realtor: User) -> TakeResult:
    """Атомарно назначает заявку первому нажавшему риелтору.

    Один UPDATE ... WHERE assigned_realtor_id IS NULL AND status = 'new': база гарантирует,
    что при одновременных нажатиях строку обновит ровно одна транзакция (вторая после
    снятия блокировки перепроверит WHERE и получит rowcount = 0).
    """
    now = utcnow()
    result = await session.execute(
        update(Lead)
        .where(Lead.id == lead_id, Lead.assigned_realtor_id.is_(None), Lead.status == LeadStatus.NEW)
        .values(assigned_realtor_id=realtor.id, status=LeadStatus.ASSIGNED, assigned_at=now, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    lead = await session.get(Lead, lead_id, populate_existing=True)
    if lead is None:
        return TakeResult(False, None, "not_found")
    if result.rowcount != 1:
        return TakeResult(False, lead, "taken" if lead.assigned_realtor_id else "closed")
    if lead.property_id:
        await add_history(session, lead.property_id, "lead_taken", realtor.id, "lead", lead.code, realtor.display_name)
    logger.info("Заявка %s назначена риелтору user_id=%s", lead.code, realtor.id)
    return TakeResult(True, lead)


async def assign_lead(session: AsyncSession, lead: Lead, realtor: User, by_user_id: int | None) -> None:
    """Ручное назначение/переназначение владельцем."""
    old = lead.assigned_realtor_id
    lead.assigned_realtor_id = realtor.id
    lead.assigned_at = utcnow()
    if lead.status == LeadStatus.NEW:
        lead.status = LeadStatus.ASSIGNED
    if lead.property_id:
        await add_history(session, lead.property_id, "lead_reassigned", by_user_id, "lead", old, f"{lead.code} → {realtor.display_name}")
    await session.flush()
    await session.refresh(lead, ["assigned_realtor"])
    logger.info("Заявка %s вручную назначена user_id=%s", lead.code, realtor.id)


async def set_status(
    session: AsyncSession, lead: Lead, status: LeadStatus, by_user_id: int | None, viewing_at: str | None = None
) -> None:
    old = lead.status
    lead.status = status
    now = utcnow()
    if status == LeadStatus.VIEWING_SCHEDULED and viewing_at:
        lead.viewing_at = viewing_at
    if status == LeadStatus.VIEWING_DONE and lead.viewing_done_at is None:
        lead.viewing_done_at = now
    if status in (LeadStatus.CLOSED, LeadStatus.CANCELLED):
        lead.closed_at = now
    elif old in (LeadStatus.CLOSED, LeadStatus.CANCELLED):
        lead.closed_at = None
    if lead.property_id:
        await add_history(session, lead.property_id, "lead_status", by_user_id, lead.code, old, status)
    logger.info("Заявка %s: статус %s -> %s", lead.code, old.value, status.value)


async def get_lead(session: AsyncSession, lead_id: int) -> Lead | None:
    return await session.get(Lead, lead_id)


LEAD_FILTERS = {
    "open": OPEN_LEAD_STATUSES,
    "new": frozenset({LeadStatus.NEW}),
    "work": frozenset({LeadStatus.ASSIGNED, LeadStatus.IN_PROGRESS, LeadStatus.VIEWING_SCHEDULED, LeadStatus.VIEWING_DONE}),
    "done": frozenset({LeadStatus.CLOSED, LeadStatus.CANCELLED}),
    "all": frozenset(LeadStatus),
}


async def list_leads(
    session: AsyncSession, *, flt: str = "open", realtor_id: int | None = None, property_id: int | None = None, page: int = 0
) -> tuple[list[Lead], int]:
    conditions = [Lead.status.in_(LEAD_FILTERS.get(flt, OPEN_LEAD_STATUSES))]
    if realtor_id is not None:
        conditions.append(Lead.assigned_realtor_id == realtor_id)
    if property_id is not None:
        conditions.append(Lead.property_id == property_id)
    total = int(await session.scalar(select(func.count(Lead.id)).where(*conditions)) or 0)
    rows = (
        await session.scalars(
            select(Lead).where(*conditions).order_by(Lead.id.desc()).limit(PAGE_SIZE).offset(page * PAGE_SIZE)
        )
    ).unique().all()
    return list(rows), total


async def leads_for_deal(session: AsyncSession, property_id: int, realtor_id: int | None) -> list[Lead]:
    conditions = [Lead.property_id == property_id, Lead.assigned_realtor_id.is_not(None)]
    if realtor_id is not None:
        conditions.append(Lead.assigned_realtor_id == realtor_id)
    rows = await session.scalars(select(Lead).where(*conditions).order_by(Lead.id.desc()).limit(8))
    return list(rows.unique().all())


def lead_property_label(lead: Lead) -> str:
    prop: Property | None = lead.prop
    return prop.code if prop else "без объекта"
