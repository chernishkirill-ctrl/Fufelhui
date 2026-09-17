import asyncio
import logging
import re
from datetime import datetime
import aiohttp
from bs4 import BeautifulSoup

from aiogram import Bot, Dispatcher, F, html, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
MY_ADMIN_ID = 123456789  # Твой личный Telegram ID

PUBLIC_CHANNEL_ID = "@your_public_channel"  
INTERNAL_BASE_ID = "@your_internal_base"    

GROUP_CHAT_ID = -1001234567890             
CHAT_TOPIC_ID = 1                          
DEALS_TOPIC_ID = 2                         

WEBAPP_FORM_URL = "https://your-webapp-url.com/form"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

PENDING_POSTS = {}

# ==========================================
# 2. СОСТОЯНИЯ (FSM)
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

# ==========================================
# 3. ИНТЕЛЛЕКТУАЛЬНЫЙ ПАРСИНГ ПОД ЭТАЛОН
# ==========================================
def clean_sensitive_info(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\+?\d[\d\s-]{8,}\d', '[КОНТАКТИ ПРИХОВАНО]', text)
    text = re.sub(r'http[s]?://\S+', '', text)
    text = re.sub(r'@[A-Za-z0-9_]+', '', text)
    return text.strip()

def extract_fields(text: str):
    """Вытаскивает параметры из текста объявления."""
    # 1. Краткое кол-во комнат (например: 3к, 1-кімнатна -> 3к)
    rooms = "1к"
    room_match = re.search(r'(\d)\s*[-ккiмн]', text.lower())
    if room_match:
        rooms = f"{room_match.group(1)}к"

    # 2. Площадь (например: 65 м2, 65.0м² -> 65.0 м²)
    area = "Уточнюється"
    area_match = re.search(r'(\d+[\.,]?\d*)\s*(?:м²|кв\.?\s*м)', text.lower())
    if area_match:
        area = f"{area_match.group(1)} м²"

    # 3. Этаж (например: 10/10, поверх 2 з 9)
    floor = "Уточнюється"
    floor_match = re.search(r'(?:поверх:?\s*)?(\d{1,2}\s*/\s*\d{1,2})', text.lower())
    if floor_match:
        floor = floor_match.group(1)
    else:
        floor_single = re.search(r'(\d{1,2})\s*поверх', text.lower())
        if floor_single:
            floor = f"{floor_single.group(1)} поверх"

    # 4. Берег
    bank = "Правий Берег"
    if "лівий" in text.lower():
        bank = "Лівий Берег"

    # 5. Район
    district = "Центр"
    districts_map = {
        "перемога": "Перемога", "центр": "Центр", "поля": "Поля", 
        "кірова": "Поля", "гагаріна": "Гагаріна", "набережна": "Набережна", 
        "парус": "Парус", "тополя": "Тополя", "космічна": "Космічна", "рогальова": "Центр"
    }
    for key, val in districts_map.items():
        if key in text.lower():
            district = val
            break

    # 6. Адрес (ищем улицу)
    address = "вул. Центральна, 1"
    addr_match = re.search(r'(?:вул\.?|вулиця|просп\.?|проспект|пл\.?)\s+([А-Яа-яІіЇїЄє0-9\-\.\,\s"]+)', text)
    if addr_match:
        address = addr_match.group(0).split(',')[0] + ("," + addr_match.group(0).split(',')[1] if ',' in addr_match.group(0) else "")

    # 7. Цена
    price = "Ціна за запитом"
    price_match = re.search(r'(\d[\d\s]*)\s*(?:грн|usd|\$|дол)', text.lower())
    if price_match:
        price = price_match.group(0).upper()

    return {
        "rooms": rooms,
        "area": area,
        "floor": floor,
        "bank": bank,
        "district": district,
        "address": address,
        "price": price,
        "object_id": f"ID-{datetime.now().strftime('%M%S')}"
    }

async def parse_data(source_input: str):
    fields = extract_fields(source_input)
    clean_text = clean_sensitive_info(source_input)
    
    # Извлекаем телефон из сырого текста для внутренней базы
    phone_match = re.search(r'\+?\d[\d\s-]{8,}\d', source_input)
    phone = phone_match.group(0) if phone_match else "Не вказано"

    return {
        "raw_text": source_input,
        "clean_text": clean_text,
        "phone": phone,
        **fields
    }

async def create_telegraph_page(title: str, text: str) -> str:
    return f"https://telegra.ph/Obyekt-{datetime.now().strftime('%d-%m-%Y-%H%M')}"

# ==========================================
# 4. ПУЛЬТ РУКОВОДИТЕЛЯ
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != MY_ADMIN_ID:
        await message.answer("👋 Вітаю! Бот працює.")
        return

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Опублікувати об'єкт / пост", callback_data="add_property"))
    builder.row(types.InlineKeyboardButton(text="🎉 Зафіксувати угоду", callback_data="add_deal"))
    builder.row(types.InlineKeyboardButton(text="📊 Бухгалтерія", callback_data="view_accounting"))
    builder.row(types.InlineKeyboardButton(text="📋 Звіти за сьогодні", callback_data="view_daily_reports"))

    await message.answer("🛠 <b>Панель управління Nestima Real Estate:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

# ==========================================
# 5. ПУБЛИКАЦИЯ ОБЪЕКТА
# ==========================================
@dp.callback_query(F.data == "add_property")
async def start_add_property(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🔗 <b>Надішліть текст або посилання на оголошення:</b>")
    await state.set_state(FormStates.waiting_for_input)
    await callback.answer()

@dp.message(FormStates.waiting_for_input)
async def process_property_input(message: types.Message, state: FSMContext):
    input_text = message.text or message.caption or "Об'єкт без тексту"
    data = await parse_data(input_text)
    telegraph_url = await create_telegraph_page("Огляд об'єкта", data['clean_text'])
    
    obj_id = data['object_id']
    PENDING_POSTS[obj_id] = {**data, "telegraph_url": telegraph_url}

    # 1. Отправка во внутреннюю базу (с лимитом длины)
    safe_clean_text = data['clean_text'][:3500] if data['clean_text'] else "Опис відсутній"
    work_text = (
        f"📥 <b>НОВИЙ ОБ'ЄКТ У ВНУТРІШНІЙ БАЗІ</b>\n\n"
        f"🆔 <b>ID:</b> {obj_id}\n"
        f"👤 <b>Контакт:</b> {html.quote(data['phone'])}\n"
        f"📍 <b>Адреса:</b> {html.quote(data['address'])}\n"
        f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
        f"📄 <b>Telegraph:</b> {telegraph_url}\n\n"
        f"📝 <b>Оригінал:</b>\n{html.quote(safe_clean_text)}"
    )
    await bot.send_message(chat_id=INTERNAL_BASE_ID, text=work_text, parse_mode="HTML")

    # 2. Предпросмотр для публичного канала
    preview_text = (
        f"✅ <b>Об'єкт {obj_id} готовий до публікації!</b>\n\n"
        f"<b>{data['rooms']}</b>\n"
        f"📐 {data['area']}\n"
        f"🔺 Поверх: {data['floor']} ⚠️\n"
        f"📍 {data['address']}\n"
        f"💰 {data['price']}"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📢 Опублікувати в Публічний канал", callback_data=f"publish_public_{obj_id}"))

    await message.answer(preview_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data.startswith("publish_public_"))
async def publish_to_public_channel(callback: types.CallbackQuery):
    obj_id = callback.data.split("publish_public_")[1]
    data = PENDING_POSTS.get(obj_id)

    if not data:
        await callback.answer("⚠️ Дані застаріли.", show_alert=True)
        return

    # СТРОГИЙ ЭТАЛОННЫЙ ФОРМАТ ПОСТА
    public_text = (
        f"{data['rooms']}\n"
        f"📐 {data['area']}\n"
        f"🔺 Поверх: {data['floor']} ⚠️\n"
        f"📍 {data['address']}\n"
        f"💰 {data['price']}\n\n"
        f"📄 Деталі та фото: <a href='{data['telegraph_url']}'>Дивитися огляд</a>\n\n"
        f"#{data['district']}{data['rooms']}\n"
        f"#{data['bank'].replace(' ', '')}\n"
        f"#Nestima"
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📝 Записатися на перегляд", web_app=types.WebAppInfo(url=WEBAPP_FORM_URL)))

    # Отправка в канал СТРОГО БЕЗ ПРЕВЬЮ
    await bot.send_message(
        chat_id=PUBLIC_CHANNEL_ID, 
        text=public_text, 
        reply_markup=builder.as_markup(), 
        parse_mode="HTML",
        link_preview_options={"is_disabled": True}
    )
    
    await callback.message.edit_text(f"🚀 **Об'єкт успішно опубліковано в Публічний канал у правильному форматі!**", parse_mode="HTML")
    await callback.answer()

# ==========================================
# 6. ОПОВЕЩЕНИЯ И ОТЧЕТЫ В ГРУППУ
# ==========================================
async def send_daily_report_prompt():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📌 Зафіксувати мій звіт", callback_data="save_group_report"))
    
    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        message_thread_id=CHAT_TOPIC_ID,
        text="⏰ <b>Час вечірнього звіту!</b>\n\nНапишіть підсумок дня у цей чат (через Reply) та натисніть кнопку нижче.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "save_group_report")
async def save_group_report_handler(callback: types.CallbackQuery):
    user = callback.from_user
    reply_msg = callback.message.reply_to_message

    if not reply_msg or reply_msg.from_user.id != user.id:
        await callback.answer("⚠️ Натисніть цю кнопку у відповідь (Reply) на СВОЄ повідомлення зі звітом!", show_alert=True)
        return

    username_str = f"@{user.username}" if user.username else "Без username"
    db.save_report(user.id, username_str, user.full_name, reply_msg.text)
    await callback.answer("✅ Ваш звіт зафіксовано!", show_alert=True)

@dp.callback_query(F.data == "view_daily_reports")
async def view_reports_admin(callback: types.CallbackQuery):
    rows = db.get_today_reports()
    today_str = datetime.now().strftime("%Y-%m-%d")

    if not rows:
        await callback.message.answer(f"📊 <b>Звіти за сьогодні ({today_str}):</b>\n\nПоки немає звітів.", parse_mode="HTML")
        await callback.answer()
        return

    report_text = f"📊 <b>Звіти за сьогодні ({today_str}):</b>\n\n"
    for row in rows:
        report_text += f"👤 <b>{html.quote(row[1])} ({row[0]}):</b>\n💬 {html.quote(row[2])}\n\n"

    await callback.message.answer(report_text, parse_mode="HTML")
    await callback.answer()

# ==========================================
# 7. ФИКСАЦИЯ СДЕЛОК
# ==========================================
@dp.callback_query(F.data == "add_deal")
async def start_add_deal(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("👤 Вкажіть @username ріелтора:")
    await state.set_state(FormStates.waiting_for_deal_realtor)
    await callback.answer()

@dp.message(FormStates.waiting_for_deal_realtor)
async def process_deal_realtor(message: types.Message, state: FSMContext):
    await state.update_data(realtor=message.text)
    await message.answer("🏠 Вкажіть адресу об'єкта:")
    await state.set_state(FormStates.waiting_for_deal_address)

@dp.message(FormStates.waiting_for_deal_address)
async def process_deal_address(message: types.Message, state: FSMContext):
    await state.update_data(address=message.text)
    await message.answer("💰 Вкажіть суму угоди ($):")
    await state.set_state(FormStates.waiting_for_deal_price)

@dp.message(FormStates.waiting_for_deal_price)
async def process_deal_price(message: types.Message, state: FSMContext):
    await state.update_data(price=float(message.text))
    await message.answer("💵 Вкажіть комісію агентства ($):")
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
        f"🎉 <b>УГОДУ ЗАКРИТО!</b>\n\n"
        f"👤 <b>Ріелтор:</b> {data['realtor']}\n"
        f"🏠 <b>Об'єкт:</b> {data['address']}\n"
        f"💰 <b>Сума угоди:</b> ${data['price']:,.0f}\n"
        f"💵 <b>Комісія агентства:</b> ${data['commission']:,.0f}"
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

    await message.answer("✅ Угоду успішно опубліковано у темі «Сделки»!")
    await state.clear()

# ==========================================
# 8. БУХГАЛТЕРИЯ
# ==========================================
@dp.callback_query(F.data == "view_accounting")
async def view_accounting_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Додати витрату", callback_data="add_expense"))
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
    await message.answer("💵 Введіть суму витрати у $:")
    await state.set_state(FormStates.waiting_for_expense_amount)

@dp.message(FormStates.waiting_for_expense_amount)
async def process_expense_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    db.save_expense(data['desc'], float(message.text))
    await message.answer("✅ Витрату внесено!")
    await state.clear()

@dp.callback_query(F.data == "generate_fin_report")
async def generate_financial_report(callback: types.CallbackQuery):
    income, expenses = db.get_financial_summary()
    net_profit = income - expenses

    report = (
        f"📊 <b>ЗВЕДЕНИЙ ФІНАНСОВИЙ ЗВІТ</b>\n\n"
        f"📥 <b>Дохід (комісії):</b> ${income:,.2f}\n"
        f"📤 <b>Витрати:</b> ${expenses:,.2f}\n"
        f"-----------------------------------\n"
        f"💰 <b>ЧИСТИЙ ПРИБУТОК:</b> ${net_profit:,.2f}"
    )

    await callback.message.answer(report, parse_mode="HTML")
    await callback.answer()

# ==========================================
# 9. ЗАПУСК
# ==========================================
async def main():
    db.init_db()
    scheduler.add_job(send_daily_report_prompt, 'cron', hour=22, minute=0)
    scheduler.start()

    logging.info("Bot started!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
