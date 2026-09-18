import os
import asyncio
import logging
import re
from datetime import datetime
from aiohttp import web, ClientSession, FormData
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

PUBLIC_CHANNEL_ID = "-1004464251419"
INTERNAL_BASE_ID = "-1004357065341"

GROUP_CHAT_ID = -1004357065341
CHAT_TOPIC_ID = 3
DEALS_TOPIC_ID = 5

CONTACT_URL = "https://t.me/gggggsre"

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

# ==========================================
# ТОЧЕЧНЫЙ ПАРСЕР DIM.RIA / OLX И TELEGRAPH
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
        "космічна": "Космічна", "шевченка": "Шевченка"
    }
    for key, val in districts.items():
        if key in text_lower:
            return val
    return "ПравийБерег"

async def fetch_page_data(input_text: str):
    raw_text = input_text
    images = []
    source_url = ""

    url_match = re.search(r'https?://[^\s]+', input_text)
    if url_match:
        source_url = url_match.group(0)
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'uk-UA,uk;q=0.9,en;q=0.8',
                'Connection': 'keep-alive',
            }
            async with ClientSession() as session:
                async with session.get(source_url, headers=headers, timeout=15) as resp:
                    if resp.status == 200:
                        html_doc = await resp.text()
                        soup = BeautifulSoup(html_doc, 'html.parser')
                        
                        # Сбор всех картинок объекта
                        for img in soup.find_all(['img', 'source']):
                            src = img.get('src') or img.get('data-src') or img.get('srcset')
                            if src:
                                if src.startswith('//'):
                                    src = 'https:' + src
                                if 'http' in src and not any(x in src for x in ['logo', 'icon', 'avatar', '.svg', 'spinner']):
                                    clean_src = src.split(' ')[0]
                                    if clean_src not in images:
                                        images.append(clean_src)

                        # ТОЧЕЧНЫЙ ПОИСК ОПИСАНИЯ (исключаем меню сайтов)
                        content_block = soup.find('div', class_=re.compile(r'(description|realty-description|text|details)', re.I))
                        if content_block:
                            raw_text = content_block.get_text(separator="\n", strip=True)
                        else:
                            # Запасной вариант: берем параграфы, исключая навигационные теги
                            for tag in soup(['nav', 'header', 'footer', 'script', 'style']):
                                tag.decompose()
                            paragraphs = [p.get_text(strip=True) for p in soup.find_all(['p', 'div']) if len(p.get_text(strip=True)) > 20]
                            if paragraphs:
                                raw_text = "\n".join(paragraphs[:30])
        except Exception as e:
            logging.error(f"HTTP fetch error: {e}")

    district = extract_district(raw_text)
    
    # Парсим комнаты
    rooms_match = re.search(r'(\d)\s*(?:к|кімн|кімнат|комн)', raw_text, re.IGNORECASE)
    rooms = f"{rooms_match.group(1)}к" if rooms_match else "2к"

    # Парсим цену
    price_match = re.search(r'(\$\s*\d+[\d\s,]*|\d+[\d\s,]*\s*\$|\d+[\d\s,]*\s*грн|\d+[\d\s,]*\s*у\.е\.)', raw_text, re.IGNORECASE)
    price = price_match.group(1).strip() if price_match else "Уточнюється"

    # Парсим площадь
    area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:м²|м2|кв\.м)', raw_text, re.IGNORECASE)
    area = f"{area_match.group(1)} м²" if area_match else "Уточнюється"

    # Парсим этаж
    floor_match = re.search(r'(?:поверх:?\s*)?(\d{1,2}\s*/\s*\d{1,2})', raw_text, re.IGNORECASE)
    floor = floor_match.group(1) if floor_match else "Уточнюється"

    # Парсим адрес
    lines = [line.strip() for line in raw_text.split('\n') if line.strip() and len(line.strip()) < 60]
    address = lines[0] if lines else "вул. Центральна, 1"

    # Парсим телефон владельца
    phone_match = re.search(r'\+?380\s*\(?\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}|\+?\d[\d\s-]{8,}\d', raw_text)
    phone = phone_match.group(0) if phone_match else "Не вказано"

    return {
        "source_url": source_url,
        "raw_text": raw_text,
        "clean_text": clean_sensitive_info(raw_text[:1200]),
        "district": district,
        "address": address,
        "rooms": rooms,
        "area": area,
        "floor": floor,
        "price": price,
        "phone": phone,
        "images": images[:10],
        "object_id": f"ID-{datetime.now().strftime('%M%S')}"
    }

async def upload_to_telegraph(image_url: str) -> str:
    try:
        async with ClientSession() as session:
            async with session.get(image_url, timeout=8) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    form = FormData()
                    form.add_field('file', data, filename='img.jpg', content_type='image/jpeg')
                    async with session.post('https://telegra.ph/upload', data=form) as up_resp:
                        res = await up_resp.json()
                        if isinstance(res, list) and 'src' in res[0]:
                            return f"https://telegra.ph{res[0]['src']}"
    except Exception as e:
        logging.error(f"Telegraph image upload error: {e}")
    return ""

