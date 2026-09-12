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
TOKEN = "8992791495:AAFRweBImbJDNYahSlwX3LOClEbSEl8yfDI"
PUBLIC_CHANNEL_ID = "-1003889243376"       # Публичный канал
AGENT_WORK_CHAT_ID = -1004428877093       # Рабочий чат
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
lead_details_storage = {} # Хранит полные данные заявки для выдачи агенту

class FormStates(StatesGroup):
    waiting_for_object_data = State()
    waiting_for_client_contact = State()
    waiting_for_agent_report = State()

# --- 1. KEEP-ALIVE WEB SERVER FOR RENDER (ЧТОБЫ БОТ НЕ ЗАСЫПАЛ) ---
async def handle(request):
    return web.Response(text="I am alive and working 24/7!")

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
        "Бот сформує пост, опублікує в канал і надішле базу в робочий чат!"
    )
    await state.set_state(FormStates.waiting_for_object_data)
    await callback.answer()

def parse_dom_ria(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    data = {
        "rooms": "3к",
        "area": "65.0м²",
        "floor": "10/10",
        "bank": "Правий Берег",
        "address": "Дніпро",
        "price": "Ціна за запитом",
        "district_hashtag": "#Дніпро3к",
        "bank_hashtag": "#ПравийБерег",
        "title": "Об'єкт нерухомості Nestima",
        "description": f"Посилання на джерело: {url}",
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
            soup.find('span', class_='full-description')
        )
        if desc_div:
            data["description"] = desc_div.get_text(separator='\n', strip=True)
            
        address_tag = (
            soup.find('span', class_='realty-address') or 
            soup.find('div', class_='address') or
            soup.find('h4', class_='address')
        )
        if address_tag:
            addr_text = address_tag.get_text(strip=True)
            data["address"] = addr_text
            
            lower_addr = addr_text.lower()
            if "топол" in lower_addr:
                data["district_hashtag"] = "#Тополя3к"
            elif "центр" in lower_addr:
                data["district_hashtag"] = "#Центр3к"
            elif "перемог" in lower_addr:
                data["district_hashtag"] = "#Перемога3к"
            elif "правд" in lower_addr or "слобожанск" in lower_addr:
                data["district_hashtag"] = "#Слобожанський3к"
            elif "парус" in lower_addr:
                data["district_hashtag"] = "#Парус3к"
            else:
                data["district_hashtag"] = "#Дніпро3к"

        price_tag = soup.find('span', class_='price') or soup.find('div', class_='price')
        if price_tag:
            p_text = price_tag.get_text(strip=True)
            if "грн" in p_text or "$" in p_text or "€" in p_text:
                data["price"] = f"{p_text} + комунальні послуги"

        phone_tag = soup.find('span', class_='phone') or soup.find('a', class_='phone')
        if phone_tag:
            data["phone"] = phone_tag.get_text(strip=True)
            
        for img in soup.find_all('img', src=True):
            src = img['src']
            if any(x in src for x in ['photos', 'rio', 'dom.ria', 's.dom.ria']):
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

    await message.answer("⏳ Парсю дані, створюю сторінку Telegraph та формую пост...")

    words = user_input.split()
    url = next((w for w in words if w.startswith("http")), user_input.strip())
    parsed_info = parse_dom_ria(url)

    try:
        obj_id = int(datetime.now().timestamp()) % 100000

        telegraph_html = f"<h3>{parsed_info['title']}</h3>"
        for p in parsed_info["photos"]:
            telegraph_html += f'<img src="{p}"/>'
        telegraph_html += f"<p>{parsed_info['description'].replace(chr(10), '<br>')}</p><p><b>Адреса:</b> {parsed_info['address']}</p>"
        
        response = telegraph.create_page(title=f"Об'єкт №{obj_id}", html_content=telegraph_html)
        page_path = response.get('path') if isinstance(response, dict) else response
        telegraph_url = f"https://telegra.ph/{page_path}"

        channel_text = (
            f"{parsed_info['rooms']}\n"
            f"✏️{parsed_info['area']}\n"
            f"🔺Поверх: {parsed_info['floor']} ⚠️\n"
            f"🚣{parsed_info['bank']}\n"
            f"📍{parsed_info['address']}\n"
            f"💵{parsed_info['price']}\n"
            f"📄 **Деталі та фото:** {telegraph_url}\n\n"
            f"{parsed_info['district_hashtag']}\n"
            f"{parsed_info['bank_hashtag']}\n"
            f"#13000x16000 #Nestima"
        )

        builder = InlineKeyboardBuilder()
        # Ссылка на карту формируется строго по конкретному адресу из объявления
        maps_query = parsed_info['address'] if parsed_info['address'] else "Дніпро"
        maps_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(maps_query)}"
        
        builder.row(
            types.InlineKeyboardButton(text="📍 На мапі", url=maps_url),
            types.InlineKeyboardButton(text="📝 Записатися на перегляд", callback_data=f"book_view_{obj_id}")
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
            f"📞 **Телефон власника/агента:** `{parsed_info['phone']}`\n"
            f"📄 **Telegraph:** {telegraph_url}\n\n"
            f"📄 **Опис з сайту:**\n{parsed_info['description']}\n\n"
            f"🔗 **Посилання:** {url}"
        )
        
        await bot.send_message(chat_id=AGENT_WORK_CHAT_ID, text=work_chat_text, parse_mode="Markdown")

        if parsed_info["photos"]:
            work_media_group = [types.InputMediaPhoto(media=p) for p in parsed_info["photos"][:10]]
            await bot.send_media_group(chat_id=AGENT_WORK_CHAT_ID, media=work_media_group)

        await message.answer(f"✅ Успішно! Об'єкт №{obj_id} опубліковано.")
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")
    
    await state.clear()


