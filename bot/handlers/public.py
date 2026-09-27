"""Клиентская часть: запись на просмотр из публичного канала (deep-link ?start=lead_<id>).

Все тексты для клиента — на украинском языке."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.states import LeadBooking
from bot.keyboards.callbacks import FormCB
from bot.keyboards.menus import CANCEL_TEXT_UK, btn, choices_kb
from bot.services import cards, lead_actions, lead_service, property_service
from bot.services.auth import Actor
from bot.services.context import AppContext
from bot.services.settings_service import RuntimeSettings
from bot.utils.text import esc, find_phone
from bot.utils.ui import ack

logger = logging.getLogger(__name__)
router = Router(name="public")
router.message.filter(F.chat.type == "private")

SKIP_PHONE = "Пропустити"
TIME_OPTIONS = [("Сьогодні", "Сьогодні"), ("Завтра", "Завтра"), ("У вихідні", "У вихідні"), ("Будь-коли", "Будь-коли")]

CLIENT_HELP = (
    "👋 Вітаємо! Це бот агентства нерухомості.\n\n"
    "Щоб записатися на перегляд, відкрийте оголошення в нашому каналі та натисніть «📅 Записатися на перегляд»."
)


async def start_booking(message: Message, state: FSMContext, session: AsyncSession, property_id: int) -> None:
    prop = await property_service.get_property(session, property_id)
    if prop is None or prop.is_archived:
        await message.answer("😔 Цей об'єкт вже неактуальний. Перегляньте інші пропозиції в нашому каналі.")
        return
    await state.clear()
    await state.set_state(LeadBooking.name)
    await state.update_data(property_id=prop.id)
    name = message.from_user.full_name if message.from_user else ""
    kb = choices_kb("bname", [(f"✅ {name}", "tg")], per_row=1, cancel_text=CANCEL_TEXT_UK) if name else None
    await message.answer(
        "📅 <b>Запис на перегляд</b>\n\n"
        f"{cards.public_caption(prop, None, limit=700)}\n\n"
        "Як до вас звертатися? Напишіть ім'я або натисніть кнопку.",
        reply_markup=kb,
    )


async def _ask_phone(event, state: FSMContext) -> None:
    await state.set_state(LeadBooking.phone)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Надіслати мій номер", request_contact=True)], [KeyboardButton(text=SKIP_PHONE)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer("📞 Залиште номер телефону для зв'язку (або натисніть «Пропустити» — рієлтор напише вам у Telegram):", reply_markup=kb)


@router.callback_query(LeadBooking.name, FormCB.filter(F.field == "bname"))
async def booking_name_tg(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(client_name=(cq.from_user.full_name or "")[:128])
    await _ask_phone(cq, state)
    await ack(cq)


@router.message(LeadBooking.name, F.text)
async def booking_name(message: Message, state: FSMContext) -> None:
    await state.update_data(client_name=message.text.strip()[:128])
    await _ask_phone(message, state)


@router.message(LeadBooking.phone, F.contact)
async def booking_contact(message: Message, state: FSMContext) -> None:
    await state.update_data(phone=message.contact.phone_number[:64])
    await _ask_time(message, state)


@router.message(LeadBooking.phone, F.text)
async def booking_phone(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text != SKIP_PHONE:
        phone = find_phone(text)
        if not phone:
            return await message.answer("Це не схоже на номер телефону. Введіть номер, наприклад +380 67 123 45 67, або натисніть «Пропустити».")
        await state.update_data(phone=phone[:64])
    await _ask_time(message, state)


async def _ask_time(message: Message, state: FSMContext) -> None:
    await state.set_state(LeadBooking.time)
    await message.answer("👌", reply_markup=ReplyKeyboardRemove())
    await message.answer("🕒 Коли вам зручно переглянути об'єкт? Оберіть або напишіть дату й час:", reply_markup=choices_kb("btime", TIME_OPTIONS, cancel_text=CANCEL_TEXT_UK))


@router.callback_query(LeadBooking.time, FormCB.filter(F.field == "btime"))
async def booking_time_btn(cq: CallbackQuery, callback_data: FormCB, state: FSMContext) -> None:
    await state.update_data(preferred_time=callback_data.value[:128])
    await _ask_comment(cq.message, state)
    await ack(cq)


@router.message(LeadBooking.time, F.text)
async def booking_time(message: Message, state: FSMContext) -> None:
    await state.update_data(preferred_time=message.text.strip()[:128])
    await _ask_comment(message, state)


async def _ask_comment(message: Message, state: FSMContext) -> None:
    await state.set_state(LeadBooking.comment)
    await message.answer("💬 Коментар або запитання (необов'язково):", reply_markup=choices_kb("bskip", [("⏭ Без коментаря", "1")], per_row=1, cancel_text=CANCEL_TEXT_UK))


@router.callback_query(LeadBooking.comment, FormCB.filter(F.field == "bskip"))
async def booking_comment_skip(cq: CallbackQuery, state: FSMContext) -> None:
    await _confirm(cq.message, state)
    await ack(cq)


@router.message(LeadBooking.comment, F.text)
async def booking_comment(message: Message, state: FSMContext) -> None:
    await state.update_data(comment=message.text.strip()[:1000])
    await _confirm(message, state)


async def _confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(LeadBooking.confirm)
    lines = [
        "✅ <b>Перевірте заявку</b>",
        "",
        f"Ім'я: {esc(data.get('client_name') or '—')}",
        f"Телефон: {esc(data.get('phone') or 'не вказано')}",
        f"Час: {esc(data.get('preferred_time') or '—')}",
    ]
    if data.get("comment"):
        lines.append(f"Коментар: {esc(data['comment'])}")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[btn("📨 Надіслати заявку", FormCB(field="bsend"))], [btn(CANCEL_TEXT_UK, FormCB(field="cancel"))]]
    )
    await message.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(LeadBooking.confirm, FormCB.filter(F.field == "bsend"))
async def booking_send(cq: CallbackQuery, state: FSMContext, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, actor: Actor) -> None:
    data = await state.get_data()
    await state.clear()
    prop = await property_service.get_property(session, data.get("property_id", 0))
    if prop is None or prop.is_archived:
        await cq.message.edit_text("😔 Об'єкт вже неактуальний.")
        return await ack(cq)
    try:
        await lead_actions.submit_lead(
            ctx, session, rs, prop,
            client_telegram_id=cq.from_user.id,
            client_username=cq.from_user.username,
            client_name=data.get("client_name"),
            phone=data.get("phone"),
            preferred_time=data.get("preferred_time"),
            comment=data.get("comment"),
        )
    except lead_service.LeadError as exc:
        await cq.message.edit_text(f"ℹ️ {esc(exc)}")
        return await ack(cq)
    await cq.message.edit_text(
        "🎉 <b>Дякуємо! Заявку надіслано.</b>\n\nРієлтор зв'яжеться з вами найближчим часом."
    )
    await ack(cq)