async def create_telegraph_page(title: str, text: str, images: list) -> str:
    try:
        async with ClientSession() as session:
            acc_resp = await session.post("https://api.telegra.ph/createAccount", json={"short_name": "Nestima", "author_name": "Nestima Real Estate"})
            acc_data = await acc_resp.json()
            token = acc_data.get("result", {}).get("access_token")
            if not token:
                return "https://telegra.ph"

            content = []
            for img_url in images[:6]:
                ph_url = await upload_to_telegraph(img_url)
                if ph_url:
                    content.append({"tag": "img", "attrs": {"src": ph_url}})

            content.append({"tag": "p", "children": [text[:1000]]})

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
        logging.error(f"Telegraph page creation error: {e}")
    return "https://telegra.ph"

# ==========================================
# ХЕНДЛЕРЫ БОТА
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != MY_ADMIN_ID:
        return
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Опублікувати об'єкт (Аренда)", callback_data="add_property"))
    builder.row(types.InlineKeyboardButton(text="🎉 Зафіксувати угоду", callback_data="add_deal"))
    builder.row(types.InlineKeyboardButton(text="📊 Бухгалтерія", callback_data="view_accounting"))
    await message.answer("🛠 <b>Панель керівника (Nestima Rent):</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "add_property")
async def start_add_property(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🔗 <b>Надішліть посилання</b> на оголошення (OLX, DOM.RIA):")
    await state.set_state(FormStates.waiting_for_input)
    await callback.answer()

@dp.message(FormStates.waiting_for_input)
async def process_property_input(message: types.Message, state: FSMContext):
    status_msg = await message.answer("⏳ Витягуємо дані, фото та створюємо Telegraph...")
    
    input_text = message.text or message.caption or ""
    data = await fetch_page_data(input_text)
    
    page_title = f"Об'єкт №{data['object_id']}"
    telegraph_url = await create_telegraph_page(page_title, data['clean_text'], data['images'])
    
    obj_id = data['object_id']
    PENDING_POSTS[obj_id] = {**data, "telegraph_url": telegraph_url}

    source_str = f"<a href='{data['source_url']}'>Посилання на джерело</a>" if data['source_url'] else "Не вказано"
    
    work_text = (
        f"📥 <b>НОВИЙ ОБ'ЄКТ У БАЗІ (ID: {obj_id})</b>\n\n"
        f"🔗 <b>Джерело:</b> {source_str}\n"
        f"👤 <b>Контакт власника:</b> {html.quote(data['phone'])}\n"
        f"📍 <b>Адреса:</b> {html.quote(data['address'])}\n"
        f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 🔺 <b>Поверх:</b> {data['floor']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
        f"📄 <b>Telegraph альбом:</b> {telegraph_url}\n\n"
        f"📝 <b>Знайдено фоток:</b> {len(data['images'])}"
    )
    try:
        await bot.send_message(chat_id=INTERNAL_BASE_ID, text=work_text, parse_mode="HTML", disable_web_page_preview=False)
    except Exception as e:
        logging.error(f"Error sending to base: {e}")

    preview_text = (
        f"✅ <b>Об'єкт {obj_id} повністю розпізнано!</b>\n\n"
        f"🚪 {data['rooms']} | 📐 {data['area']} | 🔺 Поверх: {data['floor']}\n"
        f"📍 {data['address']}\n"
        f"💰 {data['price']}\n"
        f"🔗 <a href='{telegraph_url}'>Перевірити створений Telegraph</a>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📢 Опублікувати в Публічний канал", callback_data=f"pub_{obj_id}"))

    await status_msg.edit_text(preview_text, reply_markup=builder.as_markup(), parse_mode="HTML", disable_web_page_preview=True)
    await state.clear()

@dp.callback_query(F.data.startswith("pub_"))
async def publish_to_public(callback: types.CallbackQuery):
    obj_id = callback.data.split("pub_")[1]
    data = PENDING_POSTS.get(obj_id)

    if not data:
        await callback.answer("⚠️ Дані застаріли. Створіть об'єкт заново.", show_alert=True)
        return

    public_text = (
        f"{data['rooms']}\n"
        f"📐 {data['area']}\n"
        f"🔺 Поверх: {data['floor']} ⚠️\n"
        f"📍 {data['address']}\n"
        f"💰 {data['price']}\n\n"
        f"📄 Деталі та фото: <a href='{data['telegraph_url']}'>Дивитися огляд</a>\n\n"
        f"#{data['district']}{data['rooms']}\n"
        f"#ПравийБерег\n"
        f"#Nestima"
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📄 Дивитися фото та огляд", url=data['telegraph_url']))
    builder.row(types.InlineKeyboardButton(text="📝 Записатися на перегляд", url=CONTACT_URL))

    try:
        await bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=public_text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.message.edit_text(f"🚀 <b>Об'єкт {obj_id} опубліковано в публічний канал!</b>", parse_mode="HTML")
    except Exception as e:
        await callback.message.answer(f"⚠️ Помилка публікації: {e}")
    
    await callback.answer()

@dp.callback_query(F.data == "add_deal")
async def start_add_deal(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("👤 Вкажіть @username ріелтора:")
    await state.set_state(FormStates.waiting_for_deal_realtor)
    await callback.answer()

@dp.callback_query(F.data == "view_accounting")
async def view_accounting_menu(callback: types.CallbackQuery):
    income, expenses = db.get_financial_summary()
    net_profit = income - expenses
    await callback.message.answer(f"📊 <b>Фінансовий звіт:</b>\nДоходи: {income}\nВитрати: {expenses}\nПрибуток: {net_profit}", parse_mode="HTML")
    await callback.answer()

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    db.init_db()
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot started successfully!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
