"""Ежедневные отчеты: заполнение риелтором, просмотр владельцем, рассылка по расписанию."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import DailyReport
from bot.database.repositories import settings as settings_repo
from bot.database.repositories import users as users_repo
from bot.handlers.states import ReportFill
from bot.keyboards.callbacks import FormCB, RealtorCB, ReportCB
from bot.keyboards.menus import btn, choices_kb, home_btn, skip_cancel_kb
from bot.services import report_service, settings_service
from bot.services.auth import Actor
from bot.services.context import AppContext
from bot.services.report_service import REPORT_FIELDS, REPORT_LABELS
from bot.utils.telegram import safe_call
from bot.utils.text import esc, parse_int
from bot.utils.timeutils import fmt_date, local_now
from bot.utils.ui import ack, deny, show

logger = logging.getLogger(__name__)
router = Router(name="reports")
router.message.filter(F.chat.type == "private")

REMINDER_TEXT = "🕙 <b>Время ежедневного отчета.</b> Расскажите, что было сделано сегодня."
NUMBER_OPTIONS = [(str(i), str(i)) for i in (0, 1, 2, 3, 5, 10)]


def report_date_for_now(ctx: AppContext) -> date:
    """После полуночи (до 05:00) отчет относится к предыдущему дню."""
    now = local_now(ctx.config.tz)
    return (now - timedelta(days=1)).date() if now.hour < 5 else now.date()


# ======================= заполнение (риелтор) =======================

async def start_report(event: Message | CallbackQuery, actor: Actor, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    if not actor.is_staff or actor.user is None:
        return await deny(event)
    await state.clear()
    day = report_date_for_now(ctx)
    existing = await report_service.get_report(session, actor.user_id, day)
    await state.set_state(ReportFill.number)
    await state.update_data(report={"day": day.isoformat(), "idx": 0, "values": {}})
    note = "\n\nℹ️ Отчет за этот день уже отправлен — новый заменит его." if existing else ""
    await show(event, f"📝 <b>Отчет за {fmt_date(day)}</b>{note}\n\n{_question(0)}", _number_kb(), new=isinstance(event, CallbackQuery))


def _question(idx: int) -> str:
    return f"{idx + 1}/{len(REPORT_FIELDS) + 1}. {REPORT_FIELDS[idx][1]}"


def _number_kb() -> InlineKeyboardMarkup:
    return choices_kb("rnum", NUMBER_OPTIONS, per_row=6)


async def _store_number(event, state: FSMContext, value: int) -> None:
    data = (await state.get_data()).get("report", {})
    idx = data.get("idx", 0)
    data.setdefault("values", {})[REPORT_FIELDS[idx][0]] = value
    idx += 1
    data["idx"] = idx
    await state.update_data(report=data)
    if idx < len(REPORT_FIELDS):
        await show(event, _question(idx), _number_kb(), new=isinstance(event, Message))
    else:
        await state.set_state(ReportFill.comment)
        await show(
            event,
            f"{len(REPORT_FIELDS) + 1}/{len(REPORT_FIELDS) + 1}. Комментарий / результат дня (что сделано, планы, проблемы):",
            skip_cancel_kb(),
            new=isinstance(event, Message),
        )


@router.callback_query(ReportFill.number, FormCB.filter(F.field == "rnum"))
async def report_number_btn(cq: CallbackQuery, callback_data: FormCB, state: FSMContext) -> None:
    await _store_number(cq, state, parse_int(callback_data.value) or 0)
    await ack(cq)


@router.message(ReportFill.number, F.text)
async def report_number(message: Message, state: FSMContext) -> None:
    value = parse_int(message.text)
    if value is None or not 0 <= value <= 10000:
        return await message.answer("Введите число (0, если ничего).", reply_markup=_number_kb())
    await _store_number(message, state, value)


async def _report_confirm(event, state: FSMContext) -> None:
    data = (await state.get_data()).get("report", {})
    await state.set_state(ReportFill.confirm)
    lines = [f"📝 <b>Отчет за {fmt_date(date.fromisoformat(data['day']))}</b>", ""]
    for key, _ in REPORT_FIELDS:
        lines.append(f"{REPORT_LABELS[key]}: {data['values'].get(key, 0)}")
    if data.get("text"):
        lines += ["", esc(data["text"])]
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("📨 Отправить отчет", FormCB(field="rsend"))], [btn("✖️ Отмена", FormCB(field="cancel"))]])
    await show(event, "\n".join(lines), kb, new=isinstance(event, Message))


@router.callback_query(ReportFill.comment, FormCB.filter(F.field == "skip"))
async def report_comment_skip(cq: CallbackQuery, state: FSMContext) -> None:
    await _report_confirm(cq, state)
    await ack(cq)


@router.message(ReportFill.comment, F.text)
async def report_comment(message: Message, state: FSMContext) -> None:
    data = (await state.get_data()).get("report", {})
    data["text"] = message.text.strip()[:3000]
    await state.update_data(report=data)
    await _report_confirm(message, state)


@router.callback_query(ReportFill.confirm, FormCB.filter(F.field == "rsend"))
async def report_send(cq: CallbackQuery, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    if not actor.is_staff or actor.user is None:
        await state.clear()
        return await deny(cq)
    data = (await state.get_data()).get("report", {})
    await state.clear()
    if not data.get("day"):
        return await ack(cq, "Данные устарели", alert=True)
    await report_service.save_report(session, actor.user_id, date.fromisoformat(data["day"]), data.get("values", {}), data.get("text"))
    await ack(cq, "Отчет сохранен")
    await show(cq, "✅ Спасибо! Отчет сохранен.", InlineKeyboardMarkup(inline_keyboard=[[home_btn()]]))


@router.callback_query(ReportCB.filter(F.action == "fill"))
async def cb_fill(cq: CallbackQuery, actor: Actor, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    await start_report(cq, actor, session, ctx, state)
    await ack(cq)


# ======================= просмотр (владелец) =======================

def report_text(report: DailyReport) -> str:
    lines = [f"📝 <b>{esc(report.realtor.display_name)}</b> — {fmt_date(report.report_date)}", ""]
    for key, _ in REPORT_FIELDS:
        lines.append(f"{REPORT_LABELS[key]}: {getattr(report, key)}")
    if report.report_text:
        lines += ["", esc(report.report_text)]
    return "\n".join(lines)


async def show_day(event, actor: Actor, session: AsyncSession, ctx: AppContext, day: date) -> None:
    if not actor.is_owner:
        return await deny(event)
    statuses = await report_service.reports_for_date(session, day)
    lines = [f"📑 <b>Отчеты за {fmt_date(day)}</b>", ""]
    b = InlineKeyboardBuilder()
    if not statuses:
        lines.append("Нет активных риелторов.")
    for st in statuses:
        if st.report:
            r = st.report
            lines.append(
                f"✅ {esc(st.realtor.display_name)}: клиенты {r.new_clients}, звонки {r.calls}, показы {r.viewings}, сделки {r.deals_closed}"
            )
            b.row(btn(f"📄 {st.realtor.display_name}", ReportCB(action="view", id=r.id)))
        else:
            lines.append(f"❌ {esc(st.realtor.display_name)}: Отчет не предоставлен")
    prev_day, next_day = day - timedelta(days=1), day + timedelta(days=1)
    nav = [btn("◀️", ReportCB(action="day", day=prev_day.strftime("%Y%m%d")))]
    if next_day <= local_now(ctx.config.tz).date():
        nav.append(btn("▶️", ReportCB(action="day", day=next_day.strftime("%Y%m%d"))))
    b.row(*nav)
    b.row(home_btn())
    await show(event, "\n".join(lines), b.as_markup())


@router.callback_query(ReportCB.filter(F.action == "day"))
async def cb_day(cq: CallbackQuery, callback_data: ReportCB, actor: Actor, session: AsyncSession, ctx: AppContext) -> None:
    try:
        day = datetime.strptime(callback_data.day, "%Y%m%d").date()
    except ValueError:
        return await ack(cq)
    await show_day(cq, actor, session, ctx, day)
    await ack(cq)


@router.callback_query(ReportCB.filter(F.action == "view"))
async def cb_view(cq: CallbackQuery, callback_data: ReportCB, actor: Actor, session: AsyncSession) -> None:
    report = await session.get(DailyReport, callback_data.id)
    if report is None:
        return await ack(cq, "Отчет не найден", alert=True)
    if not (actor.is_owner or report.realtor_id == actor.user_id):
        return await deny(cq)
    b = InlineKeyboardBuilder()
    if actor.is_owner:
        b.row(btn("⬅️ К дню", ReportCB(action="day", day=report.report_date.strftime("%Y%m%d"))),
              btn("👤 Риелтор", RealtorCB(action="open", id=report.realtor_id)))
    b.row(home_btn())
    await show(cq, report_text(report), b.as_markup())
    await ack(cq)


# ======================= расписание =======================

async def send_report_reminders(ctx: AppContext, force: bool = False) -> int:
    """Рассылка запросов отчета всем активным риелторам. Идемпотентна в пределах дня."""
    async with ctx.session_factory() as session:
        rs = await settings_service.load(session, ctx.config)
        if not rs.reports_enabled and not force:
            return 0
        today = local_now(ctx.config.tz).date()
        if not await settings_repo.claim_once(session, "report_reminder_date", today.isoformat()):
            logger.info("Запросы отчетов за %s уже отправлены", today)
            await session.rollback()
            return 0
        await session.commit()
        realtors = await users_repo.list_staff(session)
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("📝 Заполнить отчет", ReportCB(action="fill"))]])
    sent = 0
    for realtor in realtors:
        if await safe_call("report_reminder", ctx.bot.send_message, chat_id=realtor.telegram_id, text=REMINDER_TEXT, reply_markup=kb):
            sent += 1
    logger.info("Запросы отчетов отправлены: %s из %s", sent, len(realtors))
    return sent


async def send_report_summary(ctx: AppContext) -> None:
    """Сводка владельцу: кто сдал отчет, кто нет."""
    async with ctx.session_factory() as session:
        rs = await settings_service.load(session, ctx.config)
        if not (rs.reports_enabled and rs.report_summary):
            return
        day = report_date_for_now(ctx)
        if not await settings_repo.claim_once(session, "report_summary_date", day.isoformat()):
            await session.rollback()
            return
        await session.commit()
        statuses = await report_service.reports_for_date(session, day)
    if not statuses:
        return
    done = [s for s in statuses if s.report]
    missing = [s for s in statuses if not s.report]
    lines = [f"📑 <b>Отчеты за {fmt_date(day)}</b>: {len(done)}/{len(statuses)}"]
    for s in missing:
        lines.append(f"❌ {esc(s.realtor.display_name)}: Отчет не предоставлен")
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("Открыть отчеты", ReportCB(action="day", day=day.strftime("%Y%m%d")))]])
    await safe_call("report_summary", ctx.bot.send_message, chat_id=ctx.config.owner_id, text="\n".join(lines), reply_markup=kb)

