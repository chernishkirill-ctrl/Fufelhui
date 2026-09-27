"""Клавиатуры: главное меню владельца/риелтора и типовые inline-кнопки."""
from __future__ import annotations

from math import ceil

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.callbacks import FormCB, MenuCB

# --- главное меню владельца ---
OWNER_STATS = "📊 Статистика"
OWNER_PROPS = "🏠 Объекты"
OWNER_REALTORS = "👥 Риелторы"
OWNER_LEADS = "📋 Заявки"
OWNER_DEALS = "💰 Сделки"
OWNER_REPORTS = "📑 Отчеты"
OWNER_SETTINGS = "⚙️ Настройки"

# --- главное меню риелтора ---
R_MY_PROPS = "🏠 Мои объекты"
R_ADD = "➕ Добавить объект"
R_IMPORT = "🔗 Импортировать"
R_LEADS = "📋 Мои заявки"
R_DEAL = "🤝 Зафиксировать сделку"
R_STATS = "📊 Моя статистика"
R_REPORT = "📝 Отчет за день"

CANCEL_TEXT = "✖️ Отмена"
CANCEL_TEXT_UK = "✖️ Скасувати"  # для клиентов


def owner_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=OWNER_STATS), KeyboardButton(text=OWNER_PROPS)],
            [KeyboardButton(text=OWNER_REALTORS), KeyboardButton(text=OWNER_LEADS)],
            [KeyboardButton(text=OWNER_DEALS), KeyboardButton(text=OWNER_REPORTS)],
            [KeyboardButton(text=OWNER_SETTINGS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def realtor_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=R_MY_PROPS), KeyboardButton(text=R_ADD)],
            [KeyboardButton(text=R_IMPORT), KeyboardButton(text=R_LEADS)],
            [KeyboardButton(text=R_DEAL), KeyboardButton(text=R_STATS)],
            [KeyboardButton(text=R_REPORT)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def btn(text: str, cb) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb.pack() if hasattr(cb, "pack") else cb)


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def home_btn() -> InlineKeyboardButton:
    return btn("🏠 В меню", MenuCB(section="home"))


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn(CANCEL_TEXT, FormCB(field="cancel"))]])


def skip_cancel_kb(skip_text: str = "⏭ Пропустить") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[btn(skip_text, FormCB(field="skip")), btn(CANCEL_TEXT, FormCB(field="cancel"))]]
    )


def choices_kb(
    field: str,
    options: list[tuple[str, str]],
    per_row: int = 2,
    cancel: bool = True,
    skip: bool = False,
    cancel_text: str = CANCEL_TEXT,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for text, value in options:
        b.add(btn(text, FormCB(field=field, value=value)))
    b.adjust(per_row)
    extra = []
    if skip:
        extra.append(btn("⏭ Пропустить", FormCB(field="skip")))
    if cancel:
        extra.append(btn(cancel_text, FormCB(field="cancel")))
    if extra:
        b.row(*extra)
    return b.as_markup()


def pager_row(make_cb, page: int, total: int, page_size: int) -> list[InlineKeyboardButton]:
    pages = max(1, ceil(total / page_size))
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(btn("◀️", make_cb(page - 1)))
    row.append(btn(f"{page + 1}/{pages}", make_cb(page)))
    if page < pages - 1:
        row.append(btn("▶️", make_cb(page + 1)))
    return row
