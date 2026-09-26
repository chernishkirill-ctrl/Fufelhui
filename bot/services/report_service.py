"""Ежедневные отчеты риелторов."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import DailyReport, User
from bot.database.repositories import users as users_repo

logger = logging.getLogger(__name__)

REPORT_FIELDS: list[tuple[str, str]] = [
    ("new_clients", "Сколько новых клиентов?"),
    ("calls", "Сколько звонков?"),
    ("leads_processed", "Сколько заявок обработано?"),
    ("viewings", "Сколько показов проведено?"),
    ("new_properties", "Сколько новых объектов добавлено?"),
    ("deals_closed", "Сколько сделок проведено?"),
]

REPORT_LABELS = {
    "new_clients": "Новые клиенты",
    "calls": "Звонки",
    "leads_processed": "Заявки",
    "viewings": "Показы",
    "new_properties": "Новые объекты",
    "deals_closed": "Сделки",
}


async def save_report(session: AsyncSession, realtor_id: int, report_date: date, values: dict, text: str | None) -> DailyReport:
    """Сохраняет отчет; повторная отправка за ту же дату обновляет его."""
    report = await session.scalar(
        select(DailyReport).where(DailyReport.realtor_id == realtor_id, DailyReport.report_date == report_date)
    )
    if report is None:
        report = DailyReport(realtor_id=realtor_id, report_date=report_date)
        session.add(report)
    for key, _ in REPORT_FIELDS:
        setattr(report, key, int(values.get(key, 0) or 0))
    report.report_text = text
    await session.flush()
    logger.info("Сохранен отчет realtor user_id=%s за %s", realtor_id, report_date)
    return report


async def get_report(session: AsyncSession, realtor_id: int, report_date: date) -> DailyReport | None:
    return await session.scalar(
        select(DailyReport).where(DailyReport.realtor_id == realtor_id, DailyReport.report_date == report_date)
    )


@dataclass
class ReportStatus:
    realtor: User
    report: DailyReport | None


async def reports_for_date(session: AsyncSession, report_date: date) -> list[ReportStatus]:
    realtors = await users_repo.list_staff(session)
    reports = (
        await session.scalars(select(DailyReport).where(DailyReport.report_date == report_date))
    ).unique().all()
    by_realtor = {r.realtor_id: r for r in reports}
    return [ReportStatus(r, by_realtor.get(r.id)) for r in realtors]


async def list_realtor_reports(session: AsyncSession, realtor_id: int, limit: int = 14) -> list[DailyReport]:
    rows = await session.scalars(
        select(DailyReport)
        .where(DailyReport.realtor_id == realtor_id)
        .order_by(DailyReport.report_date.desc())
        .limit(limit)
    )
    return list(rows.unique().all())


async def count_reports(session: AsyncSession, realtor_id: int, start: date, end: date) -> int:
    rows = await session.scalars(
        select(DailyReport.id).where(
            DailyReport.realtor_id == realtor_id, DailyReport.report_date >= start, DailyReport.report_date <= end
        )
    )
    return len(rows.all())
