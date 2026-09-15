import os
import asyncio
import logging
import re
from datetime import datetime
from aiohttp import ClientSession, web, FormData
from bs4 import BeautifulSoup

from aiogram import Bot, Dispatcher, F, html, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MY_ADMIN_ID = 8799145351

PUBLIC_CHANNEL_ID = "-1003889243376"
INTERNAL_BASE_ID = "-1003747034156"

GROUP_CHAT_ID = -1004428877093
CHAT_TOPIC_ID = 3

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

PENDING_LEADS = {}
TAKEN_LEADS = {}

# ==========================================
# 2. МИНИ-ВЕБ-СЕРВЕР (ДЛЯ RENDER)
# ==========================================
async def handle_ping(request):
    return web.Response(text="Nestima Bot Active")

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

# ==========================================
# 4. ПАРСИНГ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def extract_bank(text: str) -> str:
    text_l = text.lower()
    left_bank_keywords = ["лівий", "левый", "слобожанский", "калиновая", "правда", "косиора", "березинка", "парус", "левобережный"]
    for kw in left_bank_keywords:
        if kw in text_l:
            return "Лівий берег"
    return "Правий берег"

def extract_district(text: str) -> str:
    text_lower = text.lower()
    districts = {
        "поля": "Поля", "кірова": "Поля", "центр": "Центр", 
        "перемога": "Перемога", "победа": "Перемога", "гагаріна": "Гагаріна", 
        "набережна": "Набережна", "шевченка": "ПаркШевченка", 
        "тополя": "Тополя", "сокіл": "Сокіл", "парус": "Парус"
    }
    for key, val in districts.items():
        if key in text_lower:
            return val
    return "Дніпро"

async def fetch_page_data(input_text: str):
    images = []
    raw_text = input_text
    source_url = ""

    url_match = re.search(r'https?://[^\s]+', input_text)
    if url_match:
        source_url = url_match.group(0)
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7'
            }
            async with ClientSession() as session:
                async with session.get(source_url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html_doc = await resp.text()
                        soup = BeautifulSoup(html_doc, 'html.parser')
                        
                        # Извлекаем все изображения
                        for img in soup.find_all('img'):
                            src = img.get('src') or img.get('data-src') or img.get('srcset')
                            if src:
                                if ' ' in src:
                                    src = src.split(' ')[0]
                                if src.startswith('//'):
                                    src = 'https:' + src
                                if 'http' in src and ('photos' in src or 'images' in src or 'olx' in src or 'ria' in src):
                                    if src not in images and not any(ext in src for ext in ['.svg', '.gif', 'icon', 'logo', 'avatar']):
                                        images.append(src)

                        # Извлекаем текст
                        paragraphs = [p.get_text().strip() for p in soup.find_all(['p', 'div', 'h1', 'h2']) if len(p.get_text().strip()) > 25]
                        if paragraphs:
                            raw_text = "\n\n".join(paragraphs[:20])

        except Exception as e:
            logging.error(f"Parsing error: {e}")

    district = extract_district(raw_text)
    bank = extract_bank(raw_text)
    
    rooms_match = re.search(r'(\d)\s*(?:к|кімн|кімнат|комн)', raw_text, re.IGNORECASE)
    rooms = f"{rooms_match.group(1)}к" if rooms_match else "1к"

    price_match = re.search(r'(\$\s*\d+[\d\s,]*|\d+[\d\s,]*\s*\$|\d+[\d\s,]*\s*грн|\d+[\d\s,]*\s*у\.е\.)', raw_text, re.IGNORECASE)
    price = price_match.group(1).strip() if price_match else "Уточнюється"

    area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:м²|м2|кв\.м)', raw_text, re.IGNORECASE)
    area = f"{area_match.group(1)} м²" if area_match else "Уточнюється"

    floor_match = re.search(r'(\d+)\s*/\s*(\d+)', raw_text)
    floor = f"{floor_match.group(1)}/{floor_match.group(2)}" if floor_match else "Уточнюється"

    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    address = lines[0][:50] if lines else "Об'єкт нерухомості"

    phone_match = re.search(r'\+?\d[\d\s-]{8,}\d', raw_text)
    phone = phone_match.group(0) if phone_match else "Вказано в джерелі"

    return {
        "source_url": source_url,
        "raw_text": raw_text,
        "district": district,
        "bank": bank,
        "address": address,
        "rooms": rooms,
        "area": area,
        "floor": floor,
        "price": price,
        "phone": phone,
        "images": images[:15],
        "object_id": f"ID{datetime.now().strftime('%M%S')}"
    }

