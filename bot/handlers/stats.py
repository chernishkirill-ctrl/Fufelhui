"""Статистика: общая (владелец) и личная (риелтор)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.states import StatsCustom
from bot.keyboards.callbacks import DealCB, StatsCB
from bot.keyboards.menus import btn, cancel_kb, home_btn
from bot.services import stats_service
from bot.services.auth import Actor
from bot.services.context import AppContext
from bot.services.settings_service import RuntimeSettings
from bot.utils.text import esc, fmt_money_map
from bot.utils.timeutils import Period, fmt_date, make_period, parse_custom_period
from bot.utils.ui import ack, deny, show

router = Router(name="stats")
router.message.filter(F.chat.type == "private")

PERIOD_BUTTONS = [("today", "Сегодня"), ("yesterday", "Вчера"), ("week", "7 дней"), ("month", "Месяц"), ("all", "Всё время")]


def period_kb(scope: str, current: str, extra: list | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, title in PERIOD_BUTTONS:
        b.add(btn(("• " if key == current else "") + title, StatsCB(period=key, scope=scope)))
    b.add(btn(("• " if current == "custom" else "") + "📅 Период", StatsCB(period="custom", scope=scope)))
    b.adjust(3)
    for row in extra or []:
        b.row(*row)
    b.row(home_btn())
    return b.as_markup()


async def owner_stats_text(session: AsyncSession, ctx: AppContext, period: Period) -> str:
    s = await stats_service.collect(session, period, ctx.config.tz)
    rows = await stats_service.per_realtor(session, period)
    lines = [
        f"📊 <b>Статистика: {esc(period.title)}</b>",
        f"<i>{fmt_date(period.start)} — {fmt_date(period.end)}</i>" if period.key != "all" else "",
        "",
        "<b>Сейчас в базе</b>",
        f"🏠 Активных объектов: {s.active_properties}",
        f"📦 В архиве: {s.archived_properties}",
        f"👥 Риелторов: {s.realtors}",
        "",
        "<b>За период</b>",
        f"➕ Новых объектов: {s.new_properties}",
        f"📋 Заявок: {s.leads} (принято: {s.leads_taken})",
        f"👀 Просмотров: {s.viewings}",
        f"🤝 Сделок: {s.deals}",
        f"💵 Объем сделок: {fmt_money_map(s.deal_volume)}",
        f"💰 Комиссия: <b>{fmt_money_map(s.commission)}</b>",
    ]
    if rows:
        lines += ["", "<b>Комиссии по риелторам</b>"]
        for row in rows:
            lines.append(f"• {esc(row.realtor.display_name)}: {row.deals} сд. — {fmt_money_map(row.commission)}")
    return "\n".join(line for line in lines if line is not None)


async def realtor_stats_text(session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, actor: Actor, period: Period) -> str:
    # realtor_id берется ТОЛЬКО из авторизованного пользователя — чужую статистику получить нельзя
    s = await stats_service.collect(session, period, ctx.config.tz, realtor_id=actor.user_id)
    lines = [
        f"📊 <b>Моя статистика: {esc(period.title)}</b>",
        "",
        f"🏠 Мои активные объекты: {s.active_properties} · в архиве: {s.archived_properties}",
        f"➕ Добавлено объектов: {s.new_properties}",
        f"📋 Взято заявок: {s.leads_taken}",
        f"👀 Просмотров: {s.viewings}",
        f"🤝 Сделок: {s.deals}",
        f"💰 Комиссия: <b>{fmt_money_map(s.commission)}</b>",
        f"📝 Отчетов сдано: {s.reports}",
    ]
    if rs.realtors_see_leaderboard:
        rows = await stats_service.per_realtor(session, period)
        if rows:
            lines += ["", "<b>🏆 Рейтинг по сделкам</b>"]
            for i, row in enumerate(rows[:10], 1):
                lines.append(f"{i}. {esc(row.realtor.display_name)} — {row.deals}")
    return "\n".join(lines)


async def show_stats(event, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, period: Period) -> None:
    if not actor.is_staff:
        return await deny(event)
    if actor.is_owner:
        text = await owner_stats_text(session, ctx, period)
        await show(event, text, period_kb("all", period.key))
    else:
        text = await realtor_stats_text(session, ctx, rs, actor, period)
        extra = [[btn("🤝 Мои сделки", DealCB(action="list"))]]
        await show(event, text, period_kb("me", period.key, extra))


@router.callback_query(StatsCB.filter())
async def cb_stats(cq: CallbackQuery, callback_data: StatsCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    if not actor.is_staff:
        return await deny(cq)
    if callback_data.period == "custom":
        await state.set_state(StatsCustom.period)
        await show(cq, "📅 Введите период в формате <code>01.09.2026-15.09.2026</code>:", cancel_kb())
        return await ack(cq)
    try:
        period = make_period(callback_data.period, ctx.config.tz)
    except ValueError:
        return await ack(cq)
    await show_stats(cq, actor, session, ctx, rs, period)
    await ack(cq)


@router.message(StatsCustom.period, F.text)
async def msg_custom_period(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    period = parse_custom_period(message.text, ctx.config.tz)
    if period is None:
        return await message.answer("Не понял период. Пример: 01.09.2026-15.09.2026", reply_markup=cancel_kb())
    await state.clear()
    await show_stats(message, actor, session, ctx, rs, period)