# --- 4. ЗАКРЫТАЯ ЗАЯВКА ДЛЯ АГЕНТОВ ---
@dp.callback_query(F.data.startswith("book_view_"))
async def direct_client_booking(callback: types.CallbackQuery):
    obj_id = callback.data.split("_")[-1]
    user = callback.from_user
    
    if user.username:
        client_contact = f"@{user.username}"
    else:
        client_contact = f"[{user.full_name}](tg://user?id={user.id})"

    # Сохраняем скрытые данные заявки в памяти словаря
    lead_id = int(datetime.now().timestamp())
    lead_details_storage[lead_id] = {
        "obj_id": obj_id,
        "client_name": user.full_name,
        "client_contact": client_contact
    }

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🟢 Прийняти заявку", callback_data=f"claim_lead_{lead_id}"))

    # Отправляем в чат агентов ТОЛЬКО чистый текст-уведомление без контактов
    await bot.send_message(
        chat_id=AGENT_WORK_CHAT_ID,
        text="😱😱 ЗАЯВКА НА ПЕРЕГЛЯД 😱😱",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    
    await callback.answer(
        "✅ Заявку надіслано! Менеджер зв'яжеться з вами найближчим часом.", 
        show_alert=True
    )

@dp.callback_query(F.data.startswith("claim_lead_"))
async def claim_lead_action(callback: types.CallbackQuery):
    lead_id = int(callback.data.split("_")[-1])
    agent_name = callback.from_user.full_name

    # Проверка, не забрал ли кто-то другой
    if lead_id not in lead_details_storage:
        await callback.answer("⚠️ Цю заявку вже хтось прийняв або вона недоступна!", show_alert=True)
        return

    lead_data = lead_details_storage.pop(lead_id) # Удаляем из общего доступа, чтобы другие не видели

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=f"🔒 Заброньовано агентом: {agent_name}", callback_data="claimed"))

    # Редактируем сообщение в чате: теперь полные данные видит ТОЛЬКО тот, кто нажал первый
    await callback.message.edit_text(
        text=(
            f"😱😱 ЗАЯВКА НА ПЕРЕГЛЯД 😱😱\n\n"
            f"📌 **Об'єкт №:** {lead_data['obj_id']}\n"
            f"👤 **Клієнт:** {lead_data['client_name']}\n"
            f"📞 **Контакт клієнта:** {lead_data['client_contact']}\n\n"
            f"✅ **Взято в роботу агентом:** {agent_name}"
        ),
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer("Ви успішно прийняли заявку! Контакти клієнта розблоковано для вас.", show_alert=True)


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
