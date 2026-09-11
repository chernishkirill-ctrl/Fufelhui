import os
import logging
import asyncio
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from telegraph import Telegraph

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Новый токен бота
TOKEN = "8824858569:AAG4sMNxzUz3_VTBAIPI4XAhuwk76dUBrE8"
PUBLIC_CHANNEL_ID = "-1004428877093"
AGENT_WORK_CHAT_ID = -1003889243376
MY_ADMIN_ID = 8799145351

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Инициализация Telegraph
telegraph = Telegraph()
telegraph.create_account(short_name='RealEstateBot')

# Хранилища в памяти
ACTIVE_AGENTS = [11111111, 22222222] 
reports_storage = {} 
lead_claims = {} 

# Состояния FSM
class FormStates(StatesGroup):
    waiting_for_object_data = State()
    waiting_for_client_name = State()
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


# --- 2. ЛИЧНЫЙ ПУЛЬТ УПРАВЛЕНИЯ (ТОЛЬКО ДЛЯ ТЕБЯ) ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != MY_ADMIN_ID:
        await message.answer("Цей бот є закритим пунктом управління агентством нерухомості.")
        return

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Додати об'єкт за посиланням", callback_data="add_object"))
    builder.row(types.InlineKeyboardButton(text="📊 Переглянути звіти за сьогодні", callback_data="view_reports"))
    
    await message.answer(
        "Вітаю, босі! Це твій особистий пульт управління.\nОбери необхідну дію:",
        reply_markup=builder.as_markup()
    )


# --- 3. ПУБЛІКАЦІЯ ОБ'ЄКТА ЧЕРЕЗ TELEGRAPH С ФОТО ---
@dp.callback_query(F.data == "add_object")
async def process_add_object(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != MY_ADMIN_ID:
        return
    await callback.message.answer("Будь ласка, надішли **фотографію** об'єкта разом із посиланням в описі (або просто текст із посиланням):")
    await state.set_state(FormStates.waiting_for_object_data)
    await callback.answer()

@dp.message(FormStates.waiting_for_object_data)
async def handle_object_data(message: types.Message, state: FSMContext):
    if message.from_user.id != MY_ADMIN_ID:
        return
    
    text_content = message.caption if message.caption else message.text
    photo_id = message.photo[-1].file_id if message.photo else None

    if not text_content:
        await message.answer("❌ Потрібно надіслати посилання або текст із описом!")
        return

    await message.answer("⏳ Обробляю дані та створюю сторінку на Telegraph...")

    try:
        response = telegraph.create_page(
            title="Об'єкт нерухомості",
            html_content=f"<p><b>Опис / Деталі:</b> {text_content}</p>"
        )
        
        page_path = response.get('path') if isinstance(response, dict) else response
        telegraph_url = f"https://telegra.ph/{page_path}"

        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="📄 Дивитися повний огляд об'єкта", url=telegraph_url))
        builder.row(types.InlineKeyboardButton(text="📝 Записатися на перегляд", callback_data="client_request_view"))

        channel_text = "🏠 **Новий об'єкт нерухомості у базі!**\n\nАктуальна пропозиція за вигідною ціною. Переходьте за посиланням нижче для ознайомлення з деталями."

        if photo_id:
            await bot.send_photo(
                chat_id=PUBLIC_CHANNEL_ID,
                photo=photo_id,
                caption=channel_text,
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(
                chat_id=PUBLIC_CHANNEL_ID,
                text=channel_text,
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )

        await message.answer(f"✅ Об'єкт успішно опубліковано у каналі!\nПосилання на Telegraph: {telegraph_url}")
    except Exception as e:
        await message.answer(f"❌ Помилка при створенні публікації: {e}")
    
    await state.clear()


# --- 4. КЛІЄНТСЬКА ЗАЯВКА З КАНАЛУ В РОБОЧИЙ ЧАТ (БЕЗ КНОПОК ПЕРЕГЛЯДУ У ЧАТІ АГЕНТІВ) ---
@dp.callback_query(F.data == "client_request_view")
async def client_request_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer(
        "Для запису на перегляд введіть ваші дані нижче у чаті (це безпечно).", 
        show_alert=True
    )
    await callback.message.answer("Будь ласка, введіть ваше **Ім'я** та **Номер телефону** у форматі: `Іван, +380XXXXXXXXX`")
    await state.set_state(FormStates.waiting_for_client_name)

@dp.message(FormStates.waiting_for_client_name)
async def process_client_lead(message: types.Message, state: FSMContext):
    lead_info = message.text
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🟢 Прийняти заявку", callback_data="claim_lead"))

    sent_msg = await bot.send_message(
        chat_id=AGENT_WORK_CHAT_ID,
        text=f"🔔 **Нова заявка від клієнта!**\n\nКонтактні дані: {lead_info}\nСтатус: Очікує агента.",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    
    lead_claims[sent_msg.message_id] = None
    await message.answer("Дякуємо! Вашу заявку прийнято в обробку, наш менеджер зв'яжеться з вами найближчим часом.")
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
    await callback.answer("Ви успішно прийняли заявку!")


# --- 5. СИСТЕМА КОНФИДЕНЦИАЛЬНЫХ ОТЧЕТОВ (22:00) ---
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
                text=f"⏰ Час вечірнього звіту для агента (ID: {agent_id}). Натисніть кнопку нижче, щоб надіслати підсумки дня (конфіденційно).",
                reply_markup=builder.as_markup()
            )

@dp.callback_query(F.data.startswith("submit_report_"))
async def agent_click_report(callback: types.CallbackQuery, state: FSMContext):
    data_parts = callback.data.split("_")
    agent_id = int(data_parts[2])
    
    if callback.from_user.id != agent_id:
        await callback.answer("⚠️ Це чужа персональна кнопка звіту!", show_alert=True)
        return

    await callback.message.answer("Будь ласка, напишіть короткий текст вашого звіту за сьогодні (скільки дзвінків, покази, результати):")
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
    
    await message.answer("✅ Ваш звіт успішно збережено та надіслано керівнику.")
    await state.clear()

@dp.callback_query(F.data == "view_reports")
async def admin_view_reports(callback: types.CallbackQuery):
    if callback.from_user.id != MY_ADMIN_ID:
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    report_text = f"📊 **Звіти за сьогодні ({today_str}):**\n\n"

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
