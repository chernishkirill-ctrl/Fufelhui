import os
import asyncio
import logging
import re
from datetime import datetime
from aiohttp import web, ClientSession
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
# КОНФИГУРАЦИЯ
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN") 
MY_ADMIN_ID = 8700604325

# Каналы и чаты (твои актуальные ID)
PUBLIC_CHANNEL_ID = "-1004464251419"
INTERNAL_BASE_ID = "-1004357065341"

GROUP_CHAT_ID = -1004357065341
CHAT_TOPIC_ID = 3
DEALS_TOPIC_ID = 5
ARCHIVE_TOPIC_ID = -5584583701

WEBAPP_FORM_URL = "https://t.me/gggggsre"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

PENDING_POSTS = {}

# ==========================================
# МИНИ-ВЕБ-СЕРВЕР (ДЛЯ RENDER)
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
    logging.info(f"Веб-сервер запущен на порту {port}")

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
# ПАРСЕР ПО ССЫЛКЕ ИЛИ ТЕКСТУ
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
        "лівий берег": "ЛівийБерег", "парус": "Парус", "тополя": "Тополя",
        "шевченка": "Шевченка"
    }
    for key, val in districts.items():
        if key in text_lower:
            return val
    return "Дніпро"

async def parse_data(source_input: str):
    raw_text = source_input

    if source_input.startswith("http"):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept-Language': 'uk-UA,uk;q=0.9,en;q=0.8',
            }
            async with ClientSession() as session:
                async with session.get(source_input, headers=headers, timeout=15) as resp:
                    if resp.status == 200:
                        html_doc = await resp.text()
                        soup = BeautifulSoup(html_doc, 'html.parser')
                        
                        paragraphs = [p.get_text(strip=True) for p in soup.find_all(['p', 'div', 'span', 'h1']) if len(p.get_text(strip=True)) > 15]
                        if paragraphs:
                            raw_text = "\n".join(paragraphs[:35])
        except Exception as e:
            logging.error(f"HTTP fetch error: {e}")

    district = extract_district(raw_text)
    
    rooms_match = re.search(r'(\d)\s*(?:к|кімн|кімнат|комн)', raw_text, re.IGNORECASE)
    rooms = f"{rooms_match.group(1)}к" if rooms_match else "1к"

    price_match = re.search(r'(\$\s*\d+[\d\s,]*|\d+[\d\s,]*\s*\$|\d+[\d\s,]*\s*грн|\d+[\d\s,]*\s*у\.е\.)', raw_text, re.IGNORECASE)
    price = price_match.group(1).strip() if price_match else "Уточнюється"

    area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:м²|м2|кв\.м)', raw_text, re.IGNORECASE)
    area = f"{area_match.group(1)} м²" if area_match else "Уточнюється"

    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    address = lines[0][:50] if lines else "вул. Центральна"

    phone_match = re.search(r'\+?380\s*\(?\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}|\+?\d[\d\s-]{8,}\d', raw_text)
    phone = phone_match.group(0) if phone_match else "Не вказано"

    return {
        "raw_text": raw_text,
        "clean_text": clean_sensitive_info(raw_text[:600]),
        "district": district,
        "address": address,
        "rooms": rooms,
        "area": area,
        "price": price,
        "phone": phone,
        "object_id": f"ID-{datetime.now().strftime('%M%S')}"
    }

async def create_telegraph_page(title: str, text: str) -> str:
    return "https://telegra.ph/Oglyad-orendi-Nestima-09-15"

# ==========================================
# ХЕНДЛЕРЫ БОТА
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
    await message.answer("👋 Вітаю, ріелторе! Твій міні-пульт управління орендою:", reply_markup=builder.as_markup(), parse_mode="HTML")

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
        f"👤 <b>Контакт власника:</b> {data['phone']}\n"
        f"📍 <b>Адреса:</b> {data['address']}\n"
        f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
        f"📄 <b>Telegraph:</b> {telegraph_url}\n\n"
        f"📝 <b>Повний текст оголошення:</b>\n{data['clean_text'][:400]}"
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
    await callback.message.edit_text(f"🚀 <b>Об'єкт {obj_id} успішно в публічному каналі!</b>", parse_mode="HTML")
    await callback.answer()

# Сделки и бухгалтерия
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

    await message.answer("✅ Угоду оренди зафіксовано!")
    await state.clear()

@dp.callback_query(F.data == "view_accounting")
async def view_accounting_menu(callback: types.CallbackQuery):
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
# ЗАПУСК
# ==========================================
async def main():
    db.init_db()
    await start_web_server()
    
    scheduler.start()
    await bot.delete_webhook(drop_pending_updates=True)

    logging.info("Bot and Web Server started successfully!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
