"""Объекты: список, карточка, добавление, импорт по ссылке, изменение, статусы, публикация, архив."""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import OfferType, Property, PropertyStatus
from bot.database.repositories import users as users_repo
from bot.handlers.states import PropertyAdd, PropertyEdit, PropertyImport, PropertySearch
from bot.keyboards.callbacks import DealCB, FormCB, LeadCB, PropCB
from bot.keyboards.menus import btn, cancel_kb, choices_kb, home_btn, pager_row, skip_cancel_kb, url_btn
from bot.parsers.base import ParseError
from bot.services import (
    cards,
    parser_service,
    property_actions,
    property_service,
    publication_service,
    workchat_service,
)
from bot.services.auth import (
    Actor,
    can_assign_property,
    can_delete_property,
    can_edit_property,
    can_publish_property,
    can_view_property,
)
from bot.services.context import AppContext
from bot.services.property_service import EDITABLE_FIELDS, PAGE_SIZE, PROPERTY_TYPES, STATUS_LABELS, PropertyError
from bot.services.settings_service import RuntimeSettings
from bot.utils.telegram import safe_call
from bot.utils.text import esc, find_phone, parse_decimal, parse_int, parse_money
from bot.utils.ui import ack, deny, show

logger = logging.getLogger(__name__)
router = Router(name="properties")
router.message.filter(F.chat.type == "private")

SCOPE_TITLES = {"active": "🟢 Активные", "published": "📢 Опубликованные", "archive": "📦 Архив", "all": "📚 Все"}


# ======================= меню и списки =======================

def properties_menu_kb(actor: Actor) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(btn("➕ Добавить", PropCB(action="add")), btn("🔗 Импорт по ссылке", PropCB(action="imp")))
    b.row(btn("🔎 Найти объект", PropCB(action="search")))
    if actor.is_owner:
        b.row(btn(SCOPE_TITLES["active"], PropCB(action="list", arg="active")), btn(SCOPE_TITLES["published"], PropCB(action="list", arg="published")))
        b.row(btn(SCOPE_TITLES["archive"], PropCB(action="list", arg="archive")), btn(SCOPE_TITLES["all"], PropCB(action="list", arg="all")))
    else:
        b.row(btn("🟢 Мои активные", PropCB(action="list", arg="m:active")), btn("📦 Мой архив", PropCB(action="list", arg="m:archive")))
        b.row(btn("📚 Вся база агентства", PropCB(action="list", arg="active")))
    return b.as_markup()


async def show_properties_menu(event: Message | CallbackQuery, actor: Actor, session: AsyncSession) -> None:
    if actor.is_owner:
        title = "🏠 <b>Объекты</b>\n\nУправление общей базой объектов агентства."
    else:
        title = "🏠 <b>Мои объекты</b>"
    await show(event, title, properties_menu_kb(actor))


async def show_list(
    event: Message | CallbackQuery, actor: Actor, session: AsyncSession, scope_arg: str, page: int = 0, query: str | None = None
) -> None:
    mine = scope_arg.startswith("m:")
    scope = scope_arg[2:] if mine else scope_arg
    responsible = actor.user_id if mine else None
    items, total = await property_service.list_properties(session, scope=scope, responsible_id=responsible, page=page, query=query)
    if query:
        title = f"🔎 Поиск «{esc(query)}»: найдено {total}"
    else:
        title = f"{'Мои: ' if mine else ''}{SCOPE_TITLES.get(scope, scope)} — {total}"
    b = InlineKeyboardBuilder()
    for prop in items:
        b.row(btn(cards.short_line(prop)[:60], PropCB(action="open", id=prop.id)))
    if not query:
        nav = pager_row(lambda p: PropCB(action="list", arg=scope_arg, page=p), page, total, PAGE_SIZE)
        if nav:
            b.row(*nav)
    b.row(btn("⬅️ Объекты", PropCB(action="menu")), home_btn())
    text = title if items else title + "\n\nНичего не найдено."
    await show(event, text, b.as_markup())


