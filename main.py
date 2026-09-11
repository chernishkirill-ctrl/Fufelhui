import os
import logging
import asyncio
import urllib.parse
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from telegraph import Telegraph

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Конфигурация
TOKEN = "8863316976:AAEkwc6WL6ntAwL8slhskQD4tbXLT_7sjSE"
PUBLIC_CHANNEL_ID = "-1003889243376"       # Публичный канал (Telegraph + текст + хештег + 2 кнопки)
AGENT_WORK_CHAT_ID = -1004428877093       # Рабочая база (полные фотки, телефон, вся инфа)
MY_ADMIN_ID = 8799145351

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Инициализация Telegraph
telegraph = Telegraph()
telegraph.create_account(short_name='NestimaRealEstate')

# Хранилища в памяти
ACTIVE_AGENTS = [11111111, 22222222] 
reports_storage = {} 
lead_claims = {} 

class FormStates(StatesGroup):
    waiting_for_object_data = State()
    waiting_for_client_contact = State()
    waiting_for_agent_report = State()

# --- 1. KEEP-ALIVE WEB SERVER FOR RENDER ---
async def handle(request):
    return web.Response(text="I am alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")


# --- 2. ПУЛЬТ УПРАВЛЕНИЯ ---
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


# --- 3. ПАРСИНГ И ПУБЛИКАЦИЯ ---
@dp.callback_query(F.data == "add_object")
async def process_add_object(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MY_ADMIN_ID:
        return
    await callback.message.answer(
        "Надішли мені **посилання на оголошення (наприклад, з DOM.RIA)**.\n"
        "Бот створить красиву сторінку Telegraph, опублікує її в канал із хештегом і кнопками, а повну базу надішле в робочий чат!"
    )
    await state.set_state(FormStates.waiting_for_object_data)
    await callback.answer()

def parse_dom_ria(url: str):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    data = {
        "title": "Об'єкт нерухомості Nestima", 
        "description": f"Посилання на джерело: {url}", 
        "short_description": "Продаж/Оренда нерухомості від Nestima.",
        "photos": [], 
        "address": "Дніпро",
        "district_hashtag": "#Дніпро",
        "phone": "Не вказано"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return data
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Заголовок
        h1 = soup.find('h1')
        if h1:
            data["title"] = h1.get_text(strip=True)
            data["short_description"] = h1.get_text(strip=True)
            
        # Описание
        desc_div = soup.find('div', class_='description') or soup.find('div', id='description-area')
        if desc_div:
            data["description"] = desc_div.get_text(separator='\n', strip=True)
            
        # Адрес и район для хештега
        address_tag = soup.find('span', class_='realty-address') or soup.find('div', class_='address')
        if address_tag:
            addr_text = address_tag.get_text(strip=True)
            data["address"] = addr_text
            
            lower_addr = addr_text.lower()
            if "топол" in lower_addr:
                data["district_hashtag"] = "#Тополя"
            elif "центр" in lower_addr:
                data["district_hashtag"] = "#Центр"
            elif "перемог" in lower_addr:
                data["district_hashtag"] = "#Перемога"
            elif "правд" in lower_addr or "слобожанск" in lower_addr:
                data["district_hashtag"] = "#Слобожанський"
            elif "парус" in lower_addr:
                data["district_hashtag"] = "#Парус"
            elif "островськ" in lower_addr or "вокзал" in lower_addr:
                data["district_hashtag"] = "#Вокзал"
            else:
                data["district_hashtag"] = "#Дніпро"
            
        # Телефон
        phone_tag = soup.find('span', class_='phone') or soup.find('a', class_='phone')
        if phone_tag:
            data["phone"] = phone_tag.get_text(strip=True)
            
        # Фотографии
        for img in soup.find_all('img', src=True):
            src = img['src']
            if 'photos' in src or 'rio' in src or 'dom.ria' in src:
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
    if not user_input:
        await message.answer("❌ Надішли посилання на оголошення!")
        return

    await message.answer("⏳ Створюю сторінку Telegraph, готую пост для каналу та зводку для робочої бази...")

    parsed_info = {
        "title": "Об'єкт нерухомості", 
        "description": user_input, 
        "short_description": "Новий об'єкт нерухомості у базі Nestima.",
        "photos": [], 
        "address": user_input,
        "district_hashtag": "#Дніпро",
        "phone": "Не вказано"
    }
    
    if "http" in user_input:
        words = user_input.split()
        url = next((w for w in words if w.startswith("http")), None)
        if url and "dom.ria" in url:
            parsed_info = parse_dom_ria(url)

    try:
        # Уникальный порядковый номер объекта
        obj_id = int(datetime.now().timestamp()) % 100000

        # 1. Создаем страницу в Telegraph со всеми фото и описанием
        telegraph_html = f"<h3>{parsed_info['title']}</h3>"
        for p in parsed_info["photos"]:
            telegraph_html += f'<img src="{p}"/>'
        telegraph_html += f"<p>{parsed_info['description'].replace(chr(10), '<br>')}</p><p><b>Адреса:</b> {parsed_info['address']}</p>"
        
        response = telegraph.create_page(title=f"Об'єкт №{obj_id}", html_content=telegraph_html)
        page_path = response.get('path') if isinstance(response, dict) else response
        telegraph_url = f"https://telegra.ph/{page_path}"

        # 2. Формируем текст для публикации в публичный канал
        channel_text = (
            f"🏢 **Об'єкт №{obj_id}**\n\n"
            f"{parsed_info['short_description']}\n\n"
            f"📍 **Адреса:** {parsed_info['address']}\n"
            f"📄 **Детальний огляд (Telegraph):** {telegraph_url}\n\n"
            f"🏷 {parsed_info['district_hashtag']} #Nestima"
        )

        # 3. Кнопки для публичного канала
        builder = InlineKeyboardBuilder()
        maps_query = parsed_info['address'] if parsed_info['address'] else parsed_info['title']
        maps_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(maps_query[:100])}"
        
        builder.row(
            types.InlineKeyboardButton(text="📍 На мапі", url=maps_url),
            types.InlineKeyboardButton(text="📝 Записатися на перегляд", callback_data="client_request_view")
        )

        # Отправляем в публичный канал
        await bot.send_message(
            chat_id=PUBLIC_CHANNEL_ID, 
            text=channel_text, 
            reply_markup=builder.as_markup(), 
            parse_mode="Markdown",
            disable_web_page_preview=False
        )

        # 4. Отправка в РАБОЧУЮ БАЗУ (Вся инфа, телефон отдельно, все фото отдельно)
        work_chat_text = (
            f"📥 **Новий об'єкт №{obj_id} у робочій базі!**\n\n"
            f"📌 **Назва:** {parsed_info['title']}\n"
            f"📍 **Адреса:** {parsed_info['address']}\n"
            f"📞 **Номер телефону власника/агента:** `{parsed_info['phone']}`\n"
            f"📄 **Telegraph:** {telegraph_url}\n\n"
            f"📄 **Повний опис:**\n{parsed_info['description']}\n\n"
            f"🔗 **Посилання на джерело:** {user_input}"
        )
        
        await bot.send_message(chat_id=AGENT_WORK_CHAT_ID, text=work_chat_text, parse_mode="Markdown")

        if parsed_info["photos"]:
            work_media_group = [types.InputMediaPhoto(media=p) for p in parsed_info["photos"][:10]]
            await bot.send_media_group(chat_id=AGENT_WORK_CHAT_ID, media=work_media_group)

        await message.answer(f"✅ Успішно! Об'єкт №{obj_id} опубліковано в канал через Telegraph (з описом, хештегом і кнопками), а деталі та телефон відправлені в робочу базу.")
    except Exception as e:
        await message.answer(f"❌ Помилка при публікації: {e}")
    
    await state.clear()


# --- 4. ЗАПИСЬ КЛИЕНТА НА ПРОСМОТР ---
@dp.callback_query(F.data == "client_request_view")
async def client_request_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Запит на перегляд об'єкта", show_alert=False)
    
    kb_builder = ReplyKeyboardBuilder()
    kb_builder.row(types.KeyboardButton(text="📱 Поділитися контактом", request_contact=True))
    
    msg = await callback.message.answer(
        "Дбаємо про ваш час! Натисніть кнопку нижче **«📱 Поділитися контактом»**, щоб надіслати свій номер телефону менеджерові в один клік.",
        reply_markup=kb_builder.as_markup(resize_keyboard=True, one_time_keyboard=True)
    )
    
    await state.update_data(prompt_msg_id=msg.message_id)
    await state.set_state(FormStates.waiting_for_client_contact)

@dp.message(FormStates.waiting_for_client_contact, F.contact | F.text)
async def process_client_lead(message: types.Message, state: FSMContext):
    if message.contact:
        client_phone = message.contact.phone_number
        client_name = f"{message.contact.first_name or ''} {message.contact.last_name or ''}".strip()
        lead_info = f"Ім'я: {client_name}, Телефон: +{client_phone}"
    else:
        lead_info = f"Контактні дані / текст: {message.text}"

    try:
        data = await state.get_data()
        if "prompt_msg_id" in data:
            await bot.delete_message(chat_id=message.chat.id, message_id=data["prompt_msg_id"])
    except:
        pass

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🟢 Прийняти заявку", callback_data="claim_lead"))

    sent_msg = await bot.send_message(
        chat_id=AGENT_WORK_CHAT_ID,
        text=f"🔔 **Нова заявка на перегляд від клієнта!**\n\n👤 Дані клієнта: {lead_info}\n📌 Статус: Очікує агента.",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    
    lead_claims[sent_msg.message_id] = None
    
    remove_kb = types.ReplyKeyboardRemove()
    await message.answer("✅ Дякуємо! Вашу заявку успішно надіслано. Наш агент зв'яжеться з вами найближчим часом.", reply_markup=remove_kb)
    await state.clear()

@dp.callback_query(F.data == "claim_lead")
async def claim_lead_action(callback: types.CallbackQuery):
    msg_id = callback.message.message_id
    agent_id = callback.from_user.id
    agent_name = callback.from_user.full_name

    if lead_claims.get(msg_id) is not None:
        await callback.answer("⚠️ Цю заявку вже забронював інший агент!", show_alert=True)
        return

    lead_claims[msg_id] = agent_id
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=f"🔒 Заброньовано агентом: {agent_name}", callback_data="claimed"))

    await callback.message.edit_text(
        text=callback.message.text + f"\n\n✅ **Взято в роботу агентом:** {agent_name}",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer("Ви успішно прийняли заявку в роботу!")


# --- 5. ВЕЧЕРНИЕ ОТЧЕТЫ В 22:00 ---
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
async def agent_click_report(callback: types.CallbackQuery, state: FSMContext):
    data_parts = callback.data.split("_")
    agent_id = int(data_parts[2])
    
    if callback.from_user.id != agent_id:
        await callback.answer("⚠️ Це чужа персональна кнопка звіту!", show_alert=True)
        return

    await callback.message.answer("Будь ласка, напишіть короткий текст вашого звіту за сьогодні (дзвінки, покази, угоди):")
    await state.set_state(FormStates.waiting_for_agent_report)
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


# --- ЗАПУСК ---
async def main():
    asyncio.create_task(start_web_server())
    asyncio.create_task(schedule_daily_reports())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
