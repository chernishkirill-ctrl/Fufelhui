"""Заявки: «Взять клиента» в рабочем чате, карточки заявок, статусы, ручное назначение."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Lead, LeadStatus
from bot.database.repositories import users as users_repo
from bot.handlers.states import LeadWork
from bot.keyboards.callbacks import DealCB, LeadCB, PropCB
from bot.keyboards.menus import btn, cancel_kb, home_btn, pager_row
from bot.services import cards, lead_service, property_service, workchat_service
from bot.services.auth import Actor, can_edit_property, can_manage_lead, can_view_lead_private
from bot.services.context import AppContext
from bot.services.lead_service import LEAD_STATUS_LABELS, REALTOR_TRANSITIONS
from bot.services.property_service import PAGE_SIZE
from bot.services.settings_service import RuntimeSettings
from bot.utils.telegram import safe_call
from bot.utils.text import esc, parse_int
from bot.utils.ui import ack, deny, show

logger = logging.getLogger(__name__)
router = Router(name="leads")
router.message.filter(F.chat.type == "private")

FILTER_TITLES = {"new": "🆕 Новые", "work": "🔄 В работе", "done": "🏁 Закрытые", "all": "📚 Все", "open": "📋 Открытые"}


# ======================= «Взять клиента» =======================

@router.callback_query(LeadCB.filter(F.action == "take"))
async def cb_take(cq: CallbackQuery, callback_data: LeadCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    if not actor.is_staff or actor.user is None:
        return await deny(cq)
    result = await lead_service.take_lead(session, callback_data.id, actor.user)
    if not result.ok:
        if result.reason == "not_found":
            return await ack(cq, "Заявка не найдена.", alert=True)
        lead = result.lead
        if lead and lead.assigned_realtor_id == actor.user_id:
            return await ack(cq, "Эта заявка уже ваша — см. «Мои заявки».", alert=True)
        # Кнопка у остальных должна исчезнуть, даже если редактирование ранее не удалось
        if lead and lead.assigned_realtor_id:
            await workchat_service.mark_lead_taken(ctx, rs, lead)
        return await ack(cq, "Заявку уже забрал другой риелтор.", alert=True)

    lead = result.lead
    # Фиксируем назначение до любых сетевых вызовов
    await session.commit()
    await session.refresh(lead)
    await workchat_service.mark_lead_taken(ctx, rs, lead)
    sent = await safe_call(
        "lead_to_realtor", ctx.bot.send_message, chat_id=actor.telegram_id,
        text="🙋 Вы взяли клиента!\n\n" + cards.lead_private_card(lead, ctx.config.tz),
        reply_markup=lead_keyboard(actor, lead),
    )
    if rs.notify_owner_leads and not actor.is_owner:
        await workchat_service.notify_owner(
            ctx,
            f"🙋 {esc(actor.user.display_name)} взял(а) заявку {lead.code}",
            InlineKeyboardMarkup(inline_keyboard=[[btn("Открыть заявку", LeadCB(action="open", id=lead.id))]]),
        )
    if sent:
        await ack(cq, "✅ Заявка ваша! Данные клиента отправлены вам в личные сообщения.", alert=True)
    else:
        await ack(cq, "✅ Заявка ваша! Откройте бота → «Мои заявки» (нажмите /start в личке с ботом).", alert=True)


# ======================= карточка заявки =======================

def lead_keyboard(actor: Actor, lead: Lead) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if actor.is_owner:
        statuses = [s for s in LeadStatus if s != lead.status]
    else:
        statuses = REALTOR_TRANSITIONS.get(lead.status, [])
    for status in statuses:
        b.add(btn(LEAD_STATUS_LABELS[status], LeadCB(action="st", id=lead.id, arg=status.value)))
    b.adjust(2)
    b.row(btn("📝 Заметка", LeadCB(action="note", id=lead.id)))
    if lead.property_id and lead.status not in (LeadStatus.CANCELLED,):
        b.row(btn("🤝 Оформить сделку", DealCB(action="new", arg=f"l{lead.id}")))
    if actor.is_owner:
        b.row(btn("👤 Назначить риелтора", LeadCB(action="asg", id=lead.id)))
    if lead.property_id:
        b.row(btn("🏠 Объект", PropCB(action="open", id=lead.property_id)))
    b.row(btn("⬅️ Заявки", LeadCB(action="menu")), home_btn())
    return b.as_markup()


async def load_lead(event, session: AsyncSession, lead_id: int) -> Lead | None:
    lead = await lead_service.get_lead(session, lead_id)
    if lead is None:
        if isinstance(event, CallbackQuery):
            await event.answer("Заявка не найдена.", show_alert=True)
        else:
            await event.answer("Заявка не найдена.")
    return lead


async def show_lead(event, actor: Actor, ctx: AppContext, lead: Lead) -> None:
    if not can_view_lead_private(actor, lead):
        return await deny(event)
    await show(event, cards.lead_private_card(lead, ctx.config.tz), lead_keyboard(actor, lead))


@router.callback_query(LeadCB.filter(F.action == "open"))
async def cb_open(cq: CallbackQuery, callback_data: LeadCB, actor: Actor, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    lead = await load_lead(cq, session, callback_data.id)
    if lead:
        await show_lead(cq, actor, ctx, lead)
        await ack(cq)


@router.callback_query(LeadCB.filter(F.action == "st"))
async def cb_status(cq: CallbackQuery, callback_data: LeadCB, actor: Actor, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    lead = await load_lead(cq, session, callback_data.id)
    if not lead:
        return
    if not can_manage_lead(actor, lead):
        return await deny(cq)
    try:
        status = LeadStatus(callback_data.arg)
    except ValueError:
        return await ack(cq)
    if not actor.is_owner and status not in REALTOR_TRANSITIONS.get(lead.status, []):
        return await ack(cq, "Этот переход недоступен", alert=True)
    if status == LeadStatus.NEW and not actor.is_owner:
        return await deny(cq)
    if status == LeadStatus.VIEWING_SCHEDULED:
        await state.set_state(LeadWork.viewing_time)
        await state.update_data(lead_id=lead.id)
        await show(cq, "📅 Когда просмотр? Напишите дату и время (например «28.09 18:30»):", cancel_kb())
        return await ack(cq)
    if status == LeadStatus.NEW:
        # Владелец возвращает заявку в общий пул
        lead.assigned_realtor_id = None
        lead.assigned_at = None
    await lead_service.set_status(session, lead, status, actor.user_id)
    await ack(cq, LEAD_STATUS_LABELS[status])
    await show_lead(cq, actor, ctx, lead)


@router.message(LeadWork.viewing_time, F.text)
async def msg_viewing_time(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    lead = await load_lead(message, session, data.get("lead_id", 0))
    if not lead:
        return
    if not can_manage_lead(actor, lead):
        return await deny(message)
    await lead_service.set_status(session, lead, LeadStatus.VIEWING_SCHEDULED, actor.user_id, viewing_at=message.text.strip()[:120])
    await message.answer("✅ Просмотр назначен.")
    await show_lead(message, actor, ctx, lead)


@router.callback_query(LeadCB.filter(F.action == "note"))
async def cb_note(cq: CallbackQuery, callback_data: LeadCB, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    lead = await load_lead(cq, session, callback_data.id)
    if not lead:
        return
    if not can_manage_lead(actor, lead):
        return await deny(cq)
    await state.set_state(LeadWork.note)
    await state.update_data(lead_id=lead.id)
    await show(cq, "📝 Внутренняя заметка по заявке (видна только вам и владельцу):", cancel_kb())
    await ack(cq)


@router.message(LeadWork.note, F.text)
async def msg_note(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    lead = await load_lead(message, session, data.get("lead_id", 0))
    if not lead:
        return
    if not can_manage_lead(actor, lead):
        return await deny(message)
    lead.internal_comment = message.text.strip()[:2000]
    await message.answer("✅ Заметка сохранена.")
    await show_lead(message, actor, ctx, lead)


# ======================= ручное назначение (владелец) =======================

@router.callback_query(LeadCB.filter(F.action == "asg"))
async def cb_assign_menu(cq: CallbackQuery, callback_data: LeadCB, actor: Actor, session: AsyncSession) -> None:
    if not actor.is_owner:
        return await deny(cq)
    lead = await load_lead(cq, session, callback_data.id)
    if not lead:
        return
    b = InlineKeyboardBuilder()
    for user in await users_repo.list_staff(session, include_owner=True):
        mark = "✅ " if user.id == lead.assigned_realtor_id else ""
        b.add(btn(f"{mark}{user.display_name}", LeadCB(action="asgs", id=lead.id, arg=str(user.id))))
    b.adjust(2)
    b.row(btn("⬅️ К заявке", LeadCB(action="open", id=lead.id)))
    await show(cq, f"👤 Кому передать заявку {lead.code}?", b.as_markup())
    await ack(cq)


@router.callback_query(LeadCB.filter(F.action == "asgs"))
async def cb_assign(cq: CallbackQuery, callback_data: LeadCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    if not actor.is_owner:
        return await deny(cq)
    lead = await load_lead(cq, session, callback_data.id)
    if not lead:
        return
    realtor = await users_repo.get(session, parse_int(callback_data.arg) or 0)
    if realtor is None or not realtor.is_active:
        return await ack(cq, "Сотрудник не найден или заблокирован", alert=True)
    await lead_service.assign_lead(session, lead, realtor, actor.user_id)
    await workchat_service.mark_lead_taken(ctx, rs, lead)
    if realtor.telegram_id != actor.telegram_id:
        realtor_actor = Actor(telegram_id=realtor.telegram_id, user=realtor, is_owner=False)
        await safe_call(
            "lead_assigned", ctx.bot.send_message, chat_id=realtor.telegram_id,
            text="👤 Вам назначена заявка\n\n" + cards.lead_private_card(lead, ctx.config.tz),
            reply_markup=lead_keyboard(realtor_actor, lead),
        )
    await ack(cq, "Назначено")
    await show_lead(cq, actor, ctx, lead)


# ======================= списки =======================

async def show_leads_menu(event, actor: Actor, session: AsyncSession) -> None:
    if not actor.is_staff:
        return await deny(event)
    b = InlineKeyboardBuilder()
    if actor.is_owner:
        b.row(btn(FILTER_TITLES["new"], LeadCB(action="list", arg="new")), btn(FILTER_TITLES["work"], LeadCB(action="list", arg="work")))
        b.row(btn(FILTER_TITLES["done"], LeadCB(action="list", arg="done")), btn(FILTER_TITLES["all"], LeadCB(action="list", arg="all")))
        title = "📋 <b>Заявки клиентов</b>"
    else:
        b.row(btn("🔄 В работе", LeadCB(action="list", arg="m:work")), btn("🏁 Закрытые", LeadCB(action="list", arg="m:done")))
        b.row(btn("🆕 Свободные заявки", LeadCB(action="list", arg="new")))
        title = "📋 <b>Мои заявки</b>"
    b.row(home_btn())
    await show(event, title, b.as_markup())


@router.callback_query(LeadCB.filter(F.action == "menu"))
async def cb_menu(cq: CallbackQuery, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await show_leads_menu(cq, actor, session)
    await ack(cq)


async def show_leads_list(event, actor: Actor, session: AsyncSession, arg: str, page: int = 0, property_id: int | None = None) -> None:
    if not actor.is_staff:
        return await deny(event)
    mine = arg.startswith("m:")
    flt = arg[2:] if mine else arg
    if not actor.is_owner and not mine and flt != "new" and property_id is None:
        return await deny(event)
    realtor_id = actor.user_id if mine else None
    leads, total = await lead_service.list_leads(session, flt=flt, realtor_id=realtor_id, property_id=property_id, page=page)
    title = FILTER_TITLES.get(flt, flt)
    b = InlineKeyboardBuilder()
    for lead in leads:
        private = can_view_lead_private(actor, lead)
        if private:
            b.row(btn(cards.lead_line(lead, show_client=True)[:60], LeadCB(action="open", id=lead.id)))
        elif lead.status == LeadStatus.NEW:
            b.row(btn(f"🙋 Взять: {cards.lead_line(lead, show_client=False)}"[:60], LeadCB(action="take", id=lead.id)))
        else:
            b.row(btn(cards.lead_line(lead, show_client=False)[:60], LeadCB(action="noop", id=lead.id)))
    nav = pager_row(lambda p: LeadCB(action="plist" if property_id else "list", id=property_id or 0, arg=arg, page=p), page, total, PAGE_SIZE)
    if nav:
        b.row(*nav)
    if property_id:
        b.row(btn("⬅️ К объекту", PropCB(action="open", id=property_id)))
    else:
        b.row(btn("⬅️ Заявки", LeadCB(action="menu")), home_btn())
    header = f"📋 Заявки по объекту — {total}" if property_id else f"📋 {'Мои: ' if mine else ''}{title} — {total}"
    await show(event, header if leads else header + "\n\nЗаявок нет.", b.as_markup())


@router.callback_query(LeadCB.filter(F.action == "list"))
async def cb_list(cq: CallbackQuery, callback_data: LeadCB, actor: Actor, session: AsyncSession) -> None:
    await show_leads_list(cq, actor, session, callback_data.arg or "open", callback_data.page)
    await ack(cq)


@router.callback_query(LeadCB.filter(F.action == "plist"))
async def cb_property_leads(cq: CallbackQuery, callback_data: LeadCB, actor: Actor, session: AsyncSession) -> None:
    prop = await property_service.get_property(session, callback_data.id)
    if prop is None:
        return await ack(cq, "Объект не найден", alert=True)
    if not (actor.is_owner or can_edit_property(actor, prop)):
        return await deny(cq)
    await show_leads_list(cq, actor, session, callback_data.arg or "all", callback_data.page, property_id=prop.id)
    await ack(cq)


@router.callback_query(LeadCB.filter(F.action == "noop"))
async def cb_noop(cq: CallbackQuery) -> None:
    await ack(cq, "Заявка у другого риелтора — детали скрыты.", alert=True)
