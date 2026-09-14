import os
import asyncio
import logging
import re
from datetime import datetime
from aiohttp import ClientSession, web

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
BOT_TOKEN = os.getenv("BOT_TOKEN")

MY_ADMIN_ID = 8799145351

PUBLIC_CHANNEL_ID = "-1003889243376"
INTERNAL_BASE_ID = "-5325255205"

GROUP_CHAT_ID = -1004428877093
CHAT_TOPIC_ID = 3
DEALS_TOPIC_ID = 5

CONTACT_URL = "https://t.me/gggggsre"

logging.basicConfig(level=logging.INFO)

if not BOT_TOKEN:
    logging.error("ОШИБКА: BOT_TOKEN не найден в переменных окружения Render!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

PENDING_POSTS = {}

# ==========================================
# 2. МИНИ-ВЕБ-СЕРВЕР (ЗАГЛУШКА ДЛЯ RENDER)
# ==========================================
async def handle_ping(request):
    return web.Response(text="Nestima Bot is active and running 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Dummy Web-server successfully started on port {port}")

# ==========================================
# 3. СОСТОЯНИЯ (FSM)
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
# 4. УМНЫЙ ПАРСИНГ ТЕКСТА И TELEGRAPH API
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
        "поля": "Поля", "кірова": "Поля", "центр": "Центр", 
        "перемога": "Перемога", "гагаріна": "Гагаріна", "набережна": "Набережна", 
        "шевченка": "Парк_Шевченка", "лівий берег": "ЛівийБерег", 
        "парус": "Парус", "тополя": "Тополя", "сокіл": "Сокіл"
    }
    for key, val in districts.items():
        if key in text_lower:
            return val
    return "Дніпро"

async def parse_data(source_input: str):
    raw_text = source_input or "Об'єкт без опису"
    district = extract_district(raw_text)
    
    rooms_match = re.search(r'(\d)\s*(?:к|кімн|кімнат|комн)', raw_text, re.IGNORECASE)
    rooms = f"{rooms_match.group(1)}к" if rooms_match else "Уточнюється"

    price_match = re.search(r'(\$\s*\d+[\d\s,]*|\d+[\d\s,]*\s*\$|\d+[\d\s,]*\s*грн|\d+[\d\s,]*\s*у\.е\.)', raw_text, re.IGNORECASE)
    price = price_match.group(1).strip() if price_match else "Уточнюється"

    area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:м²|м2|кв\.м)', raw_text, re.IGNORECASE)
    area = f"{area_match.group(1)} м²" if area_match else "Уточнюється"

    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    address = lines[0][:60] if lines else "Апартаменти"

    phone_match = re.search(r'\+?\d[\d\s-]{8,}\d', raw_text)
    phone = phone_match.group(0) if phone_match else "Контакт через агентство"

    return {
        "raw_text": raw_text,
        "clean_text": clean_sensitive_info(raw_text),
        "district": district,
        "address": address,
        "rooms": rooms,
        "area": area,
        "price": price,
        "phone": phone,
        "object_id": f"ID-{datetime.now().strftime('%M%S')}"
    }

async def create_telegraph_page(title: str, text: str) -> str:
    """Динамическое создание настоящей страницы в Telegraph"""
    try:
        async with ClientSession() as session:
            # 1. Создаем анонимный аккаунт в Telegraph, если нужно
            acc_response = await session.post("https://api.telegra.ph/createAccount", json={
                "short_name": "Nestima",
                "author_name": "Nestima Real Estate"
            })
            acc_data = await acc_response.json()
            access_token = acc_data.get("result", {}).get("access_token")

            if not access_token:
                return "https://telegra.ph"

            # 2. Формируем контент страницы
            content = [{"tag": "p", "children": [paragraph]} for paragraph in text.split("\n") if paragraph.strip()]
            
            # 3. Публикуем статью
            page_response = await session.post("https://api.telegra.ph/createPage", json={
                "access_token": access_token,
                "title": title[:256],
                "author_name": "Nestima Real Estate",
                "content": content,
                "return_content": False
            })
            page_data = await page_response.json()
            if page_data.get("ok"):
                return f"https://telegra.ph/{page_data['result']['path']}"
    except Exception as e:
        logging.error(f"Ошибка создания Telegraph страницы: {e}")
    
    return "https://telegra.ph"

