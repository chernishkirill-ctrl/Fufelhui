import os
import asyncio
import logging
import re
from datetime import datetime
from aiohttp import ClientSession, web
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
BOT_TOKEN = os.getenv("BOT_TOKEN")

MY_ADMIN_ID = 8799145351

PUBLIC_CHANNEL_ID = "-1003889243376"
INTERNAL_BASE_ID = "-5325255205"

GROUP_CHAT_ID = -1004428877093
CHAT_TOPIC_ID = 3
DEALS_TOPIC_ID = 5

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Хранилище заявок и объектов в памяти
PENDING_LEADS = {}
TAKEN_LEADS = {}

# ==========================================
# 2. МИНИ-ВЕБ-СЕРВЕР (ДЛЯ RENDER)
# ==========================================
async def handle_ping(request):
    return web.Response(text="Nestima Real Estate Bot status: Active")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# ==========================================
# 3. СОСТОЯНИЯ (FSM)
# ==========================================
class FormStates(StatesGroup):
    waiting_for_input = State()
    waiting_for_lead_info = State()
    waiting_for_deal_realtor = State()
    waiting_for_deal_address = State()
    waiting_for_deal_price = State()
    waiting_for_deal_commission = State()
    waiting_for_deal_telegraph = State()
    waiting_for_expense_desc = State()
    waiting_for_expense_amount = State()

# ==========================================
# 4. ПАРСИНГ И TELEGRAPH
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

async def fetch_page_data(url_or_text: str):
    images = []
    raw_text = url_or_text

    if url_or_text.startswith("http"):
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            async with ClientSession() as session:
                async with session.get(url_or_text, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        html_doc = await resp.text()
                        soup = BeautifulSoup(html_doc, 'html.parser')
                        
                        paragraphs = [p.get_text() for p in soup.find_all(['p', 'h1', 'div']) if len(p.get_text()) > 30]
                        if paragraphs:
                            raw_text = "\n".join(paragraphs[:10])

                        for img in soup.find_all('img'):
                            src = img.get('src') or img.get('data-src')
                            if src and 'http' in src and ('photos' in src or 'images' in src or 'olx' in src):
                                if src not in images:
                                    images.append(src)
        except Exception as e:
            logging.error(f"Ошибка парсинга ссылки: {e}")

    district = extract_district(raw_text)
    
    rooms_match = re.search(r'(\d)\s*(?:к|кімн|кімнат|комн)', raw_text, re.IGNORECASE)
    rooms = f"{rooms_match.group(1)}к" if rooms_match else "Уточнюється"

    price_match = re.search(r'(\$\s*\d+[\d\s,]*|\d+[\d\s,]*\s*\$|\d+[\d\s,]*\s*грн|\d+[\d\s,]*\s*у\.е\.)', raw_text, re.IGNORECASE)
    price = price_match.group(1).strip() if price_match else "Уточнюється"

    area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:м²|м2|кв\.м)', raw_text, re.IGNORECASE)
    area = f"{area_match.group(1)} м²" if area_match else "Уточнюється"

    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    address = lines[0][:60] if lines else "Об'єкт нерухомості"

    phone_match = re.search(r'\+?\d[\d\s-]{8,}\d', raw_text)
    phone = phone_match.group(0) if phone_match else "Контакт через агентство"

    map_url = f"https://www.google.com/maps/search/?api=1&query={district}+Дніпро"

    return {
        "raw_text": raw_text,
        "clean_text": clean_sensitive_info(raw_text),
        "district": district,
        "address": address,
        "rooms": rooms,
        "area": area,
        "price": price,
        "phone": phone,
        "images": images[:10],
        "map_url": map_url,
        "object_id": f"ID-{datetime.now().strftime('%M%S')}"
    }

