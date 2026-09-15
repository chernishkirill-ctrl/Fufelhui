import os
import asyncio
import logging
import re
from datetime import datetime
from aiohttp import ClientSession, web
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
INTERNAL_BASE_ID = "-1005325255205"  # Убедись, что ID базы с -100 если это супергруппа!

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
# 4. ПАРСЕР И ОБРАБОТКА ДАННЫХ
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

    # Если скинули ссылку — пробуем вытянуть данные
    if input_text.startswith("http"):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            async with ClientSession() as session:
                async with session.get(input_text, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html_doc = await resp.text()
                        soup = BeautifulSoup(html_doc, 'html.parser')
                        
                        # Вытягиваем текст
                        paragraphs = [p.get_text().strip() for p in soup.find_all(['p', 'h1', 'div']) if len(p.get_text().strip()) > 30]
                        if paragraphs:
                            raw_text = "\n\n".join(paragraphs[:15])

                        # Вытягиваем картинки
                        for img in soup.find_all('img'):
                            src = img.get('src') or img.get('data-src') or img.get('data-old-src')
                            if src and 'http' in src and ('photos' in src or 'images' in src or 'olx' in src or 'ria' in src):
                                if src not in images and not src.endswith('.svg') and not src.endswith('.gif'):
                                    images.append(src)
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
    phone = phone_match.group(0) if phone_match else "Контакт приховано"

    return {
        "raw_text": raw_text,
        "district": district,
        "bank": bank,
        "address": address,
        "rooms": rooms,
        "area": area,
        "floor": floor,
        "price": price,
        "phone": phone,
        "images": images[:10],
        "object_id": f"ID{datetime.now().strftime('%M%S')}"
    }

async def create_telegraph_page(title: str, text: str, images: list) -> str:
    try:
        async with ClientSession() as session:
            acc_resp = await session.post("https://api.telegra.ph/createAccount", json={"short_name": "Nestima", "author_name": "Nestima Real Estate"})
            acc_data = await acc_resp.json()
            token = acc_data.get("result", {}).get("access_token")

            if not token:
                return ""

            content = []
            # Добавляем фото в Telegraph
            for img_url in images:
                content.append({"tag": "img", "attrs": {"src": img_url}})
            
            # Добавляем текст по абзацам
            paragraphs = text.split("\n")
            for p in paragraphs:
                p_clean = p.strip()
                if p_clean:
                    content.append({"tag": "p", "children": [p_clean]})

            if not content:
                content.append({"tag": "p", "children": ["Детальна інформація за запитом."]})

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
    await message.answer("🛠 **Панель управління Nestima Real Estate:**", reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "add_property")
async def start_add_property(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MY_ADMIN_ID:
        return
    await callback.message.answer("🔗 Надішліть посилання (OLX / DOM.RIA) або опис об'єкта:")
    await state.set_state(FormStates.waiting_for_input)
    await callback.answer()

@dp.message(FormStates.waiting_for_input)
async def process_property_input(message: types.Message, state: FSMContext):
    if message.from_user.id != MY_ADMIN_ID:
        return
    
    status_msg = await message.answer("⏳ Обробка об'єкта та генерація Telegraph...")
    
    try:
        input_text = message.text or ""
        data = await fetch_page_data(input_text)
        
        page_title = f"{data['district']} | {data['rooms']} | {data['price']}"
        telegraph_url = await create_telegraph_page(page_title, data['raw_text'], data['images'])
        
        if not telegraph_url:
            telegraph_url = "https://telegra.ph"

        obj_id = data['object_id']

        # ----------------------------------------------------
        # 1. ПУБЛИКАЦИЯ В ЗАКРЫТУЮ БАЗУ РИЕЛТОРОВ
        # ----------------------------------------------------
        work_text = (
            f"📥 <b>НОВИЙ ОБ'ЄКТ У ВНУТРІШНІЙ БАЗІ</b>\n\n"
            f"🆔 <b>ID:</b> {obj_id}\n"
            f"👤 <b>Контакт:</b> {html.quote(data['phone'])}\n"
            f"📍 <b>Адреса:</b> {html.quote(data['address'])}\n"
            f"🚪 <b>Кімнат:</b> {data['rooms']} | 📐 <b>Площа:</b> {data['area']} | 💰 <b>Ціна:</b> {data['price']}\n\n"
            f"📄 <b>Telegraph:</b> {telegraph_url}\n\n"
            f"📝 <b>Опис:</b>\n{html.quote(data['raw_text'][:600])}"
        )
        
        try:
            if data['images']:
                media = [types.InputMediaPhoto(media=url) for url in data['images'][:10]]
                await bot.send_media_group(chat_id=INTERNAL_BASE_ID, media=media)
            await bot.send_message(chat_id=INTERNAL_BASE_ID, text=work_text, parse_mode="HTML")
        except Exception as err:
            logging.error(f" Ошибка отправки в базу ({INTERNAL_BASE_ID}): {err}")
            await message.answer(f"⚠️ Ошибка отправки в базу! Проверь ID базы `{INTERNAL_BASE_ID}` и права бота. Текст ошибки: {err}", parse_mode="Markdown")

        # ----------------------------------------------------
        # 2. ПУБЛИКАЦИЯ В ПУБЛИЧНЫЙ КАНАЛ
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
            f"📄 **Фото та детальний опис:** {telegraph_url}\n\n"
            f"#Nestima #ОрендаДніпро"
        )

        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="📝 Записатися на перегляд", callback_data=f"book_view_{obj_id}"))

        await bot.send_message(
            chat_id=PUBLIC_CHANNEL_ID, 
            text=public_text, 
            reply_markup=builder.as_markup(), 
            parse_mode="HTML",
            disable_web_page_preview=False
        )

        await status_msg.edit_text(f"✅ **Опубліковано!**\n• Об'єкт {obj_id} відправлено в базу.\n• Пост додано в публічний канал.", parse_mode="Markdown")
        await state.clear()

    except Exception as e:
        logging.error(f"Processing error: {e}")
        await status_msg.edit_text(f"⚠️ Помилка обробки: {e}")
        await state.clear()

# ==========================================
# 6. КЛИЕНТ ЖМЕТ КНОПКУ В КАНАЛЕ
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

# ==========================================
# 7. РИЕЛТОР ЖМЕТ «ПРИНЯТЬ КЛИЕНТА»
# ==========================================
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
