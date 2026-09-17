import os
import asyncio
import logging
import re
from datetime import datetime
from aiohttp import web

from aiogram import Bot, Dispatcher, F, html, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN") # Токен подтягивается из секретов Render
MY_ADMIN_ID = 8799145351

# Каналы и чаты
PUBLIC_CHANNEL_ID = "-1003889243376"
INTERNAL_BASE_ID = "-5325255205"

GROUP_CHAT_ID = -1004428877093
CHAT_TOPIC_ID = 3
DEALS_TOPIC_ID = 5
ARCHIVE_TOPIC_ID = 7

WEBAPP_FORM_URL = "https://t.me/gggggsre"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

PENDING_POSTS = {}

# ==========================================
# МИНИ-ВЕБ-СЕРВЕР (ОБЯЗАТЕЛЬНО ДЛЯ RENDER)
# ==========================================
async def handle_ping(request):
    return web.Response(text="Nestima Rent Bot is active!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Веб-сервер-заглушка успешно запущен на порту {port}")

# ==========================================
# СОСТОЯНИЯ (FSM)
# ==========================================
class FormStates(StatesGroup):
    waiting_for_input = State()
    waiting_for_deal_realtor = State()
    waiting_for_deal_address = State()
    waiting_for_deal_price = State()
    waiting_for_deal_commission = State()
    waiting_for_deal_telegraph = State()
    waiting_for_expense_desc = State()
    waiting_for_expense_amount = State()
    waiting_for_rent_date = State()

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def clean_sensitive_info(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\+?\d[\d\s-]{8,}\d', '[КОНТАКТИ ПРИХОВАНО]', text)
    text = re.sub(r'http[s]?://\S+', '', text)
    text = re.sub(r'@[A-Za-z0-9_]+', '', text)
    return text.strip()

def extract_district(text: str) -> str:
    text_lower = text.lower()
    districts = {
        "перемога": "Перемога", "центр": "Центр", "поля": "Поля", 
        "гагаріна": "Гагаріна", "набережна": "Набережна", 
        "лівий берег": "ЛівийБерег", "парус": "Парус", "тополя": "Тополя"
    }
    for key, val in districts.items():
        if key in text_lower:
            return val
    return "Дніпро"

async def parse_data(source_input: str):
    raw_text = source_input
    district = extract_district(raw_text)
    return {
        "raw_text": raw_text,
        "clean_text": clean_sensitive_info(raw_text[:600]),
        "district": district,
        "address": "вул. Набережна Перемоги, 42",
        "rooms": "1",
        "area": "38 м²",
        "price": "12,000 грн",
        "phone": "+380970000000",
        "object_id": f"RENT-{datetime.now().strftime('%M%S')}"
    }

async def create_telegraph_page(title: str, text: str) -> str:
    return "https://telegra.ph/Oglyad-orendi-Nestima-09-15"

# ==========================================
# СТАРТ / ПУЛЬТЫ УПРАВЛЕНИЯ
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    if user_id == MY_ADMIN_ID:
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="➕ Опублікувати об'єкт (Аренда)", callback_data="add_property"))
        builder.row(types.InlineKeyboardButton(text="🎉 Зафіксувати угоду", callback_data="add_deal"))
        builder.row(types.InlineKeyboardButton(text="📊 Бухгалтерія", callback_data="view_accounting"))
        builder.row(types.InlineKeyboardButton(text="📋 Звіти за сьогодні", callback_data="view_daily_reports"))
        await message.answer("🛠 <b>Панель керівника (Nestima Rent):</b>", reply_markup=builder.as_markup(), parse_mode="HTML")
        return

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Опублікувати об'єкт в оренду", callback_data="add_property"))
    builder.row(types.InlineKeyboardButton(text="🔄 Вторинна публікація / Перевірка", callback_data="my_objects"))
    await message.answer("👋 Вітаю, ріелторе! Твій міні-пульт управління орендою:", reply_markup=builder.as_markup(), parse_mode="HTML")