@router.callback_query(PropCB.filter(F.action == "menu"))
async def cb_menu(cq: CallbackQuery, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    if not actor.is_staff:
        return await deny(cq)
    await state.clear()
    await show_properties_menu(cq, actor, session)
    await ack(cq)


@router.callback_query(PropCB.filter(F.action == "list"))
async def cb_list(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession) -> None:
    if not actor.is_staff:
        return await deny(cq)
    await show_list(cq, actor, session, callback_data.arg or "active", callback_data.page)
    await ack(cq)


@router.callback_query(PropCB.filter(F.action == "search"))
async def cb_search(cq: CallbackQuery, actor: Actor, state: FSMContext) -> None:
    if not actor.is_staff:
        return await deny(cq)
    await state.set_state(PropertySearch.query)
    await show(cq, "🔎 Введите номер объекта (например <code>125</code> или <code>OBJ-000125</code>), адрес, район или часть описания:", cancel_kb())
    await ack(cq)


@router.message(PropertySearch.query, F.text)
async def msg_search(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    if not actor.is_staff:
        return await deny(message)
    await state.clear()
    query = message.text.strip()[:100]
    code = property_service.parse_property_code(query)
    if code:
        prop = await property_service.get_property(session, code)
        if prop:
            return await show_card(message, actor, session, ctx, prop, rs)
    await show_list(message, actor, session, "all", 0, query=query)


# ======================= карточка =======================

def card_keyboard(ctx: AppContext, actor: Actor, rs: RuntimeSettings, prop: Property) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    edit = can_edit_property(actor, prop)
    if edit and not prop.is_archived:
        b.row(btn("✏️ Изменить", PropCB(action="edit", id=prop.id)), btn("🖼 Фото", PropCB(action="photos", id=prop.id)))
    if edit:
        b.row(btn("🔁 Статус", PropCB(action="st", id=prop.id)))
    if can_assign_property(actor):
        b.row(btn("👤 Ответственный", PropCB(action="asg", id=prop.id)))
    if can_publish_property(actor, prop, rs.realtor_can_publish) and not prop.is_archived:
        if prop.public_message_id:
            b.row(btn("🔄 Обновить публикацию", PropCB(action="pub", id=prop.id)), btn("🚫 Снять публикацию", PropCB(action="unpub", id=prop.id)))
        else:
            b.row(btn("📢 Опубликовать в канал", PropCB(action="pub", id=prop.id)))
    post_url = workchat_service.public_post_url(ctx, prop)
    if post_url:
        b.row(url_btn("👁 Открыть публикацию", post_url))
    if edit:
        if prop.is_archived:
            b.row(btn("↩️ Вернуть из архива", PropCB(action="unarch", id=prop.id)))
        else:
            b.row(btn("📦 В архив", PropCB(action="arch", id=prop.id)), btn("🤝 Сделка", DealCB(action="new", arg=f"p{prop.id}")))
    row = [btn("📜 История", PropCB(action="hist", id=prop.id))]
    if actor.is_owner or edit:
        row.append(btn("📋 Заявки", LeadCB(action="plist", id=prop.id)))
    b.row(*row)
    if can_delete_property(actor):
        b.row(btn("🗑 Удалить", PropCB(action="del", id=prop.id)))
    b.row(btn("⬅️ Объекты", PropCB(action="menu")), home_btn())
    return b.as_markup()


async def show_card(
    event: Message | CallbackQuery, actor: Actor, session: AsyncSession, ctx: AppContext, prop: Property,
    rs: RuntimeSettings, new: bool = False,
) -> None:
    if not can_view_property(actor, prop):
        return await deny(event)
    summary = await property_service.get_summary(session, prop.id)
    text = cards.internal_card(prop, ctx.config.tz, summary, show_private=True)
    await show(event, text[:4096], card_keyboard(ctx, actor, rs, prop), new=new)


async def load_prop(event: Message | CallbackQuery, session: AsyncSession, prop_id: int) -> Property | None:
    prop = await property_service.get_property(session, prop_id)
    if prop is None:
        if isinstance(event, CallbackQuery):
            await event.answer("Объект не найден (возможно, удален).", show_alert=True)
        else:
            await event.answer("Объект не найден.")
    return prop


@router.callback_query(PropCB.filter(F.action == "open"))
async def cb_open(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    if not actor.is_staff:
        return await deny(cq)
    await state.clear()
    prop = await load_prop(cq, session, callback_data.id)
    if prop:
        await show_card(cq, actor, session, ctx, prop, rs)
        await ack(cq)


async def open_by_id(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, prop_id: int) -> None:
    """Открытие карточки по deep-link из рабочего чата."""
    if not actor.is_staff:
        return await deny(message)
    prop = await load_prop(message, session, prop_id)
    if prop:
        await show_card(message, actor, session, ctx, prop, rs)


# ======================= история =======================

@router.callback_query(PropCB.filter(F.action == "hist"))
async def cb_history(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession, ctx: AppContext) -> None:
    if not actor.is_staff:
        return await deny(cq)
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    history = await property_service.get_history(session, prop.id)
    kb = InlineKeyboardBuilder()
    kb.row(btn("⬅️ К объекту", PropCB(action="open", id=prop.id)))
    await show(cq, cards.history_text(prop, history, ctx.config.tz), kb.as_markup())
    await ack(cq)


# ======================= изменение полей =======================

EDIT_ORDER = [
    "title", "offer_type", "property_type", "price", "currency", "rooms", "area", "floor", "floors_total",
    "city", "district", "address", "coords", "show_exact_location", "description", "owner_name", "owner_phone", "internal_comment",
]


@router.callback_query(PropCB.filter(F.action == "edit"))
async def cb_edit_menu(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession) -> None:
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    if not can_edit_property(actor, prop):
        return await deny(cq)
    b = InlineKeyboardBuilder()
    for key in EDIT_ORDER:
        b.add(btn(EDITABLE_FIELDS[key].label, PropCB(action="ef", id=prop.id, arg=key)))
    b.adjust(2)
    b.row(btn("⬅️ К объекту", PropCB(action="open", id=prop.id)))
    await show(cq, f"✏️ <b>{prop.code}</b>: что изменить?", b.as_markup())
    await ack(cq)


async def open_edit_by_id(message: Message, actor: Actor, session: AsyncSession, prop_id: int) -> None:
    prop = await load_prop(message, session, prop_id)
    if not prop:
        return
    if not can_edit_property(actor, prop):
        return await deny(message)
    b = InlineKeyboardBuilder()
    for key in EDIT_ORDER:
        b.add(btn(EDITABLE_FIELDS[key].label, PropCB(action="ef", id=prop.id, arg=key)))
    b.adjust(2)
    b.row(btn("⬅️ К объекту", PropCB(action="open", id=prop.id)))
    await message.answer(f"✏️ <b>{prop.code}</b>: что изменить?", reply_markup=b.as_markup())


@router.callback_query(PropCB.filter(F.action == "ef"))
async def cb_edit_field(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    if not can_edit_property(actor, prop) or callback_data.arg not in EDITABLE_FIELDS:
        return await deny(cq)
    spec = EDITABLE_FIELDS[callback_data.arg]
    await state.set_state(PropertyEdit.value)
    await state.update_data(prop_id=prop.id, field=callback_data.arg)
    current = getattr(prop, callback_data.arg, None) if callback_data.arg != "coords" else (
        f"{prop.latitude}, {prop.longitude}" if prop.latitude is not None else None
    )
    if hasattr(current, "value"):
        current = current.value
    cur_text = esc(str(current)[:300]) if current not in (None, "") else "—"
    await show(
        cq,
        f"✏️ <b>{spec.label}</b>\nСейчас: {cur_text}\n\nВведите новое значение ({esc(spec.hint)}).\n«-» — очистить поле.",
        cancel_kb(),
    )
    await ack(cq)


@router.message(PropertyEdit.value, F.text)
async def msg_edit_value(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    data = await state.get_data()
    prop = await load_prop(message, session, data.get("prop_id", 0))
    if not prop:
        return await state.clear()
    if not can_edit_property(actor, prop):
        await state.clear()
        return await deny(message)
    try:
        await property_service.update_field(session, prop, data["field"], message.text, actor.user_id)
    except PropertyError as exc:
        return await message.answer(f"⚠️ {esc(exc)}\nПопробуйте еще раз.", reply_markup=cancel_kb())
    await state.clear()
    await session.flush()
    await workchat_service.refresh_base_card(ctx, rs, prop)
    note = "\n\nℹ️ Объект опубликован — нажмите «🔄 Обновить публикацию», чтобы изменения попали в канал." if prop.public_message_id else ""
    await message.answer(f"✅ Сохранено.{note}")
    await show_card(message, actor, session, ctx, prop, rs)


# ======================= фото =======================

@router.callback_query(PropCB.filter(F.action == "photos"))
async def cb_photos(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    if not can_edit_property(actor, prop):
        return await deny(cq)
    await state.set_state(PropertyEdit.photos)
    await state.update_data(prop_id=prop.id, photos=[])
    kb = InlineKeyboardBuilder()
    kb.row(btn("✅ Готово", FormCB(field="photos_done")), btn("🗑 Удалить все фото", FormCB(field="photos_clear")))
    kb.row(btn("✖️ Отмена", FormCB(field="cancel")))
    await show(
        cq,
        f"🖼 Сейчас фото: {len(prop.photos or [])}.\n\nОтправьте новые фото (можно альбомом) — они <b>добавятся</b> к существующим. "
        "Затем нажмите «Готово».",
        kb.as_markup(),
    )
    await ack(cq)


@router.message(PropertyEdit.photos, F.photo)
async def msg_photo_add(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos = list(data.get("photos", []))
    photos.append("tg:" + message.photo[-1].file_id)
    await state.update_data(photos=photos[:30])
    if len(photos) == 1 or not message.media_group_id:
        await message.answer(f"📥 Получено фото: {len(photos)}. Отправьте еще или нажмите «Готово» выше.")


@router.callback_query(PropertyEdit.photos, FormCB.filter(F.field.in_({"photos_done", "photos_clear"})))
async def cb_photos_done(cq: CallbackQuery, callback_data: FormCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    data = await state.get_data()
    prop = await load_prop(cq, session, data.get("prop_id", 0))
    await state.clear()
    if not prop:
        return
    if not can_edit_property(actor, prop):
        return await deny(cq)
    if callback_data.field == "photos_clear":
        await property_service.set_photos(session, prop, [], actor.user_id)
    else:
        await property_service.set_photos(session, prop, list(prop.photos or []) + data.get("photos", []), actor.user_id)
    await ack(cq, "Фото сохранены")
    await show_card(cq, actor, session, ctx, prop, rs)


# ======================= статус / архив =======================

@router.callback_query(PropCB.filter(F.action == "st"))
async def cb_status_menu(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession) -> None:
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    if not can_edit_property(actor, prop):
        return await deny(cq)
    b = InlineKeyboardBuilder()
    for status in PropertyStatus:
        if status == prop.status:
            continue
        b.add(btn(STATUS_LABELS[status], PropCB(action="ss", id=prop.id, arg=status.value)))
    b.adjust(2)
    b.row(btn("⬅️ К объекту", PropCB(action="open", id=prop.id)))
    await show(
        cq,
        f"🔁 <b>{prop.code}</b>: текущий статус — {STATUS_LABELS[prop.status]}\n\n"
        "Проданные/сданные/неактуальные объекты автоматически попадают в архив. «Опубликован» — публикует объект в канал.",
        b.as_markup(),
    )
    await ack(cq)


@router.callback_query(PropCB.filter(F.action == "ss"))
async def cb_set_status(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    try:
        status = PropertyStatus(callback_data.arg)
    except ValueError:
        return await ack(cq, "Неизвестный статус", alert=True)
    if not can_edit_property(actor, prop):
        return await deny(cq)
    if status == PropertyStatus.PUBLISHED and not can_publish_property(actor, prop, rs.realtor_can_publish):
        return await deny(cq)
    try:
        await property_actions.apply_status(ctx, session, rs, actor, prop, status)
    except publication_service.PublicationError as exc:
        return await ack(cq, f"⚠️ {exc}", alert=True)
    await ack(cq, f"Статус: {STATUS_LABELS[prop.status]}")
    await show_card(cq, actor, session, ctx, prop, rs)


@router.callback_query(PropCB.filter(F.action == "arch"))
async def cb_archive_confirm(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession) -> None:
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    if not can_edit_property(actor, prop):
        return await deny(cq)
    await show(cq, *archive_confirm(prop))
    await ack(cq)


def archive_confirm(prop: Property) -> tuple[str, InlineKeyboardMarkup]:
    b = InlineKeyboardBuilder()
    b.row(btn("📦 Архив (вручную)", PropCB(action="ss", id=prop.id, arg=PropertyStatus.ARCHIVED.value)))
    b.row(btn("💰 Продан", PropCB(action="ss", id=prop.id, arg=PropertyStatus.SOLD.value)), btn("🔑 Сдан", PropCB(action="ss", id=prop.id, arg=PropertyStatus.RENTED.value)))
    b.row(btn("⚪ Неактуален", PropCB(action="ss", id=prop.id, arg=PropertyStatus.INACTIVE.value)))
    b.row(btn("⬅️ Отмена", PropCB(action="open", id=prop.id)))
    return (
        f"📦 Перенести <b>{prop.code}</b> в архив?\n\nОбъект останется в базе вместе с историей, заявками и сделками. Выберите причину:",
        b.as_markup(),
    )


async def open_archive_by_id(message: Message, actor: Actor, session: AsyncSession, prop_id: int) -> None:
    prop = await load_prop(message, session, prop_id)
    if not prop:
        return
    if not can_edit_property(actor, prop):
        return await deny(message)
    if prop.is_archived:
        return await message.answer(f"{prop.code} уже в архиве.")
    text, kb = archive_confirm(prop)
    await message.answer(text, reply_markup=kb)


@router.callback_query(PropCB.filter(F.action == "unarch"))
async def cb_unarchive(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    if not can_edit_property(actor, prop):
        return await deny(cq)
    await property_actions.apply_status(ctx, session, rs, actor, prop, PropertyStatus.ACTIVE)
    await ack(cq, "Объект возвращен из архива")
    await show_card(cq, actor, session, ctx, prop, rs)


# ======================= публикация =======================

@router.callback_query(PropCB.filter(F.action == "pub"))
async def cb_publish(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    if not can_publish_property(actor, prop, rs.realtor_can_publish):
        return await deny(cq)
    await ack(cq, "⏳ Публикую…")
    try:
        await publication_service.publish(ctx, session, rs, prop, actor.user_id)
    except publication_service.PublicationError as exc:
        await show(cq, f"⚠️ Не удалось опубликовать: {esc(exc)}", InlineKeyboardMarkup(inline_keyboard=[[btn("⬅️ К объекту", PropCB(action="open", id=prop.id))]]), new=True)
        return
    await session.flush()
    await workchat_service.refresh_base_card(ctx, rs, prop)
    await show_card(cq, actor, session, ctx, prop, rs, new=True)


@router.callback_query(PropCB.filter(F.action == "unpub"))
async def cb_unpublish(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    if not can_publish_property(actor, prop, rs.realtor_can_publish):
        return await deny(cq)
    await publication_service.unpublish(ctx, session, prop, actor.user_id)
    await session.flush()
    await workchat_service.refresh_base_card(ctx, rs, prop)
    await ack(cq, "Публикация снята")
    await show_card(cq, actor, session, ctx, prop, rs)


# ======================= ответственный =======================

@router.callback_query(PropCB.filter(F.action == "asg"))
async def cb_assign_menu(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession) -> None:
    if not can_assign_property(actor):
        return await deny(cq)
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    b = InlineKeyboardBuilder()
    for user in await users_repo.list_staff(session, include_owner=True):
        mark = "✅ " if user.id == prop.responsible_realtor_id else ""
        b.add(btn(f"{mark}{user.display_name}", PropCB(action="asgs", id=prop.id, arg=str(user.id))))
    b.adjust(2)
    b.row(btn("⬅️ К объекту", PropCB(action="open", id=prop.id)))
    await show(cq, f"👤 Ответственный за <b>{prop.code}</b>:", b.as_markup())
    await ack(cq)


@router.callback_query(PropCB.filter(F.action == "asgs"))
async def cb_assign_set(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    if not can_assign_property(actor):
        return await deny(cq)
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    realtor = await users_repo.get(session, parse_int(callback_data.arg) or 0)
    if realtor is None or not realtor.is_active:
        return await ack(cq, "Сотрудник не найден или заблокирован", alert=True)
    await property_service.assign_realtor(session, prop, realtor, actor.user_id)
    await workchat_service.refresh_base_card(ctx, rs, prop)
    if realtor.telegram_id != actor.telegram_id:
        await safe_call(
            "notify_assigned", ctx.bot.send_message, chat_id=realtor.telegram_id,
            text=f"👤 Вы назначены ответственным за объект\n{esc(cards.short_line(prop))}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("Открыть", PropCB(action="open", id=prop.id))]]),
        )
    await ack(cq, "Ответственный назначен")
    await show_card(cq, actor, session, ctx, prop, rs)


# ======================= удаление =======================

@router.callback_query(PropCB.filter(F.action == "del"))
async def cb_delete_confirm(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession) -> None:
    if not can_delete_property(actor):
        return await deny(cq)
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    b = InlineKeyboardBuilder()
    b.row(btn("🗑 Да, удалить навсегда", PropCB(action="dely", id=prop.id)))
    b.row(btn("📦 Лучше в архив", PropCB(action="arch", id=prop.id)), btn("⬅️ Отмена", PropCB(action="open", id=prop.id)))
    await show(
        cq,
        f"⚠️ Удалить <b>{prop.code}</b> безвозвратно?\n\nУдалятся история и публикация. Заявки останутся без привязки к объекту. "
        "Объекты со сделками удалить нельзя — используйте архив.",
        b.as_markup(),
    )
    await ack(cq)


@router.callback_query(PropCB.filter(F.action == "dely"))
async def cb_delete(cq: CallbackQuery, callback_data: PropCB, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    if not can_delete_property(actor):
        return await deny(cq)
    prop = await load_prop(cq, session, callback_data.id)
    if not prop:
        return
    code = prop.code
    try:
        await property_service.delete_property(session, prop)
    except PropertyError as exc:
        return await ack(cq, str(exc), alert=True)
    await publication_service.unpublish(ctx, session, prop, actor.user_id, record=False)
    await workchat_service.delete_cards(ctx, rs, prop)
    await ack(cq, "Удалено")
    await show(cq, f"🗑 Объект {code} удален.", InlineKeyboardMarkup(inline_keyboard=[[btn("⬅️ Объекты", PropCB(action="menu"))]]))


# ======================= добавление вручную =======================

OFFER_OPTIONS = [("🏷 Продажа", "sale"), ("🔑 Аренда", "rent")]


async def start_add(event: Message | CallbackQuery, actor: Actor, state: FSMContext) -> None:
    if not actor.is_staff:
        return await deny(event)
    await state.clear()
    await state.set_state(PropertyAdd.offer_type)
    await state.update_data(draft={"photos": []})
    await show(event, "➕ <b>Новый объект</b>\n\nШаг 1. Тип предложения:", choices_kb("offer", OFFER_OPTIONS), new=True)


@router.callback_query(PropCB.filter(F.action == "add"))
async def cb_add(cq: CallbackQuery, actor: Actor, state: FSMContext) -> None:
    await start_add(cq, actor, state)
    await ack(cq)


async def _draft(state: FSMContext) -> dict:
    return dict((await state.get_data()).get("draft", {}))


async def _save_draft(state: FSMContext, draft: dict) -> None:
    await state.update_data(draft=draft)


@router.callback_query(PropertyAdd.offer_type, FormCB.filter(F.field == "offer"))
async def add_offer(cq: CallbackQuery, callback_data: FormCB, state: FSMContext) -> None:
    draft = await _draft(state)
    draft["offer_type"] = "rent" if callback_data.value == "rent" else "sale"
    await _save_draft(state, draft)
    await state.set_state(PropertyAdd.property_type)
    await show(cq, "Шаг 2. Тип недвижимости:", choices_kb("ptype", [(t, t) for t in PROPERTY_TYPES], per_row=3))
    await ack(cq)


@router.callback_query(PropertyAdd.property_type, FormCB.filter(F.field == "ptype"))
async def add_ptype(cq: CallbackQuery, callback_data: FormCB, state: FSMContext) -> None:
    draft = await _draft(state)
    draft["property_type"] = callback_data.value[:64]
    await _save_draft(state, draft)
    await state.set_state(PropertyAdd.rooms)
    await show(cq, "Шаг 3. Количество комнат (число):", choices_kb("rooms", [(str(i), str(i)) for i in range(1, 6)], per_row=5, skip=True))
    await ack(cq)


async def _ask(event: Message | CallbackQuery, state: FSMContext, next_state, text: str, kb=None) -> None:
    await state.set_state(next_state)
    await show(event, text, kb or skip_cancel_kb(), new=isinstance(event, Message))


async def _after_rooms(event, state):
    await _ask(event, state, PropertyAdd.area, "Шаг 4. Площадь, м² (например 42 или 42.5):")


@router.callback_query(PropertyAdd.rooms, FormCB.filter(F.field.in_({"rooms", "skip"})))
async def add_rooms_btn(cq: CallbackQuery, callback_data: FormCB, state: FSMContext) -> None:
    if callback_data.field == "rooms":
        draft = await _draft(state)
        draft["rooms"] = int(callback_data.value)
        await _save_draft(state, draft)
    await _after_rooms(cq, state)
    await ack(cq)


@router.message(PropertyAdd.rooms, F.text)
async def add_rooms(message: Message, state: FSMContext) -> None:
    value = parse_int(message.text)
    if value is None or not 0 <= value <= 50:
        return await message.answer("Введите число комнат, например 2.", reply_markup=skip_cancel_kb())
    draft = await _draft(state)
    draft["rooms"] = value
    await _save_draft(state, draft)
    await _after_rooms(message, state)


@router.message(PropertyAdd.area, F.text)
async def add_area(message: Message, state: FSMContext) -> None:
    value = parse_decimal(message.text)
    if value is None or value <= 0:
        return await message.answer("Введите площадь числом, например 42.5", reply_markup=skip_cancel_kb())
    draft = await _draft(state)
    draft["area"] = str(value)
    await _save_draft(state, draft)
    await _ask(message, state, PropertyAdd.floor, "Шаг 5. Этаж / этажность (например 5/9):")


@router.message(PropertyAdd.floor, F.text)
async def add_floor(message: Message, state: FSMContext) -> None:
    m = re.fullmatch(r"\s*(\d{1,3})(?:\s*(?:/|из|з)\s*(\d{1,3}))?\s*", message.text)
    if not m:
        return await message.answer("Формат: 5/9 или просто 5", reply_markup=skip_cancel_kb())
    draft = await _draft(state)
    draft["floor"] = int(m.group(1))
    if m.group(2):
        draft["floors_total"] = int(m.group(2))
    await _save_draft(state, draft)
    await _ask(message, state, PropertyAdd.price, "Шаг 6. Цена (например <code>45000$</code>, <code>12000 грн</code>, <code>500 eur</code>):", cancel_kb())


@router.message(PropertyAdd.price, F.text)
async def add_price(message: Message, state: FSMContext, ctx: AppContext) -> None:
    money = parse_money(message.text, ctx.config.default_currency)
    if not money or money[0] <= 0:
        return await message.answer("Введите цену числом, например 45000$", reply_markup=cancel_kb())
    draft = await _draft(state)
    draft["price"], draft["currency"] = str(money[0]), money[1]
    await _save_draft(state, draft)
    default_city = ctx.config.default_city
    kb = choices_kb("city", [(default_city, default_city)], skip=True) if default_city else skip_cancel_kb()
    await _ask(message, state, PropertyAdd.city, "Шаг 7. Город:", kb)


async def _to_district(event, state):
    await _ask(event, state, PropertyAdd.district, "Шаг 8. Район (для хэштегов и карты):")


@router.callback_query(PropertyAdd.city, FormCB.filter(F.field == "city"))
async def add_city_btn(cq: CallbackQuery, callback_data: FormCB, state: FSMContext) -> None:
    draft = await _draft(state)
    draft["city"] = callback_data.value
    await _save_draft(state, draft)
    await _to_district(cq, state)
    await ack(cq)


@router.message(PropertyAdd.city, F.text)
async def add_city(message: Message, state: FSMContext) -> None:
    draft = await _draft(state)
    draft["city"] = message.text.strip()[:128]
    await _save_draft(state, draft)
    await _to_district(message, state)


@router.message(PropertyAdd.district, F.text)
async def add_district(message: Message, state: FSMContext) -> None:
    draft = await _draft(state)
    draft["district"] = message.text.strip()[:128]
    await _save_draft(state, draft)
    await _ask(message, state, PropertyAdd.address, "Шаг 9. Адрес (улица, дом). 🔒 В канале не публикуется:")


@router.message(PropertyAdd.address, F.text)
async def add_address(message: Message, state: FSMContext) -> None:
    draft = await _draft(state)
    draft["address"] = message.text.strip()[:256]
    await _save_draft(state, draft)
    await _ask(message, state, PropertyAdd.description, "Шаг 10. Описание для публикации:")


@router.message(PropertyAdd.description, F.text)
async def add_description(message: Message, state: FSMContext) -> None:
    draft = await _draft(state)
    draft["description"] = message.text.strip()[:4000]
    await _save_draft(state, draft)
    await _ask(message, state, PropertyAdd.owner_contact, "Шаг 11. 🔒 Контакт собственника (имя и телефон) — только для CRM:")


@router.message(PropertyAdd.owner_contact, F.text)
async def add_owner(message: Message, state: FSMContext) -> None:
    draft = await _draft(state)
    text = message.text.strip()[:200]
    phone = find_phone(text)
    draft["owner_phone"] = phone
    name = text.replace(phone, "").strip(" ,;-") if phone else text
    draft["owner_name"] = name[:128] or None
    await _save_draft(state, draft)
    await _to_photos(message, state)


async def _to_photos(event, state):
    kb = InlineKeyboardBuilder()
    kb.row(btn("✅ Готово", FormCB(field="done")), btn("⏭ Без фото", FormCB(field="done")))
    kb.row(btn("✖️ Отмена", FormCB(field="cancel")))
    await state.set_state(PropertyAdd.photos)
    await show(event, "Шаг 12. Отправьте фото объекта (можно альбомом, до 30), затем нажмите «Готово».", kb.as_markup(), new=isinstance(event, Message))


@router.message(PropertyAdd.photos, F.photo)
async def add_photo(message: Message, state: FSMContext) -> None:
    draft = await _draft(state)
    photos = list(draft.get("photos", []))
    photos.append("tg:" + message.photo[-1].file_id)
    draft["photos"] = photos[:30]
    await _save_draft(state, draft)
    if len(photos) == 1 or not message.media_group_id:
        await message.answer(f"📥 Фото: {len(photos)}. Отправьте еще или нажмите «Готово».")


# «Пропустить» на текстовых шагах
_SKIP_NEXT = {
    PropertyAdd.area.state: (PropertyAdd.floor, "Шаг 5. Этаж / этажность (например 5/9):"),
    PropertyAdd.floor.state: (PropertyAdd.price, "Шаг 6. Цена (например <code>45000$</code>):"),
    PropertyAdd.city.state: (PropertyAdd.district, "Шаг 8. Район:"),
    PropertyAdd.district.state: (PropertyAdd.address, "Шаг 9. Адрес (улица, дом). 🔒 В канале не публикуется:"),
    PropertyAdd.address.state: (PropertyAdd.description, "Шаг 10. Описание для публикации:"),
    PropertyAdd.description.state: (PropertyAdd.owner_contact, "Шаг 11. 🔒 Контакт собственника (имя и телефон):"),
}


@router.callback_query(PropertyAdd.area, FormCB.filter(F.field == "skip"))
@router.callback_query(PropertyAdd.floor, FormCB.filter(F.field == "skip"))
@router.callback_query(PropertyAdd.city, FormCB.filter(F.field == "skip"))
@router.callback_query(PropertyAdd.district, FormCB.filter(F.field == "skip"))
@router.callback_query(PropertyAdd.address, FormCB.filter(F.field == "skip"))
@router.callback_query(PropertyAdd.description, FormCB.filter(F.field == "skip"))
async def add_skip(cq: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    nxt = _SKIP_NEXT.get(current)
    if nxt:
        kb = cancel_kb() if nxt[0] == PropertyAdd.price else skip_cancel_kb()
        await _ask(cq, state, nxt[0], nxt[1], kb)
    await ack(cq)


@router.callback_query(PropertyAdd.owner_contact, FormCB.filter(F.field == "skip"))
async def add_owner_skip(cq: CallbackQuery, state: FSMContext) -> None:
    await _to_photos(cq, state)
    await ack(cq)


def draft_values(draft: dict) -> dict:
    values = dict(draft)
    for key in ("price", "area"):
        if values.get(key) not in (None, ""):
            values[key] = Decimal(str(values[key]))
    return values


def confirm_kb(prefix: str, can_publish: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(btn("✅ Сохранить", FormCB(field=f"{prefix}save")))
    if can_publish:
        b.row(btn("📢 Сохранить и опубликовать", FormCB(field=f"{prefix}savepub")))
    b.row(btn("✏️ Исправить поле", FormCB(field=f"{prefix}fix")), btn("✖️ Отмена", FormCB(field="cancel")))
    return b.as_markup()


def _can_publish_new(actor: Actor, rs: RuntimeSettings, ctx: AppContext) -> bool:
    return bool(ctx.config.public_channel_id) and (actor.is_owner or rs.realtor_can_publish)


@router.callback_query(PropertyAdd.photos, FormCB.filter(F.field == "done"))
async def add_photos_done(cq: CallbackQuery, state: FSMContext, actor: Actor, ctx: AppContext, rs: RuntimeSettings) -> None:
    draft = await _draft(state)
    await state.set_state(PropertyAdd.confirm)
    await show(cq, cards.preview_card(draft_values(draft)), confirm_kb("add", _can_publish_new(actor, rs, ctx)), new=True)
    await ack(cq)


@router.callback_query(PropertyAdd.confirm, FormCB.filter(F.field.in_({"addsave", "addsavepub"})))
@router.callback_query(PropertyImport.confirm, FormCB.filter(F.field.in_({"impsave", "impsavepub"})))
async def save_draft(cq: CallbackQuery, callback_data: FormCB, state: FSMContext, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    if not actor.is_staff:
        await state.clear()
        return await deny(cq)
    draft = await _draft(state)
    if not draft:
        await state.clear()
        return await ack(cq, "Черновик устарел, начните заново", alert=True)
    await state.clear()
    responsible = None if actor.is_owner else actor.user_id
    prop = await property_service.create_property(session, draft_values(draft), created_by_id=actor.user_id, responsible_id=responsible)
    error = await property_actions.after_created(ctx, session, rs, actor, prop)
    if callback_data.field.endswith("savepub") and not prop.public_message_id and error is None:
        try:
            await publication_service.publish(ctx, session, rs, prop, actor.user_id)
            await workchat_service.refresh_base_card(ctx, rs, prop)
        except publication_service.PublicationError as exc:
            error = str(exc)
    await ack(cq, f"Объект {prop.code} сохранен")
    note = f"\n⚠️ Публикация: {esc(error)}" if error else ""
    await show(cq, f"✅ Объект <b>{prop.code}</b> добавлен в базу.{note}", None)
    await show_card(cq, actor, session, ctx, prop, rs, new=True)


# ======================= импорт по ссылке =======================

async def start_import(event: Message | CallbackQuery, actor: Actor, state: FSMContext) -> None:
    if not actor.is_staff:
        return await deny(event)
    await state.clear()
    await state.set_state(PropertyImport.url)
    await show(
        event,
        "🔗 <b>Импорт объекта</b>\n\nОтправьте ссылку на объявление (OLX, DOM.RIA или другой сайт).",
        cancel_kb(),
        new=True,
    )


@router.callback_query(PropCB.filter(F.action == "imp"))
async def cb_import(cq: CallbackQuery, actor: Actor, state: FSMContext) -> None:
    await start_import(cq, actor, state)
    await ack(cq)


@router.message(PropertyImport.url, F.text)
async def import_url(message: Message, state: FSMContext, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings) -> None:
    if not actor.is_staff:
        await state.clear()
        return await deny(message)
    url = parser_service.extract_url(message.text)
    if not url:
        return await message.answer("Это не похоже на ссылку. Отправьте URL объявления (https://…).", reply_markup=cancel_kb())
    wait = await message.answer("⏳ Получаю данные объявления…")
    try:
        parsed = await parser_service.import_from_url(
            url, default_city=ctx.config.default_city, default_currency=ctx.config.default_currency
        )
    except ParseError as exc:
        await wait.edit_text(f"⚠️ {esc(exc)}")
        kb = InlineKeyboardBuilder()
        kb.row(btn("➕ Добавить вручную", PropCB(action="add")), btn("🔗 Другая ссылка", PropCB(action="imp")))
        await message.answer("Что дальше?", reply_markup=kb.as_markup())
        await state.clear()
        return
    draft = {k: v for k, v in parsed.to_dict().items() if v not in (None, "", [], {})}
    for key in ("price", "area"):
        if key in draft:
            draft[key] = str(draft[key])
    await state.update_data(draft=draft)
    await state.set_state(PropertyImport.confirm)
    dup = await session.scalar(select(Property).where(Property.source_url == parsed.source_url).limit(1))
    warn = f"\n\n⚠️ Эта ссылка уже есть в базе: <b>{dup.code}</b>" if dup else ""
    await wait.delete()
    await message.answer(cards.preview_card(draft_values(draft)) + warn, reply_markup=confirm_kb("imp", _can_publish_new(actor, rs, ctx)))


FIX_FIELDS = ["title", "offer_type", "property_type", "price", "currency", "rooms", "area", "floor", "floors_total", "city", "district", "address", "description", "owner_name", "owner_phone"]


@router.callback_query(PropertyAdd.confirm, FormCB.filter(F.field == "addfix"))
@router.callback_query(PropertyImport.confirm, FormCB.filter(F.field == "impfix"))
async def fix_menu(cq: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    await state.update_data(return_state=current)
    b = InlineKeyboardBuilder()
    for key in FIX_FIELDS:
        b.add(btn(EDITABLE_FIELDS[key].label, FormCB(field="fixf", value=key)))
    b.adjust(2)
    b.row(btn("✖️ Отмена", FormCB(field="cancel")))
    await show(cq, "Какое поле исправить?", b.as_markup())
    await ack(cq)


@router.callback_query(PropertyAdd.confirm, FormCB.filter(F.field == "fixf"))
@router.callback_query(PropertyImport.confirm, FormCB.filter(F.field == "fixf"))
async def fix_field(cq: CallbackQuery, callback_data: FormCB, state: FSMContext) -> None:
    if callback_data.value not in FIX_FIELDS:
        return await ack(cq)
    spec = EDITABLE_FIELDS[callback_data.value]
    await state.update_data(fix_field=callback_data.value)
    await state.set_state(PropertyImport.edit_value)
    await show(cq, f"✏️ {spec.label}: введите значение ({esc(spec.hint)}), «-» — очистить.", cancel_kb())
    await ack(cq)


@router.message(PropertyImport.edit_value, F.text)
async def fix_value(message: Message, state: FSMContext, actor: Actor, ctx: AppContext, rs: RuntimeSettings) -> None:
    data = await state.get_data()
    field = data.get("fix_field")
    spec = EDITABLE_FIELDS.get(field or "")
    if spec is None:
        await state.clear()
        return
    try:
        value = spec.parser(message.text)
    except PropertyError as exc:
        return await message.answer(f"⚠️ {esc(exc)}", reply_markup=cancel_kb())
    draft = dict(data.get("draft", {}))
    if isinstance(value, OfferType):
        value = value.value
    if isinstance(value, Decimal):
        value = str(value)
    draft[field] = value
    await state.update_data(draft=draft)
    return_state = data.get("return_state") or PropertyImport.confirm.state
    await state.set_state(return_state)
    prefix = "add" if return_state == PropertyAdd.confirm.state else "imp"
    await message.answer(cards.preview_card(draft_values(draft)), reply_markup=confirm_kb(prefix, _can_publish_new(actor, rs, ctx)))

