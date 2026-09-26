"""Сделки: пошаговая фиксация, список, карточка, редактирование и отмена (владелец)."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import ARCHIVE_STATUSES, DealStatus, DealType, OfferType, Property
from bot.database.repositories import users as users_repo
from bot.handlers.states import DealCreate, DealEdit
from bot.keyboards.callbacks import DealCB, FormCB, PropCB
from bot.keyboards.menus import btn, cancel_kb, choices_kb, home_btn, pager_row, skip_cancel_kb
from bot.services import (
    cards,
    deal_service,
    lead_service,
    property_service,
    publication_service,
    workchat_service,
)
from bot.services.auth import Actor, can_edit_deal, can_view_deal
from bot.services.context import AppContext
from bot.services.deal_service import DEAL_EDIT_FIELDS, DEAL_TYPE_LABELS, DealError
from bot.services.property_service import PAGE_SIZE
from bot.services.settings_service import RuntimeSettings
from bot.utils.text import esc, fmt_money, parse_int, parse_money
from bot.utils.timeutils import fmt_date, local_today, parse_date
from bot.utils.ui import ack, deny, show

logger = logging.getLogger(__name__)
router = Router(name="deals")
router.message.filter(F.chat.type == "private")


# ======================= старт =======================

async def start_deal(event: Message | CallbackQuery, actor: Actor, session: AsyncSession, state: FSMContext, arg: str = "") -> None:
    if not actor.is_staff:
        return await deny(event)
    await state.clear()
    data: dict = {"realtor_id": None if actor.is_owner else actor.user_id}
    if arg.startswith("l"):
        lead = await lead_service.get_lead(session, parse_int(arg[1:]) or 0)
        if lead is None or not lead.property_id:
            return await show(event, "Заявка не найдена или без объекта.")
        if not actor.is_owner and lead.assigned_realtor_id != actor.user_id:
            return await deny(event)
        data.update(property_id=lead.property_id, lead_id=lead.id, realtor_id=lead.assigned_realtor_id or data["realtor_id"])
        await state.update_data(deal=data)
        return await _after_realtor(event, session, state)
    if arg.startswith("p"):
        prop = await property_service.get_property(session, parse_int(arg[1:]) or 0)
        if prop is None:
            return await show(event, "Объект не найден.")
        data["property_id"] = prop.id
        await state.update_data(deal=data)
        return await _after_property(event, actor, session, state)
    await state.update_data(deal=data)
    await state.set_state(DealCreate.property)
    b = InlineKeyboardBuilder()
    items, _ = await property_service.list_properties(session, scope="active", responsible_id=None if actor.is_owner else actor.user_id)
    for prop in items[:6]:
        b.row(btn(cards.short_line(prop)[:60], FormCB(field="dprop", value=str(prop.id))))
    b.row(btn("✖️ Отмена", FormCB(field="cancel")))
    await show(
        event,
        "🤝 <b>Фиксация сделки</b>\n\nШаг 1. Объект: выберите из списка или введите номер (например <code>125</code> или <code>OBJ-000125</code>).",
        b.as_markup(),
        new=isinstance(event, CallbackQuery),
    )


@router.callback_query(DealCB.filter(F.action == "new"))
async def cb_new(cq: CallbackQuery, callback_data: DealCB, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    await start_deal(cq, actor, session, state, callback_data.arg)
    await ack(cq)


async def _data(state: FSMContext) -> dict:
    return dict((await state.get_data()).get("deal", {}))


async def _set(state: FSMContext, **values) -> dict:
    data = await _data(state)
    data.update(values)
    await state.update_data(deal=data)
    return data


async def _choose_property(event, actor: Actor, session: AsyncSession, state: FSMContext, prop_id: int) -> None:
    prop = await property_service.get_property(session, prop_id)
    if prop is None:
        await show(event, "Объект не найден. Введите другой номер:", cancel_kb(), new=isinstance(event, Message))
        return
    await _set(state, property_id=prop.id)
    await _after_property(event, actor, session, state)


@router.callback_query(DealCreate.property, FormCB.filter(F.field == "dprop"))
async def deal_prop_btn(cq: CallbackQuery, callback_data: FormCB, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    await _choose_property(cq, actor, session, state, parse_int(callback_data.value) or 0)
    await ack(cq)


@router.message(DealCreate.property, F.text)
async def deal_prop_text(message: Message, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    code = property_service.parse_property_code(message.text)
    if not code:
        return await message.answer("Введите номер объекта, например 125 или OBJ-000125.", reply_markup=cancel_kb())
    await _choose_property(message, actor, session, state, code)


async def _after_property(event, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    data = await _data(state)
    if actor.is_owner and not data.get("realtor_id"):
        await state.set_state(DealCreate.realtor)
        options = [(u.display_name, str(u.id)) for u in await users_repo.list_staff(session, include_owner=True)]
        await show(event, "Шаг 2. Риелтор, закрывший сделку:", choices_kb("drealtor", options), new=isinstance(event, Message))
        return
    await _after_realtor(event, session, state)


@router.callback_query(DealCreate.realtor, FormCB.filter(F.field == "drealtor"))
async def deal_realtor(cq: CallbackQuery, callback_data: FormCB, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    if not actor.is_owner:
        return await deny(cq)
    user = await users_repo.get(session, parse_int(callback_data.value) or 0)
    if user is None:
        return await ack(cq, "Сотрудник не найден", alert=True)
    await _set(state, realtor_id=user.id)
    await _after_realtor(cq, session, state)
    await ack(cq)


async def _after_realtor(event, session: AsyncSession, state: FSMContext) -> None:
    data = await _data(state)
    if not data.get("lead_id"):
        leads = await lead_service.leads_for_deal(session, data["property_id"], data.get("realtor_id"))
        if leads:
            await state.set_state(DealCreate.lead)
            options = [(f"{lead.code} · {lead.client_name or 'клиент'}", str(lead.id)) for lead in leads]
            options.append(("Без заявки", "0"))
            await show(event, "Шаг 3. Клиент (заявка), с которым закрыта сделка:", choices_kb("dlead", options, per_row=1), new=isinstance(event, Message))
            return
    await _ask_type(event, session, state)


@router.callback_query(DealCreate.lead, FormCB.filter(F.field == "dlead"))
async def deal_lead(cq: CallbackQuery, callback_data: FormCB, session: AsyncSession, state: FSMContext) -> None:
    lead_id = parse_int(callback_data.value) or None
    await _set(state, lead_id=lead_id)
    await _ask_type(cq, session, state)
    await ack(cq)


async def _ask_type(event, session: AsyncSession, state: FSMContext) -> None:
    data = await _data(state)
    prop = await property_service.get_property(session, data["property_id"])
    default = "rent" if prop and prop.offer_type == OfferType.RENT else "sale"
    options = [(("✅ " if v == default else "") + DEAL_TYPE_LABELS[DealType(v)].capitalize(), v) for v in ("sale", "rent", "other")]
    await state.set_state(DealCreate.deal_type)
    header = f"Объект: {esc(cards.short_line(prop))}\n\n" if prop else ""
    await show(event, header + "Шаг 4. Тип сделки:", choices_kb("dtype", options, per_row=3), new=isinstance(event, Message))


@router.callback_query(DealCreate.deal_type, FormCB.filter(F.field == "dtype"))
async def deal_type(cq: CallbackQuery, callback_data: FormCB, state: FSMContext) -> None:
    try:
        DealType(callback_data.value)
    except ValueError:
        return await ack(cq)
    await _set(state, deal_type=callback_data.value)
    await state.set_state(DealCreate.amount)
    await show(cq, "Шаг 5. Сумма сделки (например <code>45000$</code> или <code>15000 грн</code>):", cancel_kb())
    await ack(cq)


@router.message(DealCreate.amount, F.text)
async def deal_amount(message: Message, state: FSMContext, session: AsyncSession, ctx: AppContext) -> None:
    data = await _data(state)
    prop = await property_service.get_property(session, data["property_id"])
    default_currency = prop.currency if prop else ctx.config.default_currency
    money = parse_money(message.text, default_currency)
    if not money:
        return await message.answer("Введите сумму числом, например 45000$", reply_markup=cancel_kb())
    await _set(state, amount=str(money[0]), currency=money[1])
    await state.set_state(DealCreate.commission)
    await message.answer(
        f"Шаг 6. Комиссия агентства (в {money[1]}): сумма или процент от сделки, например <code>1500</code> или <code>3%</code>:",
        reply_markup=cancel_kb(),
    )


@router.message(DealCreate.commission, F.text)
async def deal_commission(message: Message, state: FSMContext) -> None:
    data = await _data(state)
    raw = message.text.strip()
    if raw.endswith("%"):
        pct = parse_money(raw[:-1])
        if not pct or pct[0] > 100:
            return await message.answer("Процент должен быть от 0 до 100.", reply_markup=cancel_kb())
        commission = (Decimal(data["amount"]) * pct[0] / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        money = parse_money(raw, data.get("currency", "USD"))
        if not money:
            return await message.answer("Введите комиссию числом или процентом.", reply_markup=cancel_kb())
        commission = money[0]
    await _set(state, commission=str(commission))
    await state.set_state(DealCreate.date)
    await message.answer(
        "Шаг 7. Дата сделки: выберите или введите (ДД.ММ.ГГГГ):",
        reply_markup=choices_kb("ddate", [("Сегодня", "today"), ("Вчера", "yesterday")]),
    )


async def _ask_comment(event, state: FSMContext) -> None:
    await state.set_state(DealCreate.comment)
    await show(event, "Шаг 8. Комментарий (необязательно):", skip_cancel_kb(), new=isinstance(event, Message))


@router.callback_query(DealCreate.date, FormCB.filter(F.field == "ddate"))
async def deal_date_btn(cq: CallbackQuery, callback_data: FormCB, state: FSMContext, ctx: AppContext) -> None:
    today = local_today(ctx.config.tz)
    d = today if callback_data.value == "today" else today - timedelta(days=1)
    await _set(state, date=d.isoformat())
    await _ask_comment(cq, state)
    await ack(cq)


@router.message(DealCreate.date, F.text)
async def deal_date_text(message: Message, state: FSMContext, ctx: AppContext) -> None:
    today = local_today(ctx.config.tz)
    d = parse_date(message.text, today)
    if d is None or d > today + timedelta(days=1):
        return await message.answer("Введите дату в формате ДД.ММ.ГГГГ (не в будущем).", reply_markup=cancel_kb())
    await _set(state, date=d.isoformat())
    await _ask_comment(message, state)


@router.callback_query(DealCreate.comment, FormCB.filter(F.field == "skip"))
async def deal_comment_skip(cq: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await _confirm(cq, state, session)
    await ack(cq)


@router.message(DealCreate.comment, F.text)
async def deal_comment(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _set(state, comment=message.text.strip()[:1000])
    await _confirm(message, state, session)


async def _confirm(event, state: FSMContext, session: AsyncSession) -> None:
    data = await _data(state)
    prop = await property_service.get_property(session, data["property_id"])
    realtor = await users_repo.get(session, data["realtor_id"]) if data.get("realtor_id") else None
    lead = await lead_service.get_lead(session, data["lead_id"]) if data.get("lead_id") else None
    await state.set_state(DealCreate.confirm)
    lines = [
        "🤝 <b>Проверьте сделку</b>",
        "",
        f"Объект: {esc(cards.short_line(prop)) if prop else '—'}",
        f"Риелтор: {esc(realtor.display_name) if realtor else '—'}",
        f"Клиент: {esc(lead.client_name or lead.code) if lead else '—'}",
        f"Тип: {DEAL_TYPE_LABELS[DealType(data['deal_type'])]}",
        f"Сумма: {fmt_money(Decimal(data['amount']), data['currency'])}",
        f"Комиссия: {fmt_money(Decimal(data['commission']), data['currency'])}",
        f"Дата: {fmt_date(date.fromisoformat(data['date']))}",
    ]
    if data.get("comment"):
        lines.append(f"Комментарий: {esc(data['comment'])}")
    target = DEAL_TARGET.get(DealType(data["deal_type"]))
    if target and prop:
        lines.append(f"\nОбъект получит статус «{target}» и уйдет в архив.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✅ Подтвердить", FormCB(field="dconfirm"))], [btn("✖️ Отмена", FormCB(field="cancel"))]])
    await show(event, "\n".join(lines), kb, new=isinstance(event, Message))


DEAL_TARGET = {DealType.SALE: "продан", DealType.RENT: "сдан"}


@router.callback_query(DealCreate.confirm, FormCB.filter(F.field == "dconfirm"))
async def deal_save(cq: CallbackQuery, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    if not actor.is_staff:
        await state.clear()
        return await deny(cq)
    data = await _data(state)
    await state.clear()
    if not data.get("property_id") or not data.get("amount"):
        return await ack(cq, "Данные устарели, начните заново.", alert=True)
    realtor_id = data.get("realtor_id") or actor.user_id
    if not actor.is_owner and realtor_id != actor.user_id:
        return await deny(cq)  # риелтор может фиксировать сделки только на себя
    prop = await property_service.get_property(session, data["property_id"])
    if prop is None:
        return await ack(cq, "Объект не найден.", alert=True)
    was_archived = prop.is_archived
    try:
        deal, _old = await deal_service.create_deal(
            session,
            prop=prop,
            realtor_id=realtor_id,
            deal_type=DealType(data["deal_type"]),
            amount=Decimal(data["amount"]),
            commission=Decimal(data["commission"]),
            currency=data.get("currency", "USD"),
            deal_date=date.fromisoformat(data["date"]),
            comment=data.get("comment"),
            lead_id=data.get("lead_id"),
            created_by_id=actor.user_id,
        )
    except DealError as exc:
        return await ack(cq, str(exc), alert=True)
    await session.commit()  # сделка сохранена до сетевых вызовов
    await _property_side_effects(ctx, session, rs, actor, prop, was_archived)
    await workchat_service.post_deal(ctx, rs, deal)
    if rs.notify_owner_deals and not actor.is_owner:
        await workchat_service.notify_owner(
            ctx,
            "🤝 <b>Новая сделка</b>\n\n" + cards.deal_card(deal, ctx.config.tz),
            InlineKeyboardMarkup(inline_keyboard=[[btn("Открыть сделку", DealCB(action="open", id=deal.id))]]),
        )
    await ack(cq, "Сделка зафиксирована 🎉")
    await show(cq, "🎉 <b>Сделка зафиксирована!</b>\n\n" + cards.deal_card(deal, ctx.config.tz), InlineKeyboardMarkup(inline_keyboard=[[home_btn()]]))


async def _property_side_effects(ctx: AppContext, session: AsyncSession, rs: RuntimeSettings, actor: Actor, prop: Property, was_archived: bool) -> None:
    if prop.status in ARCHIVE_STATUSES and not was_archived:
        if prop.public_message_id and rs.unpublish_on_archive:
            status = prop.status
            await publication_service.unpublish(ctx, session, prop, actor.user_id, record=True)
            prop.status = status
        await workchat_service.post_archive_card(ctx, rs, prop)
    await workchat_service.refresh_base_card(ctx, rs, prop)


# ======================= списки и карточка =======================

async def show_deals_menu(event, actor: Actor, session: AsyncSession, page: int = 0) -> None:
    if not actor.is_staff:
        return await deny(event)
    realtor_id = None if actor.is_owner else actor.user_id
    deals, total = await deal_service.list_deals(session, realtor_id=realtor_id, page=page)
    b = InlineKeyboardBuilder()
    b.row(btn("➕ Зафиксировать сделку", DealCB(action="new")))
    for deal in deals:
        b.row(btn(cards.deal_line(deal)[:60], DealCB(action="open", id=deal.id)))
    nav = pager_row(lambda p: DealCB(action="list", page=p), page, total, PAGE_SIZE)
    if nav:
        b.row(*nav)
    b.row(home_btn())
    title = "💰 <b>Сделки</b>" if actor.is_owner else "🤝 <b>Мои сделки</b>"
    await show(event, f"{title} — всего {total}" + ("" if deals else "\n\nСделок пока нет."), b.as_markup())


@router.callback_query(DealCB.filter(F.action == "list"))
async def cb_list(cq: CallbackQuery, callback_data: DealCB, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await show_deals_menu(cq, actor, session, callback_data.page)
    await ack(cq)


def deal_keyboard(actor: Actor, deal) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if can_edit_deal(actor) and deal.status == DealStatus.CONFIRMED:
        for key, label in DEAL_EDIT_FIELDS.items():
            b.add(btn(f"✏️ {label}", DealCB(action="ed", id=deal.id, arg=key)))
        b.adjust(2)
        b.row(btn("❌ Отменить сделку", DealCB(action="cancel", id=deal.id)))
    if deal.property_id:
        b.row(btn("🏠 Объект", PropCB(action="open", id=deal.property_id)))
    b.row(btn("⬅️ Сделки", DealCB(action="list")), home_btn())
    return b.as_markup()


async def _load(cq: CallbackQuery, session: AsyncSession, deal_id: int):
    deal = await deal_service.get_deal(session, deal_id)
    if deal is None:
        await ack(cq, "Сделка не найдена", alert=True)
    return deal


@router.callback_query(DealCB.filter(F.action == "open"))
async def cb_open(cq: CallbackQuery, callback_data: DealCB, actor: Actor, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    deal = await _load(cq, session, callback_data.id)
    if not deal:
        return
    if not can_view_deal(actor, deal):
        return await deny(cq)
    await show(cq, cards.deal_card(deal, ctx.config.tz), deal_keyboard(actor, deal))
    await ack(cq)


@router.callback_query(DealCB.filter(F.action == "ed"))
async def cb_edit(cq: CallbackQuery, callback_data: DealCB, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    if not can_edit_deal(actor):
        return await deny(cq)
    deal = await _load(cq, session, callback_data.id)
    if not deal or callback_data.arg not in DEAL_EDIT_FIELDS:
        return
    await state.set_state(DealEdit.value)
    await state.update_data(deal_id=deal.id, field=callback_data.arg)
    hints = {"amount": "например 45000", "commission": "например 1500", "comment": "текст, «-» — очистить", "date": "ДД.ММ.ГГГГ"}
    await show(cq, f"✏️ {DEAL_EDIT_FIELDS[callback_data.arg]} ({hints[callback_data.arg]}):", cancel_kb())
    await ack(cq)


@router.message(DealEdit.value, F.text)
async def msg_edit(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    if not can_edit_deal(actor):
        await state.clear()
        return await deny(message)
    data = await state.get_data()
    deal = await deal_service.get_deal(session, data.get("deal_id", 0))
    if deal is None:
        await state.clear()
        return await message.answer("Сделка не найдена.")
    field = data.get("field")
    raw = message.text.strip()
    if field in ("amount", "commission"):
        money = parse_money(raw, deal.currency)
        if not money:
            return await message.answer("Введите число.", reply_markup=cancel_kb())
        old = getattr(deal, "deal_amount" if field == "amount" else "commission")
        setattr(deal, "deal_amount" if field == "amount" else "commission", money[0])
        new = money[0]
    elif field == "date":
        d = parse_date(raw, local_today(ctx.config.tz))
        if not d:
            return await message.answer("Формат: ДД.ММ.ГГГГ", reply_markup=cancel_kb())
        old, new = deal.deal_date, d
        deal.deal_date = d
    else:
        old, new = deal.comment, (None if raw == "-" else raw[:1000])
        deal.comment = new
    if deal.property_id:
        await property_service.add_history(session, deal.property_id, "updated", actor.user_id, f"{deal.code}.{field}", old, new)
    await state.clear()
    logger.info("Сделка %s: изменено поле %s", deal.code, field)
    await message.answer("✅ Сохранено.")
    await message.answer(cards.deal_card(deal, ctx.config.tz), reply_markup=deal_keyboard(actor, deal))


@router.callback_query(DealCB.filter(F.action == "cancel"))
async def cb_cancel_confirm(cq: CallbackQuery, callback_data: DealCB, actor: Actor, session: AsyncSession) -> None:
    if not can_edit_deal(actor):
        return await deny(cq)
    deal = await _load(cq, session, callback_data.id)
    if not deal:
        return
    b = InlineKeyboardBuilder()
    b.row(btn("❌ Отменить и вернуть объект в работу", DealCB(action="cancely", id=deal.id, arg="restore")))
    b.row(btn("❌ Только отменить сделку", DealCB(action="cancely", id=deal.id, arg="keep")))
    b.row(btn("⬅️ Назад", DealCB(action="open", id=deal.id)))
    await show(cq, f"Отменить сделку {deal.code}? Она перестанет учитываться в статистике.", b.as_markup())
    await ack(cq)


@router.callback_query(DealCB.filter(F.action == "cancely"))
async def cb_cancel(cq: CallbackQuery, callback_data: DealCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    if not can_edit_deal(actor):
        return await deny(cq)
    deal = await _load(cq, session, callback_data.id)
    if not deal:
        return
    prop = deal.prop
    was_archived = prop.is_archived if prop else False
    try:
        await deal_service.cancel_deal(session, deal, actor.user_id, restore_property=callback_data.arg == "restore")
    except DealError as exc:
        return await ack(cq, str(exc), alert=True)
    await session.flush()
    await workchat_service.mark_deal_cancelled(ctx, rs, deal)
    if prop is not None:
        if was_archived and not prop.is_archived:
            await workchat_service.mark_unarchived(ctx, rs, prop)
        await workchat_service.refresh_base_card(ctx, rs, prop)
    await ack(cq, "Сделка отменена")
    await show(cq, cards.deal_card(deal, ctx.config.tz), deal_keyboard(actor, deal))
