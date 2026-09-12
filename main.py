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
lead_details_storage = {} 

class FormStates(StatesGroup):
    waiting_for_object_data = State()
    waiting_for_agent_report = State()

async def handle_keep_alive(request):
    return web.Response(text="I am alive and working 24/7!")

async def handle_webapp_form(request):
    obj_id = request.match_info.get('obj_id', '')
    html_content = f"""
    <!DOCTYPE html>
    <html lang="uk">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Заявка на перегляд - Nestima</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                background-color: var(--tg-theme-bg-color, #ffffff);
                color: var(--tg-theme-text-color, #000000);
                padding: 20px;
                margin: 0;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                height: 90vh;
            }}
            .container {{
                width: 100%;
                max-width: 350px;
                background: var(--tg-theme-secondary-bg-color, #f5f5f5);
                padding: 20px;
                border-radius: 14px;
                box-sizing: border-box;
            }}
            h2 {{
                text-align: center;
                font-size: 20px;
                margin-bottom: 20px;
            }}
            .form-group {{
                margin-bottom: 15px;
            }}
            label {{
                display: block;
                font-size: 14px;
                margin-bottom: 5px;
                font-weight: 500;
            }}
            input {{
                width: 100%;
                padding: 12px;
                border: 1px solid #ccc;
                border-radius: 8px;
                font-size: 16px;
                box-sizing: border-box;
                background: var(--tg-theme-bg-color, #ffffff);
                color: var(--tg-theme-text-color, #000000);
            }}
            button {{
                width: 100%;
                padding: 14px;
                background-color: var(--tg-theme-button-color, #2481cc);
                color: var(--tg-theme-button-text-color, #ffffff);
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: bold;
                cursor: pointer;
                margin-top: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>🏠 Запис на перегляд (Об'єкт №{obj_id})</h2>
            <div class="form-group">
                <label for="name">Ваше ім'я:</label>
                <input type="text" id="name" placeholder="Введіть ваше ім'я" required>
            </div>
            <div class="form-group">
                <label for="phone">Номер телефону:</label>
                <input type="tel" id="phone" placeholder="+380XXXXXXXXX" required>
            </div>
            <button onclick="submitData()">Надіслати заявку</button>
        </div>

        <script>
            let tg = window.Telegram.WebApp;
            tg.expand();

            function submitData() {{
                let name = document.getElementById('name').value.trim();
                let phone = document.getElementById('phone').value.trim();

                if (!name || !phone) {{
                    alert('Будь ласка, заповніть всі поля!');
                    return;
                }}

                let data = {{
                    obj_id: "{obj_id}",
                    name: name,
                    phone: phone
                }};

                tg.sendData(JSON.stringify(data));
                tg.close();
            }}
        </script>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_keep_alive)
    app.router.add_get("/form/{obj_id}", handle_webapp_form)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")

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

@dp.message(F.web_app_data)
async def process_webapp_data(message: types.Message):
    import json
    try:
        data = json.loads(message.web_app_data.data)
        obj_id = data.get("obj_id")
        client_name = data.get("name")
        client_phone = data.get("phone")

        lead_id = int(datetime.now().timestamp())
        lead_details_storage[lead_id] = {
            "obj_id": obj_id,
            "client_name": client_name,
            "client_phone": client_phone,
            "client_username": f"@{message.from_user.username}" if message.from_user.username else "Не вказано"
        }

        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="🟢 Прийняти заявку", callback_data=f"claim_lead_{lead_id}"))

        await bot.send_message(
            chat_id=AGENT_WORK_CHAT_ID,
            text=f"😱😱 ЗАЯВКА НА ПЕРЕГЛЯД (Об'єкт №{obj_id}) 😱😱",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )

        await message.answer("✅ Дякуємо! Вашу заявку успішно надіслано агентам. Менеджер зв'яжеться з вами найближчим часом.")
    except Exception as e:
        logging.error(f"Error parsing web_app_data: {e}")
        await message.answer("❌ Сталася помилка при обробці форми. Спробуйте ще раз.")

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
        "rooms": "",
        "area": "",
        "floor": "",
        "bank": "",
        "address": "Дніпро",
        "price": "Ціна за запитом",
        "district_hashtag": "#Дніпро",
        "bank_hashtag": "#Нерухомість",
        "title": "Об'єкт нерухомості Nestima",
        "description": "Сучасна квартира в зручному районі міста.",
        "photos": [],
        "phone": "Не вказано"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return data
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        h1 = soup.find('h1')
        if h1:
            data["title"] = h1.get_text(strip=True)
            
        desc_div = (
            soup.find('div', class_='page-description') or 
            soup.find('div', class_='description') or 
            soup.find('div', id='description-area') or
            soup.find('span', class_='full-description') or
            soup.find('div', class_='realty-description')
        )
        if desc_div:
            data["description"] = desc_div.get_text(separator='\n', strip=True)
            
        address_tag = (
            soup.find('span', class_='realty-address') or 
            soup.find('div', class_='address') or
            soup.find('h4', class_='address') or
            soup.find('span', class_='address')
        )
        if address_tag:
            addr_text = address_tag.get_text(strip=True)
            data["address"] = addr_text
            
            lower_addr = addr_text.lower()
            if any(b in lower_addr for b in ["правобереж", "центр", "топол", "перемог", "парус", "нагорівк", "побед", "шевченківськ", "соборн", "центральн"]):
                data["bank"] = "Правий Берег"
                data["bank_hashtag"] = "#ПравийБерег"
            else:
                data["bank"] = "Лівий Берег"
                data["bank_hashtag"] = "#ЛівийБерег"

            if "топол" in lower_addr:
                data["district_hashtag"] = "#Тополя"
            elif "центр" in lower_addr:
                data["district_hashtag"] = "#Центр"
            elif "перемог" in lower_addr or "побед" in lower_addr:
                data["district_hashtag"] = "#Перемога"
            elif "правд" in lower_addr or "слобожанск" in lower_addr:
                data["district_hashtag"] = "#Слобожанський"
            elif "парус" in lower_addr:
                data["district_hashtag"] = "#Парус"

        price_tag = soup.find('span', class_='price') or soup.find('div', class_='price') or soup.find('strong', class_='price')
        if price_tag:
            p_text = price_tag.get_text(strip=True)
            if any(curr in p_text for curr in ["грн", "$", "€", "₴"]):
                data["price"] = p_text

        full_text_page = soup.get_text()
        
        rooms_match = re.search(r'(\d)\s*-(?:кімн|комн)|(\d)\s*кімн', full_text_page, re.IGNORECASE)
        if rooms_match:
            rooms_num = rooms_match.group(1) or rooms_match.group(2)
            data["rooms"] = f"{rooms_num}к"
            data["district_hashtag"] += f"{rooms_num}к"

        area_match = re.search(r'(\d+[.,]?\d*)\s*(?:м²|м2|кв\.?\s*м)', full_text_page, re.IGNORECASE)
        if area_match:
            data["area"] = f"{area_match.group(1)}м²"

        floor_match = re.search(r'(\d{1,2})\s*/\s*(\d{1,2})\s*пов', full_text_page, re.IGNORECASE)
        if floor_match:
            data["floor"] = f"{floor_match.group(1)}/{floor_match.group(2)}"

        phone_tag = soup.find('span', class_='phone') or soup.find('a', class_='phone') or soup.find('div', class_='phone-number')
        if phone_tag:
            data["phone"] = phone_tag.get_text(strip=True)
            
        for img in soup.find_all('img', src=True):
            src = img['src']
            if any(x in src for x in ['photos', 'rio', 'dom.ria', 's.dom.ria', 'realty', 'images']):
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
        if parsed_info['floor']:
            telegraph_html += f"<p><b>Поверх:</b> {parsed_info['floor']}</p>"
            
        telegraph_html += f"<h4>Детальний опис та характеристики:</h4>"
        telegraph_html += f"<p>{parsed_info['description'].replace(chr(10), '<br>')}</p>"
        
        response = telegraph.create_page(title=f"Об'єкт №{obj_id}", html_content=telegraph_html)
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
        
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
        if not render_url or not render_url.startswith("http"):
            render_url = "https://fufelhui.onrender.com"
        webapp_url = f"{render_url.rstrip('/')}/form/{obj_id}"

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

        await message.answer(f"✅ Успішно! Чистий пост опубліковано у канал, а повна база збережена для агенства.")
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("claim_lead_"))
async def claim_lead_action(callback: types.CallbackQuery):
    lead_id = int(callback.data.split("_")[-1])
    agent_id = callback.from_user.id
    agent_name = callback.from_user.full_name

    if lead_id not in lead_details_storage:
        await callback.answer("⚠️ Цю заявку вже хтось прийняв!", show_alert=True)
        return

    lead_data = lead_details_storage.pop(lead_id) 

    await callback.message.edit_text(
        text=f"😱😱 ЗАЯВКА НА ПЕРЕГЛЯД 😱😱\n\n🔒 **Заброньовано агентом:** {agent_name}",
        reply_markup=None,
        parse_mode="Markdown"
    )

    try:
        await bot.send_message(
            chat_id=agent_id,
            text=(
                f"🎉 **Ви успішно прийняли заявку в роботу!**\n\n"
                f"📌 **Об'єкт №:** {lead_data['obj_id']}\n"
                f"👤 **Ім'я клієнта:** {lead_data['client_name']}\n"
                f"📱 **Номер телефону:** `{lead_data['client_phone']}`\n"
                f"💬 **Telegram:** {lead_data['client_username']}"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Failed to send DM to agent: {e}")

    await callback.answer("✅ Контакти клієнта надіслано вам у особисті повідомлення.", show_alert=True)

async def schedule_daily_reports():
    while True:
        now = datetime.now()
        target_time = now.replace(hour=22, minute=0, second=0, microsecond=0)
        
        if now >= target_time:
            target_time += timedelta(days=1)
            
        sleep_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(sleep_seconds)

        today_str = datetime.now().strftime("%Y-%m-%d")
        for agent_id in ACTIVE_AGENTS:
            builder = InlineKeyboardBuilder()
            builder.row(types.InlineKeyboardButton(
                text="📝 Здати мій звіт за сьогодні", 
                callback_data=f"submit_report_{agent_id}_{today_str}"
            ))
            
            await bot.send_message(
                chat_id=AGENT_WORK_CHAT_ID,
                text=f"⏰ Час вечірнього звіту для агента (ID: {agent_id}). Натисніть кнопку нижче, щоб надіслати результати дня.",
                reply_markup=builder.as_markup()
            )

@dp.callback_query(F.data.startswith("submit_report_"))
async def agent_click_report(callback: types.CallbackQuery, state: FSMContext = None):
    data_args = callback.data.split("_")
    agent_id = int(data_args[2])
    
    if callback.from_user.id != agent_id:
        await callback.answer("⚠️ Це чужа персональна кнопка звіту!", show_alert=True)
        return

    await callback.message.answer("Будь ласка, напишіть короткий текст вашого звіту за сьогодні (дзвінки, покази, угоди):")
    await callback.answer()

@dp.message(FormStates.waiting_for_agent_report)
async def save_agent_report(message: types.Message, state: FSMContext):
    agent_id = message.from_user.id
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    reports_storage[agent_id] = {
        "date": today_str,
        "text": message.text,
        "status": "submitted"
    }
    
    await message.answer("✅ Ваш звіт успішно збережено та передано керівнику.")
    await state.clear()

@dp.callback_query(F.data == "view_reports")
async def admin_view_reports(callback: types.CallbackQuery):
    if callback.from_user.id != MY_ADMIN_ID:
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    report_text = f"📊 **Звіти агентів за сьогодні ({today_str}):**\n\n"

    for agent_id in ACTIVE_AGENTS:
        rep = reports_storage.get(agent_id)
        if rep and rep["date"] == today_str:
            report_text = report_text + f"👤 Агент `ID {agent_id}`:\n💬 {rep['text']}\n\n"
        else:
            report_text = report_text + f"👤 Агент `ID {agent_id}`:\n❌ **Пропуск / Звіт не здано**\n\n"

    await callback.message.answer(report_text, parse_mode="Markdown")
    await callback.answer()

async def main():
    asyncio.create_task(start_web_server())
    asyncio.create_task(schedule_daily_reports())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
