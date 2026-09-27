"""Общие команды: /start (с deep-link), /menu, /cancel, /help, /id, служебные команды в рабочем чате, fallback."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import UserStatus
from bot.handlers import properties as props_h
from bot.handlers import public as public_h
from bot.handlers import reports as reports_h
from bot.keyboards import menus
from bot.keyboards.callbacks import FormCB, MenuCB, RealtorCB, SettingsCB
from bot.services import settings_service, user_service, workchat_service
from bot.services.auth import Actor
from bot.services.context import AppContext
from bot.services.settings_service import TOPIC_KEYS, RuntimeSettings
from bot.utils.text import esc, parse_int
from bot.utils.ui import ACCESS_DENIED, ack, deny

logger = logging.getLogger(__name__)

router = Router(name="common")
fallback_router = Router(name="fallback")

OWNER_HELP = (
    "🛠 <b>Пульт владельца</b>\n\n"
    "Кнопки меню внизу: статистика, объекты, риелторы, заявки, сделки, отчеты, настройки.\n\n"
    "Команды:\n/menu — главное меню\n/cancel — отменить текущее действие\n/id — ваш Telegram ID\n\n"
    "В рабочем чате (группе):\n/chatinfo — показать ID чата и темы\n/bindchat — сделать группу рабочим чатом\n"
    "/settopic base|archive|deals|leads|chat — привязать текущую тему"
)
REALTOR_HELP = (
    "🏠 <b>Кабинет риелтора</b>\n\n"
    "Используйте кнопки меню внизу.\n/menu — главное меню\n/cancel — отменить текущее действие"
)


async def send_home(message: Message, actor: Actor) -> None:
    if actor.is_owner:
        await message.answer("🛠 <b>Пульт управления CRM</b>", reply_markup=menus.owner_menu())
    elif actor.is_staff:
        name = esc(actor.user.first_name or actor.user.display_name) if actor.user else ""
        await message.answer(f"👋 {name}, ваш кабинет риелтора.", reply_markup=menus.realtor_menu())
    elif actor.user is not None and actor.user.status == UserStatus.BLOCKED:
        await message.answer(ACCESS_DENIED, reply_markup=ReplyKeyboardRemove())
    else:
        await message.answer(public_h.CLIENT_HELP, reply_markup=ReplyKeyboardRemove())


@router.message(CommandStart(deep_link=True), F.chat.type == "private")
async def start_deep_link(
    message: Message, command: CommandObject, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext
) -> None:
    payload = (command.args or "").strip()
    kind, _, value = payload.partition("_")
    if kind == "lead":
        prop_id = parse_int(value)
        if prop_id:
            return await public_h.start_booking(message, state, session, prop_id)
    elif kind == "inv":
        if actor.is_owner:
            return await message.answer("Вы владелец — приглашение не требуется.")
        try:
            await user_service.accept_invite(
                session, value, message.from_user.id, message.from_user.username, message.from_user.first_name, ctx.config.owner_id
            )
        except user_service.UserError as exc:
            return await message.answer(f"⚠️ {esc(exc)}")
        await message.answer("✅ Доступ открыт! Добро пожаловать в CRM агентства.", reply_markup=menus.realtor_menu())
        await workchat_service.notify_owner(
            ctx, f"👤 Новый риелтор по приглашению: {esc(message.from_user.full_name)} (ID <code>{message.from_user.id}</code>)"
        )
        return
    elif kind in {"obj", "edit", "arch"}:
        if not actor.is_staff:
            return await deny(message)
        await state.clear()
        prop_id = parse_int(value) or 0
        if kind == "obj":
            return await props_h.open_by_id(message, actor, session, ctx, rs, prop_id)
        if kind == "edit":
            return await props_h.open_edit_by_id(message, actor, session, prop_id)
        return await props_h.open_archive_by_id(message, actor, session, prop_id)
    elif kind == "rep":
        return await reports_h.start_report(message, actor, session, ctx, state)
    await send_home(message, actor)


@router.message(CommandStart(), F.chat.type == "private")
@router.message(Command("menu"), F.chat.type == "private")
async def cmd_start(message: Message, actor: Actor, state: FSMContext) -> None:
    await state.clear()
    await send_home(message, actor)


@router.message(Command("cancel"), F.chat.type == "private")
async def cmd_cancel(message: Message, actor: Actor, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено." if actor.is_staff else "Дію скасовано.")
    await send_home(message, actor)


@router.callback_query(FormCB.filter(F.field == "cancel"))
async def cb_cancel(cq: CallbackQuery, actor: Actor, state: FSMContext) -> None:
    await state.clear()
    await ack(cq, "Отменено" if actor.is_staff else "Скасовано")
    try:
        await cq.message.edit_text("Действие отменено." if actor.is_staff else "Дію скасовано.")
    except Exception:  # noqa: BLE001 - сообщение могло быть фото/устаревшим
        pass
    if isinstance(cq.message, Message):
        await send_home(cq.message, actor)


@router.callback_query(MenuCB.filter(F.section == "home"))
async def cb_home(cq: CallbackQuery, actor: Actor, state: FSMContext) -> None:
    await state.clear()
    await ack(cq)
    if isinstance(cq.message, Message):
        await send_home(cq.message, actor)


@router.message(Command("help"), F.chat.type == "private")
async def cmd_help(message: Message, actor: Actor) -> None:
    if actor.is_owner:
        await message.answer(OWNER_HELP)
    elif actor.is_staff:
        await message.answer(REALTOR_HELP)
    else:
        await message.answer(public_h.CLIENT_HELP)


@router.message(Command("id"), F.chat.type == "private")
async def cmd_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


# ---------- служебные команды владельца в рабочем чате ----------

@router.message(Command("chatinfo"), F.chat.type.in_({"group", "supergroup", "channel"}))
async def cmd_chatinfo(message: Message, actor: Actor) -> None:
    if not actor.is_owner:
        return
    await message.reply(
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"message_thread_id (тема): <code>{message.message_thread_id or '—'}</code>\n"
        f"Topics: {'включены' if message.chat.is_forum else 'выключены'}"
    )


@router.message(Command("bindchat"), F.chat.type == "supergroup")
async def cmd_bindchat(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext) -> None:
    if not actor.is_owner:
        return
    if ctx.config.work_chat_id and ctx.config.work_chat_id != message.chat.id:
        return await message.reply("WORK_CHAT_ID задан в окружении — измените его в настройках Render.")
    await settings_service.set_work_chat(session, message.chat.id)
    await message.reply(f"✅ Этот чат назначен рабочим (<code>{message.chat.id}</code>).")


@router.message(Command("settopic"), F.chat.type == "supergroup")
async def cmd_settopic(message: Message, command: CommandObject, actor: Actor, session: AsyncSession) -> None:
    if not actor.is_owner:
        return
    key = (command.args or "").strip().lower()
    if key not in TOPIC_KEYS:
        return await message.reply("Использование: /settopic base|archive|deals|leads|chat (внутри нужной темы)")
    if not message.message_thread_id:
        return await message.reply("Отправьте команду внутри темы.")
    await settings_service.set_topic(session, key, message.message_thread_id)
    await message.reply(f"✅ Тема привязана как «{key}» (id {message.message_thread_id}). Значение из .env, если задано, имеет приоритет.")


# ---------- fallback: подключается последним ----------

@fallback_router.message(F.chat.type == "private", StateFilter("*"))
async def fallback_message(message: Message, actor: Actor, state: FSMContext) -> None:
    if actor.is_staff:
        if await state.get_state():
            await message.answer("Не понял ответ. Следуйте подсказке выше или нажмите /cancel.")
        else:
            await message.answer("Выберите действие в меню ниже 👇", reply_markup=menus.owner_menu() if actor.is_owner else menus.realtor_menu())
        return
    if await state.get_state():
        await message.answer("Будь ласка, дайте відповідь на запитання вище або натисніть /cancel.")
        return
    if message.text and message.text.startswith("/"):
        await message.answer(ACCESS_DENIED)
        return
    await message.answer(public_h.CLIENT_HELP)


OWNER_ONLY_PREFIXES = (RealtorCB.__prefix__ + ":", SettingsCB.__prefix__ + ":")


@fallback_router.callback_query()
async def fallback_callback(cq: CallbackQuery, actor: Actor) -> None:
    # Сюда попадают кнопки, не прошедшие проверку прав (например, риелтор вызвал callback пульта владельца)
    if not actor.is_staff or (cq.data or "").startswith(OWNER_ONLY_PREFIXES):
        await cq.answer(ACCESS_DENIED, show_alert=True)
    else:
        await cq.answer("Кнопка устарела или действие недоступно.", show_alert=True)
