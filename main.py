import os
import logging
import asyncio
import urllib.parse
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from telegraph import Telegraph

logging.basicConfig(level=logging.INFO)

TOKEN = "8900731470:AAEJXLvb6aNp7m9H_QnlxX8FKO9ZnF14Nmo"
PUBLIC_CHANNEL_ID = "-1003889243376"
AGENT_WORK_CHAT_ID = -1004428877093
MY_ADMIN_ID = 8799145351

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

telegraph = Telegraph()
telegraph.create_account(short_name='NestimaRealEstate')

ACTIVE_AGENTS = [11111111, 22222222] 
reports_storage = {} 

class FormStates(StatesGroup):
    waiting_for_object_data = State()

async def handle_keep_alive(request):
    return web.Response(text="I am alive and working 24/7!")

# Веб-сервер для приема данных из Web App формы прямо из канала
async def handle_webapp_data(request):
    try:
        data = await request.json()
        obj_id = data.get("obj_id")
        client_name = data.get("name")
        client_phone = data.get("phone")
        
        if not obj_id or not client_name or not client_phone:
            return web.json_response({"status": "error", "message": "Missing data"}, status=400)

        # Отправляем заявку в рабочий чат риелторов
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="🟢 Прийняти заявку", callback_data=f"claim_lead_{obj_id}_{client_phone}"))

        await bot.send_message(
            chat_id=AGENT_WORK_CHAT_ID,
            text=(
                f"😱😱 ЗАЯВКА НА ПЕРЕГЛЯД (Об'єкт №{obj_id}) 😱😱\n\n"
                f"👤 **Клієнт:** {client_name}\n"
                f"📞 **Телефон:** `{client_phone}`"
            ),
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
        return web.json_response({"status": "ok"})
    except Exception as e:
        logging.error(f"Webapp data error: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)

# HTML-страница формы, которая открывается во всплывающем окне Web App внутри канала
async def handle_form_page(request):
    obj_id = request.match_info.get("obj_id")
    html_content = f"""
    <!DOCTYPE html>
    <html lang="uk">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Запис на перегляд</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #0f0f0f; color: #fff; padding: 20px; }}
            .container {{ max-width: 400px; margin: 0 auto; background: #1a1a1a; padding: 20px; border-radius: 12px; }}
            input {{ width: 100%; padding: 12px; margin: 10px 0; background: #2a2a2a; border: 1px solid #444; color: #fff; border-radius: 8px; box-sizing: border-box; font-size: 16px; }}
            button {{ width: 100%; padding: 14px; background: #2ea6ff; color: white; border: none; border-radius: 8px; font-weight: bold; font-size: 16px; cursor: pointer; margin-top: 10px; }}
            button:active {{ background: #1885d1; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>📝 Запис на перегляд</h2>
            <p>Введіть ваші дані для зв'язку:</p>
            <input type="text" id="name" placeholder="Ваше ім'я" required>
            <input type="tel" id="phone" placeholder="Номер телефону (+380...)" required>
            <button onclick="submitForm()">Надіслати заявку</button>
        </div>
        <script>
            let tg = window.Telegram.WebApp;
            tg.expand();
            function submitForm() {{
                let name = document.getElementById('name').value;
                let phone = document.getElementById('phone').value;
                if(!name || !phone) {{
                    alert('Будь ласка, заповніть всі поля!');
                    return;
                }}
                fetch('/api/submit', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ obj_id: "{obj_id}", name: name, phone: phone }})
                }}).then(res => {{
                    if(res.ok) {{
                        tg.close();
                    }} else {{
                        alert('Помилка відправки. Спробуйте ще раз.');
                    }}
                }});
            }}
        </script>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != MY_ADMIN_ID:
        await message.answer("Цей бот є закритим пунктом управління агентством нерухомості Nestima.")
        return

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Додати об'єкт за посиланням", callback_data="add_object"))
    builder.row(types.InlineKeyboardButton(text="📊 Переглянути звіти за сьогодні", callback_data="view_reports"))
    
    await message.answer(
        "Вітаю, босе! Це пульт управління агентством **Nestima**.\nОбери необхідну дію:",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "add_object")
async def process_add_object(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MY_ADMIN_ID:
        return
    await callback.message.answer(
        "Надішли мені **посилання на оголошення**.\n"
        "Бот спарсить параметри, сформує мінімалістичний публічний пост та збереже службову базу!"
    )
    await state.set_state(FormStates.waiting_for_object_data)
    await callback.answer()

def parse_listing(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8"
    }
    data = {
        "rooms": "", "area": "", "floor": "", "bank": "",
        "address": "Дніпро", "price": "Ціна за запитом",
        "district_hashtag": "#Дніпро", "bank_hashtag": "#Нерухомість",
        "title": "Об'єкт нерухомості Nestima",
        "description": "Сучасна квартира в зручному районі міста.",
        "photos": [], "phone": "Не вказано"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return data
        soup = BeautifulSoup(resp.text, 'html.parser')
        h1 = soup.find('h1')
        if h1: data["title"] = h1.get_text(strip=True)
        
        desc_div = soup.find('div', class_='page-description') or soup.find('div', class_='description') or soup.find('div', class_='realty-description')
        if desc_div: data["description"] = desc_div.get_text(separator='\n', strip=True)
            
        address_tag = soup.find('span', class_='realty-address') or soup.find('div', class_='address')
        if address_tag:
            addr_text = address_tag.get_text(strip=True)
            data["address"] = addr_text
            lower_addr = addr_text.lower()
            if any(b in lower_addr for b in ["правобереж", "центр", "топол", "перемог", "парус", "нагорівк", "побед", "шевченківськ", "соборн"]):
                data["bank"] = "Правий Берег"
                data["bank_hashtag"] = "#ПравийБерег"
            else:
                data["bank"] = "Лівий Берег"
                data["bank_hashtag"] = "#ЛівийБерег"

        price_tag = soup.find('span', class_='price') or soup.find('div', class_='price')
        if price_tag: data["price"] = price_tag.get_text(strip=True)

        full_text_page = soup.get_text()
        rooms_match = re.search(r'(\d)\s*-(?:кімн|комн)|(\d)\s*кімн', full_text_page, re.IGNORECASE)
        if rooms_match:
            rooms_num = rooms_match.group(1) or rooms_match.group(2)
            data["rooms"] = f"{rooms_num}к"
            data["district_hashtag"] += f"{rooms_num}к"

        area_match = re.search(r'(\d+[.,]?\d*)\s*(?:м²|м2|кв\.?\s*м)', full_text_page, re.IGNORECASE)
        if area_match: data["area"] = f"{area_match.group(1)}м²"

        floor_match = re.search(r'(\d{1,2})\s*/\s*(\d{1,2})\s*пов', full_text_page, re.IGNORECASE)
        if floor_match: data["floor"] = f"{floor_match.group(1)}/{floor_match.group(2)}"

        phone_tag = soup.find('span', class_='phone') or soup.find('a', class_='phone')
        if phone_tag: data["phone"] = phone_tag.get_text(strip=True)
            
        for img in soup.find_all('img', src=True):
            src = img['src']
            if any(x in src for x in ['photos', 'rio', 'dom.ria', 'realty', 'images']):
                if src.startswith('http') and src not in data["photos"]:
                    data["photos"].append(src)
    except Exception as e:
        logging.error(f"Error parsing URL: {e}")
    return data

@dp.message(FormStates.waiting_for_object_data)
async def handle_object_data(message: types.Message, state: FSMContext):
    if message.from_user.id != MY_ADMIN_ID:
        return
    
    user_input = message.text or message.caption
    if not user_input or "http" not in user_input:
        await message.answer("❌ Надішли коректне посилання на оголошення!")
        return

    await message.answer("⏳ Обробляю дані, формую чисту публікацію та службову базу...")

    words = user_input.split()
    url = next((w for w in words if w.startswith("http")), user_input.strip())
    parsed_info = parse_listing(url)

    try:
        obj_id = int(datetime.now().timestamp()) % 100000

        telegraph_html = f"<h3>{parsed_info['title']}</h3>"
        for p in parsed_info["photos"]:
            telegraph_html += f'<img src="{p}"/>'
        telegraph_html += f"<p><b>Локація:</b> {parsed_info['address']}</p>"
        telegraph_html += f"<p><b>Ціна:</b> {parsed_info['price']}</p>"
        if parsed_info['floor']: telegraph_html += f"<p><b>Поверх:</b> {parsed_info['floor']}</p>"
        telegraph_html += f"<h4>Детальний опис та характеристики:</h4>"
        telegraph_html += f"<p>{parsed_info['description'].replace(chr(10), '<br>')}</p>"
        
        response = telegraph.create_page(title=f"Obj №{obj_id}", html_content=telegraph_html)
        page_path = response.get('path') if isinstance(response, dict) else response
        telegraph_url = f"https://telegra.ph/{page_path}"

        channel_parts = []
        if parsed_info['rooms']: channel_parts.append(f"🏠 {parsed_info['rooms']}")
        if parsed_info['area']: channel_parts.append(f"📐 {parsed_info['area']}")
        if parsed_info['price']: channel_parts.append(f"💵 {parsed_info['price']}")
        if parsed_info['address']: channel_parts.append(f"📍 {parsed_info['address']}")

        channel_text = (
            "\n".join(channel_parts) + 
            f"\n\n📄 **Всі деталі та фото:** {telegraph_url}\n\n"
            f"{parsed_info['district_hashtag']}\n"
            f"{parsed_info['bank_hashtag']}\n"
            f"#Nestima"
        )

        maps_query = parsed_info['address'] if parsed_info['address'] else "Дніпро"
        maps_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(maps_query)}"
        
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "https://fufelhui.onrender.com").rstrip('/')
        webapp_url = f"{render_url}/form/{obj_id}"

        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(text="📍 На мапі", url=maps_url),
            types.InlineKeyboardButton(text="📝 Записатися на перегляд", web_app=types.WebAppInfo(url=webapp_url))
        )

        await bot.send_message(
            chat_id=PUBLIC_CHANNEL_ID, 
            text=channel_text, 
            reply_markup=builder.as_markup(), 
            parse_mode="Markdown"
        )

        work_chat_text = (
            f"📥 **Новий об'єкт №{obj_id} у робочій базі!**\n\n"
            f"📌 **Назва:** {parsed_info['title']}\n"
            f"📍 **Адреса:** {parsed_info['address']}\n"
            f"📞 **Телефон власника / ріелтора:** `{parsed_info['phone']}`\n"
            f"📄 **Telegraph:** {telegraph_url}\n\n"
            f"📄 **Опис:**\n{parsed_info['description']}\n\n"
            f"🔗 **Посилання на джерело:** {url}"
        )
        
        await bot.send_message(chat_id=AGENT_WORK_CHAT_ID, text=work_chat_text, parse_mode="Markdown")

        if parsed_info["photos"]:
            work_media_group = [types.InputMediaPhoto(media=p) for p in parsed_info["photos"][:10]]
            await bot.send_media_group(chat_id=AGENT_WORK_CHAT_ID, media=work_media_group)

        await message.answer(f"✅ Успішно! Чистий пост опубліковано у канал через Web App форму.")
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("claim_lead_"))
async def claim_lead_action(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    obj_id = parts[2]
    client_phone = parts[3]
    agent_name = callback.from_user.full_name

    await callback.message.edit_text(
        text=f"😱😱 ЗАЯВКА НА ПЕРЕГЛЯД (Об'єкт №{obj_id}) 😱😱\n\n🔒 **Заброньовано агентом:** {agent_name}\n📞 **Телефон клієнта:** `{client_phone}`",
        reply_markup=None,
        parse_mode="Markdown"
    )
    await callback.answer("✅ Заявку успішно закріплено за вами.", show_alert=True)

# Обработка входящих вебхуков от Telegram
async def handle_telegram_webhook(request):
    try:
        data = await request.json()
        telegram_update = types.Update(**data)
        await dp.feed_update(bot=bot, update=telegram_update)
        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return web.Response(status=500)

async def main():
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if render_url:
        webhook_url = f"{render_url.rstrip('/')}/webhook"
        await bot.set_webhook(webhook_url)
        logging.info(f"Webhook successfully set to {webhook_url}")

    app = web.Application()
    app.router.add_get("/", handle_keep_alive)
    app.router.add_get("/form/{obj_id}", handle_form_page)
    app.router.add_post("/api/submit", handle_webapp_data)
    app.router.add_post("/webhook", handle_telegram_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