# ==========================================
# 5. ПУЛЬТ РУКОВОДИТЕЛЯ
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != MY_ADMIN_ID:
        await message.answer("👋 Вітаю! Бот працює в оперативному режимі.")
        return

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Опублікувати об'єкт / пост", callback_data="add_property"))
    builder.row(types.InlineKeyboardButton(text="🎉 Зафіксувати угоду", callback_data="add_deal"))
    builder.row(types.InlineKeyboardButton(text="📊 Бухгалтерія", callback_data="view_accounting"))
    builder.row(types.InlineKeyboardButton(text="📋 Звіти за сьогодні", callback_data="view_daily_reports"))

    await message.answer("🛠 <b>Панель управління Nestima Real Estate:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

# ==========================================
# 6. ЛОГИКА ПУБЛИКАЦИИ (БАЗА + КАНАЛ)
# ==========================================
@dp.callback_query(F.data == "add_property")
async def start_add_property(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🔗 <b>Надішліть посилання</b> (DOM.RIA, OLX, LUN) або <b>перешліть пост</b> з описом об'єкта:")
    await state.set_state(FormStates.waiting_for_input)
    await callback.answer()

@dp.message(FormStates.waiting_for_input, F.content_type.in_({'text', 'photo', 'document'}))
async def process_property_input(message: types.Message, state: FSMContext):
    try:
        input_text = message.text or message.caption or ""
        
        if not input_text and message.forward_from_chat:
            input_text = f"Пересланий пост з каналу {message.forward_from_chat.title}"

        if not input_text:
            await message.answer("⚠️ Не вдалося зчитати текст. Будь ласка, надішліть опис текстом:")
            return

        data = await parse_data(input_text)
        
        # Создаем НАСТОЯЩУЮ страницу Telegraph под этот объект
        page_title = f"{data['district']} | {data['rooms']} | {data['price']}"
        telegraph_url = await create_telegraph_page(page_title, data['clean_text'])
        
        obj_id = data['object_id']
        PENDING_POSTS[obj_id] = {**data, "telegraph_url": telegraph_url}

        # 1. Отправка во внутреннюю базу
        work_text = (
            f"📥 <b>НОВИЙ ОБ'ЄКТ У ВНУТРІШНІЙ БАЗІ</b>\n\n"
            f"🆔 <b>ID:</b> {data['object_id']}\n"
            f"👤 <b>Контакт:</b> {html.quote(data['phone'])}\n"
            f"📍 <b>Заголовок/Адреса:</b> {html.quote(data['address'])}\n"
            f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
            f"📄 <b>Telegraph:</b> {telegraph_url}\n\n"
            f"📝 <b>Оригінальний текст:</b>\n{html.quote(data['raw_text'][:500])}"
        )
        
        try:
            await bot.send_message(chat_id=INTERNAL_BASE_ID, text=work_text, parse_mode="HTML")
        except Exception as err:
            logging.error(f"Ошибка отправки во внутреннюю базу: {err}")

        # 2. Предпросмотр для публичного канала
        preview_text = (
            f"✅ <b>Об'єкт {obj_id} додано до Бази!</b>\n\n"
            f"👁 <b>Попередній перегляд для Публічного каналу:</b>\n\n"
            f"📍 #{data['district']}\n"
            f"🏢 <b>Заголовок/Опис:</b> {html.quote(data['address'])}\n"
            f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
            f"📝 <i>{html.quote(data['clean_text'][:200])}...</i>"
        )
        
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="📢 Опублікувати в Публічний канал", callback_data=f"publish_public_{obj_id}"))

        await message.answer(preview_text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await state.clear()

    except Exception as e:
        logging.error(f"Ошибка обработки объекта: {e}")
        await message.answer(f"⚠️ Сталася помилка при обробці: {e}\nСпробуйте ще раз через /start")
        await state.clear()

@dp.callback_query(F.data.startswith("publish_public_"))
async def publish_to_public_channel(callback: types.CallbackQuery):
    obj_id = callback.data.split("publish_public_")[1]
    data = PENDING_POSTS.get(obj_id)

    if not data:
        await callback.answer("⚠️ Дані об'єкта застаріли. Спробуйте ще раз через /start", show_alert=True)
        return

    public_text = (
        f"📍 #{data['district']}\n"
        f"🏢 <b>Опис:</b> {html.quote(data['address'])}\n"
        f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
        f"📝 <i>{html.quote(data['clean_text'][:350])}...</i>"
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📄 Дивитися повний огляд об'єкта", url=data['telegraph_url']))
    builder.row(types.InlineKeyboardButton(text="📝 Записатися на перегляд / Зв'язок", url=CONTACT_URL))

    try:
        await bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=public_text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.message.edit_text(f"🚀 <b>Об'єкт {obj_id} успішно опубліковано в Публічний канал!</b>", parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка публикации в публичный канал: {e}")
        await callback.message.answer(f"⚠️ Помилка публікації в канал: {e}")
    
    await callback.answer()

# ==========================================
# 7. ЗВІТИ (ТЕМА "ЧАТ")
# ==========================================
async def send_daily_report_prompt():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📌 Зафіксувати мій звіт", callback_data="save_group_report"))
    
    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        message_thread_id=CHAT_TOPIC_ID,
        text="⏰ <b>Час вечірнього звіту!</b>\n\nНапишіть підсумок дня у цей чат (через Reply) та натисніть кнопку нижче під своїм повідомленням.",
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
# 8. УГОДИ (ТЕМА "СДЕЛКИ")
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
    try:
        price = float(message.text.replace("$", "").replace(",", "").strip())
        await state.update_data(price=price)
        await message.answer("💵 Вкажіть комісію агентства ($):")
        await state.set_state(FormStates.waiting_for_deal_commission)
    except ValueError:
        await message.answer("⚠️ Вкажіть суму числом (наприклад: 45000):")

@dp.message(FormStates.waiting_for_deal_commission)
async def process_deal_commission(message: types.Message, state: FSMContext):
    try:
        commission = float(message.text.replace("$", "").replace(",", "").strip())
        await state.update_data(commission=commission)
        await message.answer("🔗 Вкажіть посилання на Telegraph-огляд:")
        await state.set_state(FormStates.waiting_for_deal_telegraph)
    except ValueError:
        await message.answer("⚠️ Вкажіть комісію числом (наприклад: 1500):")

@dp.message(FormStates.waiting_for_deal_telegraph)
async def process_deal_telegraph(message: types.Message, state: FSMContext):
    data = await state.get_data()
    telegraph_link = message.text

    db.save_deal(data['realtor'], data['address'], data['price'], data['commission'], telegraph_link)

    deal_card_text = (
        f"🎉 <b>УГОДУ ЗАКРИТО!</b>\n\n"
        f"👤 <b>Ріелтор:</b> {html.quote(data['realtor'])}\n"
        f"🏠 <b>Об'єкт:</b> {html.quote(data['address'])}\n"
        f"💰 <b>Сума угоди:</b> ${data['price']:,.0f}\n"
        f"💵 <b>Комісія агентства:</b> ${data['commission']:,.0f}"
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📄 Огляд об'єкта", url=telegraph_link))

    try:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            message_thread_id=DEALS_TOPIC_ID,
            text=deal_card_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка отправки сделки: {e}")

    await message.answer("✅ Угоду успішно опубліковано у темі «Сделки»!")
    await state.clear()

# ==========================================
# 9. БУХГАЛТЕРИЯ
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
    try:
        amount = float(message.text.replace("$", "").replace(",", "").strip())
        data = await state.get_data()
        db.save_expense(data['desc'], amount)
        await message.answer("✅ Витрату внесено!")
        await state.clear()
    except ValueError:
        await message.answer("⚠️ Введіть суму числом (наприклад: 50):")

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
# 10. ЗАПУСК
# ==========================================
async def main():
    db.init_db()
    
    scheduler.add_job(send_daily_report_prompt, 'cron', hour=22, minute=0)
    scheduler.start()

    await start_web_server()

    logging.info("Bot started successfully!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
