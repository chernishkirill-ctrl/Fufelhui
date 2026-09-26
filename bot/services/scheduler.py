"""Планировщик ежедневных отчетов (APScheduler). Рассылка идемпотентна: повторный запуск за день ничего не шлет."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.services.context import AppContext
from bot.utils.timeutils import parse_hhmm

logger = logging.getLogger(__name__)

SUMMARY_DELAY_MIN = 90


class ReportScheduler:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.scheduler = AsyncIOScheduler(timezone=ctx.config.tz)

    def start(self, report_time: str) -> None:
        self.reschedule(report_time)
        self.scheduler.start()
        logger.info("Планировщик запущен: запрос отчетов в %s (%s)", report_time, self.ctx.config.timezone)

    def reschedule(self, report_time: str) -> None:
        from bot.handlers.reports import send_report_reminders, send_report_summary

        hm = parse_hhmm(report_time) or (22, 0)
        total = hm[0] * 60 + hm[1] + SUMMARY_DELAY_MIN
        sh, sm = (total // 60) % 24, total % 60
        tz = self.ctx.config.tz
        self.scheduler.add_job(
            send_report_reminders, CronTrigger(hour=hm[0], minute=hm[1], timezone=tz), args=[self.ctx],
            id="report_reminders", replace_existing=True, misfire_grace_time=3600, coalesce=True,
        )
        self.scheduler.add_job(
            send_report_summary, CronTrigger(hour=sh, minute=sm, timezone=tz), args=[self.ctx],
            id="report_summary", replace_existing=True, misfire_grace_time=3600, coalesce=True,
        )
        logger.info("Расписание отчетов: запрос %02d:%02d, сводка %02d:%02d", hm[0], hm[1], sh, sm)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