async def create_telegraph_page_with_gallery(title: str, text: str, images: list) -> str:
    try:
        async with ClientSession() as session:
            acc_resp = await session.post("https://api.telegra.ph/createAccount", json={"short_name": "Nestima", "author_name": "Nestima Real Estate"})
            acc_data = await acc_resp.json()
            token = acc_data.get("result", {}).get("access_token")

            if not token:
                return "https://telegra.ph"

            content = []
            for img_url in images:
                content.append({"tag": "img", "attrs": {"src": img_url}})
            
            for p in text.split("\n"):
                if p.strip():
                    content.append({"tag": "p", "children": [p.strip()]})

            page_resp = await session.post("https://api.telegra.ph/createPage", json={
                "access_token": token,
                "title": title[:256],
                "author_name": "Nestima Real Estate",
                "content": content,
                "return_content": False
            })
            page_data = await page_resp.json()
            if page_data.get("ok"):
                return f"https://telegra.ph/{page_data['result']['path']}"
    except Exception as e:
        logging.error(f"Ошибка создания Telegraph: {e}")
    
    return "https://telegra.ph"

# ==========================================
# 5. ПАНЕЛЬ УПРАВЛЕНИЯ И ОБРАБОТКА ССЫЛКИ
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

    await message.answer("🛠 <b>Панель управління Nestima Real Estate:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "add_property")
async def start_add_property(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🔗 Надішліть посилання (OLX / DOM.RIA) або текст об'єкта:")
    await state.set_state(FormStates.waiting_for_input)
    await callback.answer()

@dp.message(FormStates.waiting_for_input)
async def process_property_input(message: types.Message, state: FSMContext):
    try:
        input_text = message.text or ""
        data = await fetch_page_data(input_text)
        
        page_title = f"{data['district']} | {data['rooms']} | {data['price']}"
        telegraph_url = await create_telegraph_page_with_gallery(page_title, data['clean_text'], data['images'])
        
        obj_id = data['object_id']

        # 1. ОТПРАВКА В ЗАКРЫТЫЙ КАНАЛ ДЛЯ РИЕЛТОРОВ (ПОЛНАЯ ИНФО)
        work_text = (
            f"📥 <b>НОВИЙ ОБ'ЄКТ У ВНУТРІШНІЙ БАЗІ</b>\n\n"
            f"🆔 <b>ID:</b> {obj_id}\n"
            f"👤 <b>Контакт:</b> {html.quote(data['phone'])}\n"
            f"📍 <b>Адреса/Заголовок:</b> {html.quote(data['address'])}\n"
            f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
            f"📄 <b>Telegraph альбом:</b> {telegraph_url}\n\n"
            f"📝 <b>Оригінальний опис:</b>\n{html.quote(data['raw_text'][:600])}"
        )
        
        try:
            if data['images']:
                media = [types.InputMediaPhoto(media=url) for url in data['images'][:10]]
                await bot.send_media_group(chat_id=INTERNAL_BASE_ID, media=media)
            await bot.send_message(chat_id=INTERNAL_BASE_ID, text=work_text, parse_mode="HTML")
        except Exception as err:
            logging.error(f"Ошибка отправки в закрытую базу: {err}")

        # 2. ОТПРАВКА В ПУБЛИЧНЫЙ КАНАЛ (МИНИМАЛЬНОЕ ОПИСАНИЕ + 2 КНОПКИ)
        public_text = (
            f"📍 #{data['district']}\n"
            f"🏢 <b>Опис:</b> {html.quote(data['address'])}\n"
            f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
            f"📄 <a href='{telegraph_url}'>Переглянути фото та детальний опис у Telegraph</a>"
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(text="📍 Подивитися на карті", url=data['map_url']),
            types.InlineKeyboardButton(text="📝 Записатися на перегляд", callback_data=f"book_view_{obj_id}")
        )

        await bot.send_message(
            chat_id=PUBLIC_CHANNEL_ID, 
            text=public_text, 
            reply_markup=builder.as_markup(), 
            parse_mode="HTML",
            disable_web_page_preview=False
        )

        await message.answer(f"✅ <b>Успішно!</b>\n1. Повний об'єкт {obj_id} відправлено в закриту базу.\n2. Публічне оголошення опубліковано в канал.", parse_mode="HTML")
        await state.clear()

    except Exception as e:
        logging.error(f"Processing error: {e}")
        await message.answer(f"⚠️ Помилка обробки: {e}")
        await state.clear()

# ==========================================
# 6. КЛИЕНТ ЖМЕТ «ЗАПИСАТЬСЯ НА ПРОСМОТР»
# ==========================================
@dp.callback_query(F.data.startswith("book_view_"))
async def start_booking(callback: types.CallbackQuery, state: FSMContext):
    obj_id = callback.data.split("book_view_")[1]
    await state.update_data(booking_obj_id=obj_id)
    
    await callback.message.answer(
        "📝 **Запис на перегляд**\n\nБудь ласка, введіть ваше **Ім'я та Номер телефону** через пробіл (наприклад: *Олексій 0971234567*):",
        parse_mode="Markdown"
    )
    await state.set_state(FormStates.waiting_for_lead_info)
    await callback.answer()

@dp.message(FormStates.waiting_for_lead_info)
async def process_lead_info(message: types.Message, state: FSMContext):
    data = await state.get_data()
    obj_id = data.get("booking_obj_id", "ID-0000")
    
    user_input = message.text or ""
    client_username = f"@{message.from_user.username}" if message.from_user.username else "Немає username"
    
    lead_id = f"LEAD-{datetime.now().strftime('%M%S')}"
    
    # Сохраняем данные клиента
    PENDING_LEADS[lead_id] = {
        "obj_id": obj_id,
        "info": user_input,
        "username": client_username,
        "user_id": message.from_user.id
    }

    # Сообщение в чат риелторов без показа личных данных
    group_text = (
        f"🔔 <b>НОВА ЗАЯВКА НА ПЕРЕГЛЯД!</b>\n\n"
        f"🆔 <b>Об'єкт:</b> {obj_id}\n"
        f"⚡️ <i>Натисніть кнопку нижче, щоб перехопити клієнта та отримати його контакти.</i>"
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🙋‍♂️ Прийняти клієнта", callback_data=f"take_client_{lead_id}"))

    try:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            message_thread_id=CHAT_TOPIC_ID,
            text=group_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await message.answer("✅ <b>Дякуємо! Заявка прийнята.</b>\nНаш ріелтор зв'яжеться з вами найближчим часом.", parse_mode="HTML")
    except Exception as e:
        logging.error(f"Lead routing error: {e}")
        await message.answer("✅ Заявку прийнято!")

    await state.clear()

# ==========================================
# 7. РИЕЛТОР ЖМЕТ «ПРИНЯТЬ КЛИЕНТА»
# ==========================================
@dp.callback_query(F.data.startswith("take_client_"))
async def accept_client_handler(callback: types.CallbackQuery):
    lead_id = callback.data.split("take_client_")[1]

    # Проверка: успел ли кто-то раньше
    if lead_id in TAKEN_LEADS:
        taken_by = TAKEN_LEADS[lead_id]
        await callback.answer(f"❌ Заявку вже перехопив ріелтор {taken_by}!", show_alert=True)
        return

    lead_data = PENDING_LEADS.get(lead_id)
    if not lead_data:
        await callback.answer("⚠️ Дані заявки застаріли.", show_alert=True)
        return

    realtor = callback.from_user
    realtor_tag = f"@{realtor.username}" if realtor.username else realtor.full_name
    TAKEN_LEADS[lead_id] = realtor_tag

    # 1. Показываем ВСЕ ДАННЫЕ ТОЛЬКО первому успевшему риелтору во всплывающем окне
    full_info = (
        f"🔑 КЛІЄНТА ПЕРЕХОПЛЕНО!\n\n"
        f"🆔 Об'єкт: {lead_data['obj_id']}\n"
        f"👤 Данні: {lead_data['info']}\n"
        f"💬 Telegram: {lead_data['username']}"
    )
    await callback.answer(full_info, show_alert=True)

    # 2. Обновляем сообщение в группе для остальных
    updated_group_text = (
        f"✅ <b>ЗАЯВКУ ЗАКРИТО</b>\n\n"
        f"🆔 <b>Об'єкт:</b> {lead_data['obj_id']}\n"
        f"👤 <b>Взяв у роботу:</b> {realtor_tag}"
    )
    await callback.message.edit_text(updated_group_text, parse_mode="HTML")

# ==========================================
# 8. СДЕЛКИ И БУХГАЛТЕРИЯ
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
        logging.error(f"Deal send error: {e}")

    await message.answer("✅ Угоду успішно опубліковано у темі «Сделки»!")
    await state.clear()

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
# 9. ЗАПУСК
# ==========================================
async def main():
    db.init_db()
    scheduler.start()
    await start_web_server()
    
    await bot.delete_webhook(drop_pending_updates=True)

    logging.info("Bot started successfully!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
