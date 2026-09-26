"""Личный пульт владельца. Доступ — только для OWNER_ID из окружения (фильтр IsOwner на всем роутере)."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import UserStatus
from bot.database.repositories import settings as settings_repo
from bot.database.repositories import users as users_repo
from bot.handlers import deals as deals_h
from bot.handlers import leads as leads_h
from bot.handlers import properties as props_h
from bot.handlers import reports as reports_h
from bot.handlers import stats as stats_h
from bot.handlers.states import RealtorAdd, SettingsEdit
from bot.keyboards import menus
from bot.keyboards.callbacks import DealCB, PropCB, RealtorCB, ReportCB, SettingsCB
from bot.keyboards.menus import btn, cancel_kb, home_btn, pager_row
from bot.middlewares.filters import IsOwner
from bot.services import cards, deal_service, property_service, report_service, settings_service, stats_service, user_service
from bot.services.auth import Actor
from bot.services.context import AppContext
from bot.services.settings_service import TOGGLES, TOPIC_TITLES, RuntimeSettings
from bot.utils.telegram import safe_call
from bot.utils.text import esc, fmt_money_map, parse_int
from bot.utils.timeutils import fmt_date, fmt_dt, local_today, make_period, parse_hhmm
from bot.utils.ui import ack, show

logger = logging.getLogger(__name__)
router = Router(name="owner")
router.message.filter(F.chat.type == "private", IsOwner())
router.callback_query.filter(IsOwner())


# ======================= главное меню =======================

@router.message(StateFilter("*"), F.text == menus.OWNER_STATS)
async def menu_stats(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    await state.clear()
    await stats_h.show_stats(message, actor, session, ctx, rs, make_period("today", ctx.config.tz))


@router.message(StateFilter("*"), F.text == menus.OWNER_PROPS)
async def menu_props(message: Message, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await props_h.show_properties_menu(message, actor, session)


@router.message(StateFilter("*"), F.text == menus.OWNER_REALTORS)
async def menu_realtors(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await show_realtors(message, session)


@router.message(StateFilter("*"), F.text == menus.OWNER_LEADS)
async def menu_leads(message: Message, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await leads_h.show_leads_menu(message, actor, session)


@router.message(StateFilter("*"), F.text == menus.OWNER_DEALS)
async def menu_deals(message: Message, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await deals_h.show_deals_menu(message, actor, session)


@router.message(StateFilter("*"), F.text == menus.OWNER_REPORTS)
async def menu_reports(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    await reports_h.show_day(message, actor, session, ctx, local_today(ctx.config.tz))


@router.message(StateFilter("*"), F.text == menus.OWNER_SETTINGS)
async def menu_settings(message: Message, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    await state.clear()
    await show_settings(message, ctx, rs)


# ======================= риелторы =======================

async def show_realtors(event, session: AsyncSession, page: int = 0) -> None:
    staff = await users_repo.list_staff(session, include_inactive=True)
    b = InlineKeyboardBuilder()
    b.row(btn("➕ Добавить по Telegram ID", RealtorCB(action="add")), btn("🔗 Ссылка-приглашение", RealtorCB(action="invite")))
    size = 10
    for user in staff[page * size:(page + 1) * size]:
        mark = "🟢" if user.status == UserStatus.ACTIVE else "⛔"
        b.row(btn(f"{mark} {user.display_name}"[:60], RealtorCB(action="open", id=user.id)))
    nav = pager_row(lambda p: RealtorCB(action="list", page=p), page, len(staff), size)
    if nav:
        b.row(*nav)
    b.row(home_btn())
    active = sum(1 for u in staff if u.status == UserStatus.ACTIVE)
    await show(event, f"👥 <b>Риелторы</b>: активных {active}, всего {len(staff)}", b.as_markup())


@router.callback_query(RealtorCB.filter(F.action == "list"))
async def cb_list(cq: CallbackQuery, callback_data: RealtorCB, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await show_realtors(cq, session, callback_data.page)
    await ack(cq)


@router.callback_query(RealtorCB.filter(F.action == "add"))
async def cb_add(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RealtorAdd.telegram_id)
    await show(
        cq,
        "➕ Отправьте <b>Telegram ID</b> риелтора (число).\n\n"
        "Риелтор может узнать свой ID командой /id в этом боте. "
        "Либо используйте «Ссылка-приглашение» — это проще.",
        cancel_kb(),
    )
    await ack(cq)


@router.message(RealtorAdd.telegram_id, F.text)
async def msg_add(message: Message, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    tg_id = parse_int(message.text)
    if tg_id is None:
        return await message.answer("Нужно число — Telegram ID.", reply_markup=cancel_kb())
    try:
        user, created = await user_service.add_realtor(session, tg_id, ctx.config.owner_id)
    except user_service.UserError as exc:
        return await message.answer(f"⚠️ {esc(exc)}", reply_markup=cancel_kb())
    await state.clear()
    await safe_call(
        "realtor_welcome", ctx.bot.send_message, chat_id=tg_id,
        text="👋 Вам открыт доступ к CRM агентства. Нажмите /start.",
    )
    await message.answer(
        f"✅ Риелтор {'добавлен' if created else 'восстановлен'} (ID {tg_id}).\n"
        "Если бот не смог написать — попросите риелтора открыть бота и нажать /start.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("Открыть карточку", RealtorCB(action="open", id=user.id))]]),
    )


@router.callback_query(RealtorCB.filter(F.action == "invite"))
async def cb_invite(cq: CallbackQuery, actor: Actor, session: AsyncSession, ctx: AppContext) -> None:
    invite = await user_service.create_invite(session, actor.user_id)
    link = ctx.deep_link(f"inv_{invite.token}")
    await show(
        cq,
        "🔗 <b>Одноразовая ссылка-приглашение</b> (действует 48 часов, только для роли «риелтор»):\n\n"
        f"<code>{link}</code>\n\nПерешлите ее сотруднику. После перехода он сразу получит кабинет риелтора.",
        InlineKeyboardMarkup(inline_keyboard=[[btn("⬅️ Риелторы", RealtorCB(action="list"))]]),
    )
    await ack(cq)


async def realtor_card(session: AsyncSession, ctx: AppContext, user_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    user = await users_repo.get(session, user_id)
    if user is None:
        return None
    totals = await stats_service.realtor_totals(session, user.id)
    month = await stats_service.collect(session, make_period("month", ctx.config.tz), ctx.config.tz, realtor_id=user.id)
    reports = await report_service.list_realtor_reports(session, user.id, limit=7)
    status = {UserStatus.ACTIVE: "🟢 активен", UserStatus.BLOCKED: "⛔ заблокирован", UserStatus.REMOVED: "🗑 удален"}[user.status]
    lines = [
        f"👤 <b>{esc(user.display_name)}</b>",
        f"Имя: {esc(user.first_name or '—')}",
        f"Username: {('@' + esc(user.username)) if user.username else '—'}",
        f"Telegram ID: <code>{user.telegram_id}</code>",
        f"Статус: {status}",
        f"Подключен: {fmt_dt(user.created_at, ctx.config.tz)}",
        "",
        "<b>Всего</b>",
        f"🏠 Объектов: {totals.properties} · 📋 заявок: {totals.leads} · 🤝 сделок: {totals.deals}",
        f"💰 Комиссии: {fmt_money_map(totals.commission)}",
        "",
        "<b>Этот месяц</b>",
        f"➕ объектов: {month.new_properties} · 📋 заявок: {month.leads_taken} · 👀 просмотров: {month.viewings}",
        f"🤝 сделок: {month.deals} · 💰 {fmt_money_map(month.commission)} · 📝 отчетов: {month.reports}",
    ]
    if reports:
        lines += ["", "<b>Последние отчеты</b>: " + ", ".join(fmt_date(r.report_date) for r in reports)]
    b = InlineKeyboardBuilder()
    b.row(btn("🏠 Объекты", RealtorCB(action="props", id=user.id)), btn("🤝 Сделки", RealtorCB(action="deals", id=user.id)))
    b.row(btn("📝 Отчеты", RealtorCB(action="reports", id=user.id)))
    if user.status == UserStatus.ACTIVE:
        b.row(btn("⛔ Заблокировать", RealtorCB(action="block", id=user.id)), btn("🗑 Удалить", RealtorCB(action="remove", id=user.id)))
    else:
        b.row(btn("✅ Восстановить доступ", RealtorCB(action="unblock", id=user.id)))
    b.row(btn("⬅️ Риелторы", RealtorCB(action="list")), home_btn())
    return "\n".join(lines), b.as_markup()


@router.callback_query(RealtorCB.filter(F.action == "open"))
async def cb_open(cq: CallbackQuery, callback_data: RealtorCB, session: AsyncSession, ctx: AppContext) -> None:
    card = await realtor_card(session, ctx, callback_data.id)
    if card is None:
        return await ack(cq, "Не найден", alert=True)
    await show(cq, *card)
    await ack(cq)


@router.callback_query(RealtorCB.filter(F.action.in_({"block", "unblock", "remove"})))
async def cb_status(cq: CallbackQuery, callback_data: RealtorCB, session: AsyncSession, ctx: AppContext) -> None:
    user = await users_repo.get(session, callback_data.id)
    if user is None:
        return await ack(cq, "Не найден", alert=True)
    status = {"block": UserStatus.BLOCKED, "unblock": UserStatus.ACTIVE, "remove": UserStatus.REMOVED}[callback_data.action]
    try:
        await user_service.set_status(session, user, status)
    except user_service.UserError as exc:
        return await ack(cq, str(exc), alert=True)
    await session.flush()
    if status == UserStatus.REMOVED:
        await ack(cq, "Сотрудник удален (история и сделки сохранены)")
        return await show_realtors(cq, session)
    await ack(cq, "Готово")
    card = await realtor_card(session, ctx, user.id)
    if card:
        await show(cq, *card)


@router.callback_query(RealtorCB.filter(F.action == "props"))
async def cb_props(cq: CallbackQuery, callback_data: RealtorCB, session: AsyncSession) -> None:
    items, total = await property_service.list_properties(session, scope="all", responsible_id=callback_data.id, page=callback_data.page)
    b = InlineKeyboardBuilder()
    for prop in items:
        b.row(btn(cards.short_line(prop)[:60], PropCB(action="open", id=prop.id)))
    nav = pager_row(lambda p: RealtorCB(action="props", id=callback_data.id, page=p), callback_data.page, total, 8)
    if nav:
        b.row(*nav)
    b.row(btn("⬅️ Риелтор", RealtorCB(action="open", id=callback_data.id)))
    await show(cq, f"🏠 Объекты риелтора — {total}", b.as_markup())
    await ack(cq)


@router.callback_query(RealtorCB.filter(F.action == "deals"))
async def cb_deals(cq: CallbackQuery, callback_data: RealtorCB, session: AsyncSession) -> None:
    deals, total = await deal_service.list_deals(session, realtor_id=callback_data.id, page=callback_data.page)
    b = InlineKeyboardBuilder()
    for deal in deals:
        b.row(btn(cards.deal_line(deal)[:60], DealCB(action="open", id=deal.id)))
    nav = pager_row(lambda p: RealtorCB(action="deals", id=callback_data.id, page=p), callback_data.page, total, 8)
    if nav:
        b.row(*nav)
    b.row(btn("⬅️ Риелтор", RealtorCB(action="open", id=callback_data.id)))
    await show(cq, f"🤝 Сделки риелтора — {total}", b.as_markup())
    await ack(cq)


@router.callback_query(RealtorCB.filter(F.action == "reports"))
async def cb_reports(cq: CallbackQuery, callback_data: RealtorCB, session: AsyncSession) -> None:
    reports = await report_service.list_realtor_reports(session, callback_data.id, limit=14)
    b = InlineKeyboardBuilder()
    for r in reports:
        b.row(btn(f"{fmt_date(r.report_date)}: клиенты {r.new_clients}, показы {r.viewings}, сделки {r.deals_closed}", ReportCB(action="view", id=r.id)))
    b.row(btn("⬅️ Риелтор", RealtorCB(action="open", id=callback_data.id)))
    await show(cq, f"📝 Отчеты риелтора (последние {len(reports)})" + ("" if reports else "\n\nОтчетов нет."), b.as_markup())
    await ack(cq)


# ======================= настройки =======================

def _id(value) -> str:
    return f"<code>{value}</code>" if value not in (None, "") else "❌ не задан"


def _webapp_line(ctx: AppContext) -> str:
    if ctx.config.webapp_short_name:
        return f"Форма записи (Mini App): ✅ <code>{esc(ctx.config.webapp_short_name)}</code>"
    url = ctx.public_url("/webapp/lead")
    hint = f"\n  URL для @BotFather → /newapp: <code>{esc(url)}</code>" if url else ""
    return "Форма записи (Mini App): ❌ не подключена — запись идет через чат с ботом" + hint


async def show_settings(event, ctx: AppContext, rs: RuntimeSettings) -> None:
    cfg = ctx.config
    lines = [
        "⚙️ <b>Настройки CRM</b>",
        "",
        "<b>Окружение (.env / Render)</b>",
        f"OWNER_ID: {_id(cfg.owner_id)}",
        f"WORK_CHAT_ID: {_id(rs.work_chat_id)}" + ("" if cfg.work_chat_id else " <i>(из бота)</i>" if rs.work_chat_id else ""),
        f"PUBLIC_CHANNEL_ID: {_id(cfg.public_channel_id)}",
    ]
    for key, title in TOPIC_TITLES.items():
        env_name = settings_service.TOPIC_KEYS[key]
        lines.append(f"{title} ({env_name}): {_id(rs.topics.get(key))}")
    lines += [
        f"Часовой пояс: {esc(cfg.timezone)} · режим: {'webhook' if cfg.use_webhook else 'polling'}",
        _webapp_line(ctx),
        f"Время запроса отчетов: <b>{esc(rs.report_time)}</b>",
        "",
        "<b>Переключатели</b> (нажмите, чтобы изменить):",
    ]
    b = InlineKeyboardBuilder()
    for key, (title, _) in TOGGLES.items():
        b.row(btn(f"{'✅' if rs.toggles[key] else '▫️'} {title}", SettingsCB(action="toggle", key=key)))
    b.row(btn("🕙 Время отчетов", SettingsCB(action="report_time")), btn("📨 Разослать запрос отчета сейчас", SettingsCB(action="remind_now")))
    b.row(btn("🧵 Создать темы в рабочем чате", SettingsCB(action="topics")), btn("🩺 Проверить подключение", SettingsCB(action="check")))
    b.row(home_btn())
    await show(event, "\n".join(lines), b.as_markup())


@router.callback_query(SettingsCB.filter(F.action == "open"))
async def cb_settings(cq: CallbackQuery, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    await state.clear()
    await show_settings(cq, ctx, rs)
    await ack(cq)


@router.callback_query(SettingsCB.filter(F.action == "toggle"))
async def cb_toggle(cq: CallbackQuery, callback_data: SettingsCB, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    if callback_data.key not in TOGGLES:
        return await ack(cq)
    new_value = not rs.toggles[callback_data.key]
    await settings_service.set_toggle(session, callback_data.key, new_value)
    rs.toggles[callback_data.key] = new_value
    await ack(cq, "Сохранено")
    await show_settings(cq, ctx, rs)


@router.callback_query(SettingsCB.filter(F.action == "report_time"))
async def cb_report_time(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsEdit.report_time)
    await show(cq, "🕙 Во сколько отправлять запрос отчета? Формат ЧЧ:ММ, например 22:00", cancel_kb())
    await ack(cq)


@router.message(SettingsEdit.report_time, F.text)
async def msg_report_time(message: Message, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext, scheduler=None) -> None:
    hm = parse_hhmm(message.text)
    if not hm:
        return await message.answer("Формат ЧЧ:ММ, например 22:00", reply_markup=cancel_kb())
    value = f"{hm[0]:02d}:{hm[1]:02d}"
    await settings_service.set_report_time(session, value)
    await state.clear()
    rs.report_time = value
    if scheduler is not None:
        scheduler.reschedule(value)
    await message.answer(f"✅ Запрос отчетов будет приходить в {value} ({esc(ctx.config.timezone)}).")
    await show_settings(message, ctx, rs)


@router.callback_query(SettingsCB.filter(F.action == "remind_now"))
async def cb_remind_now(cq: CallbackQuery, ctx: AppContext) -> None:
    await ack(cq, "Отправляю…")
    async with ctx.session_factory() as s:
        await settings_repo.set_value(s, "report_reminder_date", None)
        await s.commit()
    sent = await reports_h.send_report_reminders(ctx, force=True)
    await cq.message.answer(f"📨 Запрос отчета отправлен риелторам: {sent}.")


@router.callback_query(SettingsCB.filter(F.action == "topics"))
async def cb_create_topics(cq: CallbackQuery, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    chat = rs.work_chat_id
    if not chat:
        return await ack(cq, "Сначала задайте WORK_CHAT_ID или выполните /bindchat в рабочем чате.", alert=True)
    await ack(cq, "Создаю темы…")
    created, failed = [], []
    for key, title in TOPIC_TITLES.items():
        if rs.topics.get(key):
            continue
        topic = await safe_call("create_topic", ctx.bot.create_forum_topic, chat_id=chat, name=title)
        if topic:
            await settings_service.set_topic(session, key, topic.message_thread_id)
            rs.topics[key] = topic.message_thread_id
            created.append(title)
        else:
            failed.append(title)
    text = "🧵 Темы: " + (f"создано — {', '.join(created)}. " if created else "новых не создано. ")
    if failed:
        text += f"\n⚠️ Не удалось: {', '.join(failed)}. Включите Topics в группе и дайте боту право «Управление темами»."
    await cq.message.answer(text)
    await show_settings(cq, ctx, rs)


@router.callback_query(SettingsCB.filter(F.action == "check"))
async def cb_check(cq: CallbackQuery, ctx: AppContext, rs: RuntimeSettings) -> None:
    await ack(cq, "Проверяю…")
    me = await ctx.bot.get_me()
    lines = ["🩺 <b>Проверка подключения</b>", f"Бот: @{esc(me.username)}"]
    for label, chat_id in (("Рабочий чат", rs.work_chat_id), ("Публичный канал", ctx.config.public_channel_id)):
        if not chat_id:
            lines.append(f"❌ {label}: не задан")
            continue
        chat = await safe_call("check_chat", ctx.bot.get_chat, chat_id)
        if chat is None:
            lines.append(f"❌ {label}: бот не видит чат {esc(chat_id)} (добавьте бота администратором)")
            continue
        member = await safe_call("check_member", ctx.bot.get_chat_member, chat_id, me.id)
        is_admin = getattr(member, "status", "") in ("administrator", "creator")
        extra = ""
        if label == "Рабочий чат":
            extra = " · Topics: " + ("✅" if getattr(chat, "is_forum", False) else "❌ выключены")
        lines.append(f"{'✅' if is_admin else '⚠️'} {label}: {esc(chat.title)}{' (бот не администратор)' if not is_admin else ''}{extra}")
    await cq.message.answer("\n".join(lines))