async def upload_image_to_telegra_ph(session: ClientSession, image_url: str) -> str:
    try:
        async with session.get(image_url, timeout=8) as resp:
            if resp.status == 200:
                img_data = await resp.read()
                form = FormData()
                form.add_field('file', img_data, filename='photo.jpg', content_type='image/jpeg')
                async with session.post('https://telegra.ph/upload', data=form) as upload_resp:
                    res = await upload_resp.json()
                    if isinstance(res, list) and len(res) > 0 and 'src' in res[0]:
                        return f"https://telegra.ph{res[0]['src']}"
    except Exception as e:
        logging.error(f"Image upload to telegra.ph failed: {e}")
    return ""

async def create_telegraph_page(title: str, text: str, images: list) -> str:
    try:
        async with ClientSession() as session:
            acc_resp = await session.post("https://api.telegra.ph/createAccount", json={"short_name": "Nestima", "author_name": "Nestima Real Estate"})
            acc_data = await acc_resp.json()
            token = acc_data.get("result", {}).get("access_token")

            if not token:
                return ""

            uploaded_images = []
            for img_url in images:
                ph_url = await upload_image_to_telegra_ph(session, img_url)
                if ph_url:
                    uploaded_images.append(ph_url)

            content = []
            
            # 1. Фотографии списком (Альбом)
            for img_path in uploaded_images:
                content.append({"tag": "img", "attrs": {"src": img_path}})
            
            # 2. Небольшое описание внизу
            short_desc = text[:500] if len(text) > 500 else text
            for p in short_desc.split("\n"):
                p_clean = p.strip()
                if p_clean:
                    content.append({"tag": "p", "children": [p_clean]})

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
        logging.error(f"Telegraph creation error: {e}")
    return ""

# ==========================================
# 5. ХЕНДЛЕРЫ БОТА
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    if message.from_user.id != MY_ADMIN_ID:
        return

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Опублікувати об'єкт", callback_data="add_property"))
    builder.row(
        types.InlineKeyboardButton(text="📂 База об'єктів", callback_data="btn_base"),
        types.InlineKeyboardButton(text="📊 Статистика", callback_data="btn_stats")
    )
    builder.row(types.InlineKeyboardButton(text="⚙️ Налаштування", callback_data="btn_settings"))

    await message.answer("🛠 **Панель управління Nestima Real Estate:**", reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "add_property")
async def start_add_property(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MY_ADMIN_ID:
        return
    await callback.message.answer("🔗 Надішліть посилання (OLX / DOM.RIA) або опис об'єкта:")
    await state.set_state(FormStates.waiting_for_input)
    await callback.answer()

@dp.callback_query(F.data == "btn_base")
async def process_btn_base(callback: types.CallbackQuery):
    await callback.answer("📂 Розділ бази активний.", show_alert=True)

@dp.callback_query(F.data == "btn_stats")
async def process_btn_stats(callback: types.CallbackQuery):
    await callback.answer("📊 Статистика в нормі.", show_alert=True)

@dp.callback_query(F.data == "btn_settings")
async def process_btn_settings(callback: types.CallbackQuery):
    await callback.answer("⚙️ Налаштування активні.", show_alert=True)

# ==========================================
# 6. ПУБЛИКАЦИЯ ОБЪЕКТА
# ==========================================
@dp.message(FormStates.waiting_for_input)
async def process_property_input(message: types.Message, state: FSMContext):
    if message.from_user.id != MY_ADMIN_ID:
        return
    
    status_msg = await message.answer("⏳ Обробка об'єкта та створення альбому в Telegraph...")
    
    try:
        input_text = message.text or ""
        data = await fetch_page_data(input_text)
        
        page_title = f"{data['district']} | {data['rooms']} | {data['price']}"
        telegraph_url = await create_telegraph_page(page_title, data['raw_text'], data['images'])
        
        obj_id = data['object_id']

        # ----------------------------------------------------
        # 1. ОТПРАВКА В ЗАКРЫТУЮ БАЗУ (ПОЛНАЯ ИНФОРМАЦИЯ)
        # ----------------------------------------------------
        source_str = f"<a href='{data['source_url']}'>Перейти до джерела</a>" if data['source_url'] else "Не вказано"
        
        work_text = (
            f"📥 <b>НОВИЙ ОБ'ЄКТ У ВНУТРІШНІЙ БАЗІ</b>\n\n"
            f"🆔 <b>ID:</b> {obj_id}\n"
            f"🔗 <b>Джерело:</b> {source_str}\n"
            f"👤 <b>Контакт:</b> {html.quote(data['phone'])}\n"
            f"📍 <b>Адреса:</b> {html.quote(data['address'])}\n"
            f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
            f"📝 <b>Повний опис:</b>\n{html.quote(data['raw_text'])}"
        )
        
        try:
            if data['images']:
                media = [types.InputMediaPhoto(media=url) for url in data['images'][:10]]
                await bot.send_media_group(chat_id=INTERNAL_BASE_ID, media=media)
            await bot.send_message(chat_id=INTERNAL_BASE_ID, text=work_text, parse_mode="HTML", disable_web_page_preview=False)
        except Exception as err:
            logging.error(f"Ошибка отправки в базу: {err}")

        # ----------------------------------------------------
        # 2. ОТПРАВКА В ПУБЛИЧНЫЙ КАНАЛ (БЕЗ КОНТАКТОВ И ID)
        # ----------------------------------------------------
        bank_tag = "#ПравийБерег" if "Правий" in data['bank'] else "#ЛівийБерег"
        district_tag = f"#{data['district'].replace(' ', '')}"
        rooms_tag = f"#{data['rooms']}"

        public_text = (
            f"#Дніпро {bank_tag} {district_tag} {rooms_tag}\n\n"
            f"📍 **Адреса:** {html.quote(data['address'])}\n"
            f"🏢 **Поверх:** {data['floor']}\n"
            f"📐 **Площа:** {data['area']}\n"
            f"🌊 **Берег:** {data['bank']}\n"
            f"💰 **Ціна:** {data['price']}\n\n"
        )
        
        if telegraph_url:
            public_text += f"📄 **Фото та детальний опис:** {telegraph_url}\n\n"

        public_text += "#Nestima #ОрендаДніпро"

        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="📝 Записатися на перегляд", callback_data=f"book_view_{obj_id}"))

        await bot.send_message(
            chat_id=PUBLIC_CHANNEL_ID, 
            text=public_text, 
            reply_markup=builder.as_markup(), 
            parse_mode="HTML",
            disable_web_page_preview=False
        )

        await status_msg.edit_text("✅ **Опубліковано!**\n• Повна картка відправлена в базу.\n• Пост та Telegraph-альбом опубліковано в канал.", parse_mode="Markdown")
        await state.clear()

    except Exception as e:
        logging.error(f"Processing error: {e}")
        await status_msg.edit_text(f"⚠️ Помилка обробки: {e}")
        await state.clear()

