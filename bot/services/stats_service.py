"""Статистика CRM: общая, по риелторам, за период."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import (
    ACTIVE_STATUSES,
    ARCHIVE_STATUSES,
    Deal,
    DealStatus,
    Lead,
    Property,
    User,
)
from bot.database.repositories import users as users_repo
from bot.services import report_service
from bot.utils.timeutils import Period


@dataclass
class Stats:
    period: Period
    active_properties: int = 0
    archived_properties: int = 0
    new_properties: int = 0
    leads: int = 0
    leads_taken: int = 0
    viewings: int = 0
    deals: int = 0
    commission: dict[str, Decimal] = field(default_factory=dict)
    deal_volume: dict[str, Decimal] = field(default_factory=dict)
    realtors: int = 0
    reports: int = 0


@dataclass
class RealtorRow:
    realtor: User
    deals: int
    commission: dict[str, Decimal]


async def _money_by_currency(session: AsyncSession, column, *conditions) -> dict[str, Decimal]:
    rows = (await session.execute(select(Deal.currency, func.sum(column)).where(*conditions).group_by(Deal.currency))).all()
    return {cur: Decimal(total or 0) for cur, total in rows if total}


async def collect(session: AsyncSession, period: Period, tz: ZoneInfo, realtor_id: int | None = None) -> Stats:
    start, end = period.utc_bounds(tz)
    s = Stats(period=period)

    prop_owner = [Property.responsible_realtor_id == realtor_id] if realtor_id is not None else []
    s.active_properties = int(
        await session.scalar(select(func.count(Property.id)).where(Property.status.in_(ACTIVE_STATUSES), *prop_owner)) or 0
    )
    s.archived_properties = int(
        await session.scalar(select(func.count(Property.id)).where(Property.status.in_(ARCHIVE_STATUSES), *prop_owner)) or 0
    )
    new_cond = [Property.created_at >= start, Property.created_at < end]
    if realtor_id is not None:
        new_cond.append(Property.created_by_id == realtor_id)
    s.new_properties = int(await session.scalar(select(func.count(Property.id)).where(*new_cond)) or 0)

    if realtor_id is None:
        s.leads = int(
            await session.scalar(select(func.count(Lead.id)).where(Lead.created_at >= start, Lead.created_at < end)) or 0
        )
    lead_owner = [Lead.assigned_realtor_id == realtor_id] if realtor_id is not None else []
    s.leads_taken = int(
        await session.scalar(
            select(func.count(Lead.id)).where(Lead.assigned_at >= start, Lead.assigned_at < end, *lead_owner)
        )
        or 0
    )
    if realtor_id is not None:
        s.leads = s.leads_taken
    s.viewings = int(
        await session.scalar(
            select(func.count(Lead.id)).where(Lead.viewing_done_at >= start, Lead.viewing_done_at < end, *lead_owner)
        )
        or 0
    )

    deal_cond = [Deal.status == DealStatus.CONFIRMED, Deal.deal_date >= period.start, Deal.deal_date <= period.end]
    if realtor_id is not None:
        deal_cond.append(Deal.realtor_id == realtor_id)
    s.deals = int(await session.scalar(select(func.count(Deal.id)).where(*deal_cond)) or 0)
    s.commission = await _money_by_currency(session, Deal.commission, *deal_cond)
    s.deal_volume = await _money_by_currency(session, Deal.deal_amount, *deal_cond)

    if realtor_id is None:
        s.realtors = await users_repo.count_realtors(session)
    else:
        s.reports = await report_service.count_reports(session, realtor_id, period.start, period.end)
    return s


async def per_realtor(session: AsyncSession, period: Period) -> list[RealtorRow]:
    realtors = await users_repo.list_staff(session, include_inactive=True, include_owner=True)
    rows = (
        await session.execute(
            select(Deal.realtor_id, Deal.currency, func.count(Deal.id), func.sum(Deal.commission))
            .where(Deal.status == DealStatus.CONFIRMED, Deal.deal_date >= period.start, Deal.deal_date <= period.end)
            .group_by(Deal.realtor_id, Deal.currency)
        )
    ).all()
    counts: dict[int, int] = {}
    money: dict[int, dict[str, Decimal]] = {}
    for rid, cur, cnt, total in rows:
        if rid is None:
            continue
        counts[rid] = counts.get(rid, 0) + int(cnt)
        money.setdefault(rid, {})[cur] = Decimal(total or 0)
    result = [RealtorRow(r, counts.get(r.id, 0), money.get(r.id, {})) for r in realtors if r.id in counts or r.role.value != "owner"]
    result.sort(key=lambda row: (-row.deals, row.realtor.display_name))
    return result


@dataclass
class RealtorTotals:
    properties: int
    leads: int
    deals: int
    commission: dict[str, Decimal]


async def realtor_totals(session: AsyncSession, realtor_id: int) -> RealtorTotals:
    props = int(await session.scalar(select(func.count(Property.id)).where(Property.responsible_realtor_id == realtor_id)) or 0)
    leads = int(await session.scalar(select(func.count(Lead.id)).where(Lead.assigned_realtor_id == realtor_id)) or 0)
    deal_cond = [Deal.status == DealStatus.CONFIRMED, Deal.realtor_id == realtor_id]
    deals = int(await session.scalar(select(func.count(Deal.id)).where(*deal_cond)) or 0)
    commission = await _money_by_currency(session, Deal.commission, *deal_cond)
    return RealtorTotals(props, leads, deals, commission)
