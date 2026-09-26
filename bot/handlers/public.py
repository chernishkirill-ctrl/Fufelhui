"""Клиентская часть: запись на просмотр из публичного канала (deep-link ?start=lead_<id>)."""
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
from bot.keyboards.menus import btn, choices_kb
from bot.services import cards, lead_actions, lead_service, property_service
from bot.services.auth import Actor
from bot.services.context import AppContext
from bot.services.settings_service import RuntimeSettings
from bot.utils.text import esc, find_phone
from bot.utils.ui import ack

logger = logging.getLogger(__name__)
router = Router(name="public")
router.message.filter(F.chat.type == "private")

SKIP_PHONE = "Пропустить"
TIME_OPTIONS = [("Сегодня", "Сегодня"), ("Завтра", "Завтра"), ("В выходные", "В выходные"), ("Любое время", "Любое время")]

CLIENT_HELP = (
    "👋 Здравствуйте! Это бот агентства недвижимости.\n\n"
    "Чтобы записаться на просмотр, откройте объявление в нашем канале и нажмите «📅 Записаться на просмотр»."
)


async def start_booking(message: Message, state: FSMContext, session: AsyncSession, property_id: int) -> None:
    prop = await property_service.get_property(session, property_id)
    if prop is None or prop.is_archived:
        await message.answer("😔 Этот объект уже неактуален. Посмотрите другие предложения в нашем канале.")
        return
    await state.clear()
    await state.set_state(LeadBooking.name)
    await state.update_data(property_id=prop.id)
    name = message.from_user.full_name if message.from_user else ""
    kb = choices_kb("bname", [(f"✅ {name}", "tg")], per_row=1) if name else None
    await message.answer(
        "📅 <b>Запись на просмотр</b>\n\n"
        f"{cards.public_caption(prop, None, limit=700)}\n\n"
        "Как к вам обращаться? Напишите имя или нажмите кнопку.",
        reply_markup=kb,
    )


async def _ask_phone(event, state: FSMContext) -> None:
    await state.set_state(LeadBooking.phone)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)], [KeyboardButton(text=SKIP_PHONE)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer("📞 Оставьте номер телефона для связи (или нажмите «Пропустить» — риелтор напишет вам в Telegram):", reply_markup=kb)


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
            return await message.answer("Не похоже на номер телефона. Введите номер, например +380 67 123 45 67, или нажмите «Пропустить».")
        await state.update_data(phone=phone[:64])
    await _ask_time(message, state)


async def _ask_time(message: Message, state: FSMContext) -> None:
    await state.set_state(LeadBooking.time)
    await message.answer("👌", reply_markup=ReplyKeyboardRemove())
    await message.answer("🕒 Когда вам удобно посмотреть объект? Выберите или напишите дату и время:", reply_markup=choices_kb("btime", TIME_OPTIONS))


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
    await message.answer("💬 Комментарий или вопрос (необязательно):", reply_markup=choices_kb("bskip", [("⏭ Без комментария", "1")], per_row=1))


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
        "✅ <b>Проверьте заявку</b>",
        "",
        f"Имя: {esc(data.get('client_name') or '—')}",
        f"Телефон: {esc(data.get('phone') or 'не указан')}",
        f"Время: {esc(data.get('preferred_time') or '—')}",
    ]
    if data.get("comment"):
        lines.append(f"Комментарий: {esc(data['comment'])}")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[btn("📨 Отправить заявку", FormCB(field="bsend"))], [btn("✖️ Отмена", FormCB(field="cancel"))]]
    )
    await message.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(LeadBooking.confirm, FormCB.filter(F.field == "bsend"))
async def booking_send(cq: CallbackQuery, state: FSMContext, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, actor: Actor) -> None:
    data = await state.get_data()
    await state.clear()
    prop = await property_service.get_property(session, data.get("property_id", 0))
    if prop is None or prop.is_archived:
        await cq.message.edit_text("😔 Объект уже неактуален.")
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
        "🎉 <b>Спасибо! Заявка отправлена.</b>\n\nРиелтор свяжется с вами в ближайшее время."
    )
    await ack(cq)