# ==========================================
# 7. ОБРАБОТКА ЗАЯВОК (ПЕРЕХВАТ В ГРУППУ)
# ==========================================
@dp.callback_query(F.data.startswith("book_view_"))
async def process_inline_booking(callback: types.CallbackQuery):
    obj_id = callback.data.split("book_view_")[1]
    user = callback.from_user
    
    client_tag = f"@{user.username}" if user.username else f"ID: {user.id}"
    client_name = user.full_name
    
    lead_id = f"LEAD-{datetime.now().strftime('%M%S')}"
    
    PENDING_LEADS[lead_id] = {
        "obj_id": obj_id,
        "name": client_name,
        "username": client_tag
    }

    await callback.answer("✅ Вашу заявку прийнято! Ріелтор зв'яжеться з вами.", show_alert=True)

    group_text = (
        f"🔔 **НОВА ЗАЯВКА НА ПЕРЕГЛЯД!**\n\n"
        f"🆔 **Об'єкт:** {obj_id}\n"
        f"👤 **Клієнт:** {client_name} ({client_tag})\n\n"
        f"⚡️ *Натисніть кнопку, щоб взяти клієнта в роботу.*"
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🙋‍♂️ Прийняти клієнта", callback_data=f"take_client_{lead_id}"))

    try:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            message_thread_id=CHAT_TOPIC_ID,
            text=group_text,
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error sending lead to group: {e}")

@dp.callback_query(F.data.startswith("take_client_"))
async def accept_client_handler(callback: types.CallbackQuery):
    lead_id = callback.data.split("take_client_")[1]

    if lead_id in TAKEN_LEADS:
        taken_by = TAKEN_LEADS[lead_id]
        await callback.answer(f"❌ Заявку вже взяв {taken_by}!", show_alert=True)
        return

    lead_data = PENDING_LEADS.get(lead_id)
    if not lead_data:
        await callback.answer("⚠️ Дані застаріли.", show_alert=True)
        return

    realtor = callback.from_user
    realtor_tag = f"@{realtor.username}" if realtor.username else realtor.full_name
    TAKEN_LEADS[lead_id] = realtor_tag

    full_info = (
        f"🔑 КЛІЄНТА ПЕРЕХОПЛЕНО!\n\n"
        f"🆔 Об'єкт: {lead_data['obj_id']}\n"
        f"👤 Клієнт: {lead_data['name']}\n"
        f"💬 Контакт: {lead_data['username']}"
    )
    await callback.answer(full_info, show_alert=True)

    updated_group_text = (
        f"✅ **ЗАЯВКУ ЗАКРИТО**\n\n"
        f"🆔 **Об'єкт:** {lead_data['obj_id']}\n"
        f"👤 **Клієнт:** {lead_data['name']} ({lead_data['username']})\n"
        f"💼 **Взяв у роботу:** {realtor_tag}"
    )
    await callback.message.edit_text(updated_group_text, parse_mode="Markdown")

# ==========================================
# 8. ЗАПУСК
# ==========================================
async def main():
    db.init_db()
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot started successfully!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
