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

# Конфигурация по твоим точным требованиям
TOKEN = "8863316976:AAEkwc6WL6ntAwL8slhskQD4tbXLT_7sjSE"
PUBLIC_CHANNEL_ID = "-1003889243376"       # Публичный канал (альбом + Telegraph + 2 кнопки)
AGENT_WORK_CHAT_ID = -1004428877093       # Рабочая база (полная инфа, заявки, отчеты в 22:00)
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


# --- 3. ПАРСИНГ DOM.RIA И ПУБЛИКАЦИЯ ---
@dp.callback_query(F.data == "add_object")
async def process_add_object(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MY_ADMIN_ID:
        return
    await callback.message.answer(
        "Надішли мені **посилання на оголошення (наприклад, з DOM.RIA)**.\n"
        "Бот сам витягне опис, фотографії, адресу та надішле всю зводку в робочу базу, а в канал опублікує готовий альбом із кнопками!"
    )
    await state.set_state(FormStates.waiting_for_object_data)
    await callback.answer()

def parse_dom_ria(url: str):
    """Функция автоматического парсинга данных с DOM.RIA или других сайтов"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    data = {"title": "Об'єкт нерухомості Nestima", "description": f"Посилання на джерело: {url}", "photos": [], "address": ""}
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return data
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Попытка вытащить заголовок
        h1 = soup.find('h1')
        if h1:
            data["title"] = h1.get_text(strip=True)
            
        # Попытка вытащить описание
        desc_div = soup.find('div', class_='description') or soup.find('div', id='description-area')
        if desc_div:
            data["description"] = desc_div.get_text(separator='<br>', strip=True)
            
        # Попытка вытащить адрес
        address_tag = soup.find('span', class_='realty-address') or soup.find('div', class_='address')
        if address_tag:
            data["address"] = address_tag.get_text(strip=True)
            
        # Попытка вытащить фотографии
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

    await message.answer("⏳ Парсю дані з посилання, готую сторінку Telegraph та формую публікацію...")

    # Проверяем, есть ли в сообщении ссылка
    parsed_info = {"title": "Об'єкт нерухомості", "description": user_input, "photos": [], "address": user_input}
    if "http" in user_input:
        # Извлекаем саму ссылку
        words = user_input.split()
        url = next((w for w in words if w.startswith("http")), None)
        if url and "dom.ria" in url:
            parsed_info = parse_dom_ria(url)

    try:
        # Создаем страницу в Telegraph с полным описанием
        telegraph_html = f"<h3>{parsed_info['title']}</h3><p>{parsed_info['description']}</p><p><b>Адреса:</b> {parsed_info['address']}</p>"
        response = telegraph.create_page(title=parsed_info['title'][:50], html_content=telegraph_html)
        page_path = response.get('path') if isinstance(response, dict) else response
        telegraph_url = f"https://telegra.ph/{page_path}"

        # Кнопки для публичного канала (Ровно две штуки!)
        builder = InlineKeyboardBuilder()
        maps_query = parsed_info['address'] if parsed_info['address'] else parsed_info['title']
        maps_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(maps_query[:100])}"
        
        builder.row(types.InlineKeyboardButton(text="📍 На мапі", url=maps_url))
        builder.row(types.InlineKeyboardButton(text="📝 Записатися на перегляд", callback_data="client_request_view"))

        channel_text = f"🏠 **{parsed_info['title']}**\n\n📄 **Повний огляд (Telegraph):** {telegraph_url}"

        # Публикация в ПУБЛИЧНЫЙ КАНАЛ (-1003889243376)
        if parsed_info["photos"] and len(parsed_info["photos"]) > 1:
            # Если спарсилось несколько фото — шлем альбомом
            media_group = [types.InputMediaPhoto(media=p, caption=channel_text if i == 0 else "", parse_mode="Markdown") for i, p in enumerate(parsed_info["photos"][:10])]
            sent_msg = await bot.send_media_group(chat_id=PUBLIC_CHANNEL_ID, media=media_group)
            # Отдельным сообщением с кнопками под альбом
            await bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text="👇 Керуйте замовленням за допомогою кнопок нижче:", reply_markup=builder.as_markup())
        elif parsed_info["photos"]:
            await bot.send_photo(chat_id=PUBLIC_CHANNEL_ID, photo=parsed_info["photos"][0], caption=channel_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=PUBLIC_CHANNEL_ID, text=channel_text, reply_markup=builder.as_markup(), parse_mode="Markdown", disable_web_page_preview=False)

        # Отправка полной информации в РАБОЧУЮ БАЗУ (-1004428877093)
        work_chat_text = (
            f"📥 **Новий об'єкт додано до бази!**\n\n"
            f"📌 **Назва:** {parsed_info['title']}\n"
            f"📍 **Адреса:** {parsed_info['address']}\n"
            f"🔗 **Посилання на джерело:** {user_input}\n"
            f"📄 **Telegraph:** {telegraph_url}\n"
            f"👤 **Додано адміністратором.**"
        )
        await bot.send_message(chat_id=AGENT_WORK_CHAT_ID, text=work_chat_text, parse_mode="Markdown")

        await message.answer(f"✅ Успішно! Об'єкт опубліковано в канал, а повна зводка вирушила у робочу базу.\n🔗 Telegraph: {telegraph_url}")
    except Exception as e:
        await message.answer(f"❌ Помилка при обробці об'єкта: {e}")
    
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

    # Заявка летит в рабочую базу (-1004428877093)
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


# --- 5. ВЕЧЕРНИЕ ОТЧЕТЫ В 22:00 В РАБОЧУЮ БАЗУ ---
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
                text=f"⏰ Час вечірнього звіту для агента (ID: {agent_id}). Натисніть кнопку нижче, щоб надіслати результати дня (конфіденційно).",
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


# --- ЗАПУСК БОТА И СЕРВЕРА ---
async def main():
    asyncio.create_task(start_web_server())
    asyncio.create_task(schedule_daily_reports())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