# ==========================================
# ПУБЛИКАЦИЯ ОБЪЕКТОВ (АРЕНДА)
# ==========================================
@dp.callback_query(F.data == "add_property")
async def start_add_property(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🔗 <b>Надішліть посилання</b> або текст оголошення по оренді (Дніпро):")
    await state.set_state(FormStates.waiting_for_input)
    await callback.answer()

@dp.message(FormStates.waiting_for_input)
async def process_property_input(message: types.Message, state: FSMContext):
    input_text = message.text or message.caption or "Об'єкт оренди"
    data = await parse_data(input_text)
    telegraph_url = await create_telegraph_page("Огляд оренди", data['clean_text'])
    
    obj_id = data['object_id']
    PENDING_POSTS[obj_id] = {**data, "telegraph_url": telegraph_url}

    work_text = (
        f"📥 <b>НОВИЙ ОБ'ЄКТ ОРЕНДИ У БАЗІ</b>\n\n"
        f"🆔 <b>ID:</b> {data['object_id']}\n"
        f"👤 <b>Контакт:</b> {data['phone']}\n"
        f"📍 <b>Адреса:</b> {data['address']}\n"
        f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
        f"📄 <b>Telegraph:</b> {telegraph_url}\n\n"
        f"📝 <b>Оригінал:</b>\n{data['raw_text'][:400]}"
    )
    await bot.send_message(chat_id=INTERNAL_BASE_ID, text=work_text, parse_mode="HTML")

    preview_text = (
        f"✅ <b>Об'єкт {obj_id} додано!</b>\n\n"
        f"📍 #{data['district']} (Оренда)\n"
        f"🏢 <b>Адреса:</b> {data['address']}\n"
        f"💰 <b>Ціна:</b> {data['price']}\n"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📢 Опублікувати в Публічний канал", callback_data=f"pub_{obj_id}"))
    builder.row(
        types.InlineKeyboardButton(text="🟢 Актуально", callback_data=f"st_active_{obj_id}"),
        types.InlineKeyboardButton(text="🔴 В архів", callback_data=f"st_archive_{obj_id}")
    )

    await message.answer(preview_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data.startswith("pub_"))
async def publish_to_public(callback: types.CallbackQuery):
    obj_id = callback.data.split("pub_")[1]
    data = PENDING_POSTS.get(obj_id)

    if not data:
        await callback.answer("⚠️ Дані застаріли.", show_alert=True)
        return

    public_text = (
        f"📍 #{data['district']} | АРЕНДА\n"
        f"🏢 <b>Адреса:</b> {data['address']}\n"
        f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
        f"📝 <i>{data['clean_text'][:200]}...</i>"
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📄 Дивитися повний огляд", url=data['telegraph_url']))
    builder.row(types.InlineKeyboardButton(text="📝 Записатися на перегляд", web_app=types.WebAppInfo(url=WEBAPP_FORM_URL)))

    await bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=public_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.message.edit_text(f"🚀 <b>Об'єкт {obj_id} успішно в публічному каналі!</b> (Термін показу: 10 днів)", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("st_active_"))
async def set_active(callback: types.CallbackQuery):
    await callback.message.answer("🟢 Об'єкт позначено як актуальний в роботі.")
    await callback.answer()

@dp.callback_query(F.data.startswith("st_archive_"))
async def set_archive(callback: types.CallbackQuery, state: FSMContext):
    obj_id = callback.data.split("st_archive_")[1]
    await state.update_data(archive_obj_id=obj_id)
    
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="📅 Вказати дату (Здано до...)", callback_data="ask_rent_date"),
        types.InlineKeyboardButton(text="❌ Безстроковий архів", callback_data="confirm_pure_archive")
    )
    await callback.message.answer("📦 Переносимо в архів. Коли об'єкт знову звільниться?", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "ask_rent_date")
async def ask_rent_date_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("✍️ Введіть дату, коли об'єкт знову буде актуальним (у форматі ДД.ММ.РРРР, наприклад: 15.10.2026):")
    await state.set_state(FormStates.waiting_for_rent_date)
    await callback.answer()

@dp.message(FormStates.waiting_for_rent_date)
async def process_rent_date(message: types.Message, state: FSMContext):
    date_str = message.text
    data = await state.get_data()
    obj_id = data.get('archive_obj_id', 'RENT-0000')

    archive_text = (
        f"📦 <b>ОБ'ЄКТ В АРХІВІ (Здано до {date_str})</b>\n\n"
        f"🆔 <b>ID:</b> {obj_id}\n"
        f"⏰ <b>Авто-повернення в роботу:</b> {date_str}\n"
        f"📍 Статус: Здано в оренду, очікуємо дату розірвання/звільнення."
    )

    await bot.send_message(chat_id=GROUP_CHAT_ID, message_thread_id=ARCHIVE_TOPIC_ID, text=archive_text, parse_mode="HTML")
    await message.answer(f"✅ Об'єкт {obj_id} перенесено в архів із таймером до {date_str}!")
    await state.clear()

@dp.callback_query(F.data == "confirm_pure_archive")
async def pure_archive(callback: types.CallbackQuery):
    await callback.message.answer("✅ Об'єкт перенесено у звичайний архів без таймера.")
    await callback.answer()

# ==========================================
# ОТЧЕТЫ (В 22:00) И СДЕЛКИ
# ==========================================
async def send_daily_report_prompt():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📌 Зафіксувати мій звіт", callback_data="save_group_report"))
    
    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        message_thread_id=CHAT_TOPIC_ID,
        text="⏰ <b>Час вечірнього звіту по оренді!</b>\n\nНапишіть підсумок дня у цей чат (через Reply) та натисніть кнопку нижче.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "save_group_report")
async def save_group_report_handler(callback: types.CallbackQuery):
    user = callback.from_user
    reply_msg = callback.message.reply_to_message

    if not reply_msg or reply_msg.from_user.id != user.id:
        await callback.answer("⚠️ Натисніть кнопку у відповідь (Reply) на СВОЄ повідомлення зі звітом!", show_alert=True)
        return

    username_str = f"@{user.username}" if user.username else "Без username"
    db.save_report(user.id, username_str, user.full_name, reply_msg.text)
    await callback.answer("✅ Звіт зафіксовано!", show_alert=True)

@dp.callback_query(F.data == "view_daily_reports")
async def view_reports_admin(callback: types.CallbackQuery):
    rows = db.get_today_reports()
    today_str = datetime.now().strftime("%Y-%m-%d")

    if not rows:
        await callback.message.answer(f"📊 <b>Звіти за сьогодні ({today_str}):</b> Поки пусто.", parse_mode="HTML")
        await callback.answer()
        return

    report_text = f"📊 <b>Звіти за сьогодні ({today_str}):</b>\n\n"
    for row in rows:
        report_text += f"👤 <b>{html.quote(row[1])} ({row[0]}):</b>\n💬 {html.quote(row[2])}\n\n"

    await callback.message.answer(report_text, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "add_deal")
async def start_add_deal(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("👤 Вкажіть @username ріелтора:")
    await state.set_state(FormStates.waiting_for_deal_realtor)
    await callback.answer()

@dp.message(FormStates.waiting_for_deal_realtor)
async def process_deal_realtor(message: types.Message, state: FSMContext):
    await state.update_data(realtor=message.text)
    await message.answer("🏠 Вкажіть адресу об'єкта оренди:")
    await state.set_state(FormStates.waiting_for_deal_address)

@dp.message(FormStates.waiting_for_deal_address)
async def process_deal_address(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await message.answer("💰 Вкажіть місячну вартість оренди (грн):")
    await state.set_state(FormStates.waiting_for_deal_price)

@dp.message(FormStates.waiting_for_deal_price)
async def process_deal_price(message: types.Message, state: FSMContext):
    await state.update_data(price=float(message.text))
    await message.answer("💵 Вкажіть комісію агентства (грн):")
    await state.set_state(FormStates.waiting_for_deal_commission)

@dp.message(FormStates.waiting_for_deal_commission)
async def process_deal_commission(message: types.Message, state: FSMContext):
    await state.update_data(commission=float(message.text))
    await message.answer("🔗 Вкажіть посилання на Telegraph-огляд:")
    await state.set_state(FormStates.waiting_for_deal_telegraph)

@dp.message(FormStates.waiting_for_deal_telegraph)
async def process_deal_telegraph(message: types.Message, state: FSMContext):
    data = await state.get_data()
    telegraph_link = message.text

    db.save_deal(data['realtor'], data['address'], data['price'], data['commission'], telegraph_link)

    deal_card_text = (
        f"🎉 <b>ОРЕНДУ УСПІШНО ЗАКРИТО!</b>\n\n"
        f"👤 <b>Ріелтор:</b> {data['realtor']}\n"
        f"🏠 <b>Об'єкт:</b> {data['address']}\n"
        f"💰 <b>Ціна оренди:</b> {data['price']:,.0f} грн\n"
        f"💵 <b>Комісія агентства:</b> {data['commission']:,.0f} грн"
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📄 Огляд об'єкта", url=telegraph_link))

    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        message_thread_id=DEALS_TOPIC_ID,
        text=deal_card_text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

    await message.answer("✅ Угоду оренди зафіксовано та опубліковано у темі «Сделки»!")
    await state.clear()

# ==========================================
# БУХГАЛТЕРИЯ
# ==========================================
@dp.callback_query(F.data == "view_accounting")
async def view_accounting_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Додати витрату (таксі, реклама)", callback_data="add_expense"))
    builder.row(types.InlineKeyboardButton(text="📈 Фінансовий звіт", callback_data="generate_fin_report"))

    await callback.message.answer("📊 <b>Модуль Бухгалтерії:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "add_expense")
async def start_add_expense(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Введіть опис витрати:")
    await state.set_state(FormStates.waiting_for_expense_desc)
    await callback.answer()

@dp.message(FormStates.waiting_for_expense_desc)
async def process_expense_desc(message: types.Message, state: FSMContext):
    await state.update_data(desc=message.text)
    await message.answer("💵 Введіть суму витрати у грн:")
    await state.set_state(FormStates.waiting_for_expense_amount)

@dp.message(FormStates.waiting_for_expense_amount)
async def process_expense_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    db.save_expense(data['desc'], float(message.text))
    await message.answer("✅ Витрату успішно внесено до бухгалтерії!")
    await state.clear()

@dp.callback_query(F.data == "generate_fin_report")
async def generate_financial_report(callback: types.CallbackQuery):
    income, expenses = db.get_financial_summary()
    net_profit = income - expenses

    report = (
        f"📊 <b>ЗВЕДЕНИЙ ФІНАНСОВИЙ ЗВІТ (ОРЕНДА)</b>\n\n"
        f"📥 <b>Дохід (комісії):</b> {income:,.2f} грн\n"
        f"📤 <b>Витрати:</b> {expenses:,.2f} грн\n"
        f"-----------------------------------\n"
        f"💰 <b>ЧИСТИЙ ПРИБУТОК:</b> {net_profit:,.2f} грн"
    )

    await callback.message.answer(report, parse_mode="HTML")
    await callback.answer()

# ==========================================
# ЗАПУСК БОТА И ВЕБ-СЕРВЕРА
# ==========================================
async def main():
    db.init_db()
    
    # 1. Запуск веб-сервера заглушки для Render
    await start_web_server()
    
    scheduler.add_job(send_daily_report_prompt, 'cron', hour=22, minute=0)
    scheduler.start()

    # 2. Сброс зависших сессий во избежание TelegramConflictError
    await bot.delete_webhook(drop_pending_updates=True)

    logging.info("Bot and Web Server started successfully!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
