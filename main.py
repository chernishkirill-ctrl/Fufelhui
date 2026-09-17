import os
import asyncio
import logging
import re
from datetime import datetime
from aiohttp import ClientSession, web

from aiogram import Bot, Dispatcher, F, html, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MY_ADMIN_ID = 8700604325

PUBLIC_CHANNEL_ID = "-1003889243376"
INTERNAL_BASE_ID = "-1004357065341"

GROUP_CHAT_ID = -1004428877093
CHAT_TOPIC_ID = 3
DEALS_TOPIC_ID = 5

WEBAPP_FORM_URL = "https://t.me/gggggsre"

logging.basicConfig(level=logging.INFO)

if not BOT_TOKEN:
    logging.error("ОШИБКА: BOT_TOKEN не найден в переменных окружения!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

PENDING_POSTS = {}

# ==========================================
# 2. МИНИ-ВЕБ-СЕРВЕР (ДЛЯ RENDER 24/7)
# ==========================================
async def handle_ping(request):
    return web.Response(text="Nestima Bot is active and running 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Dummy Web-server successfully started on port {port}")

# ==========================================
# 3. СОСТОЯНИЯ (FSM)
# ==========================================
class FormStates(StatesGroup):
    waiting_for_input = State()
    waiting_for_deal_realtor = State()
    waiting_for_deal_address = State()
    waiting_for_deal_price = State()
    waiting_for_deal_commission = State()
    waiting_for_deal_telegraph = State()
    waiting_for_expense_desc = State()
    waiting_for_expense_amount = State()

# ==========================================
# 4. ОБРАБОТКА И TELEGRAPH
# ==========================================
def clean_sensitive_info(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\+?\d[\d\s-]{8,}\d', '[КОНТАКТИ ПРИХОВАНО]', text)
    text = re.sub(r'http[s]?://\S+', '', text)
    text = re.sub(r'@[A-Za-z0-9_]+', '', text)
    return text.strip()

async def create_telegraph_page(title: str, text: str) -> str:
    try:
        async with ClientSession() as session:
            acc_response = await session.post("https://api.telegra.ph/createAccount", json={
                "short_name": "Nestima",
                "author_name": "Nestima Real Estate"
            })
            acc_data = await acc_response.json()
            access_token = acc_data.get("result", {}).get("access_token")

            if not access_token:
                return f"https://telegra.ph/Obyekt-{datetime.now().strftime('%d-%m-%Y-%H%M')}"

            content = [{"tag": "p", "children": [p]} for p in text.split("\n") if p.strip()]
            
            page_response = await session.post("https://api.telegra.ph/createPage", json={
                "access_token": access_token,
                "title": title[:256],
                "author_name": "Nestima Real Estate",
                "content": content,
                "return_content": False
            })
            page_data = await page_response.json()
            if page_data.get("ok"):
                return f"https://telegra.ph/{page_data['result']['path']}"
    except Exception as e:
        logging.error(f"Telegraph API error: {e}")
    
    return f"https://telegra.ph/Obyekt-{datetime.now().strftime('%d-%m-%Y-%H%M')}"

# ==========================================
# 5. ПУЛЬТ РУКОВОДИТЕЛЯ
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != MY_ADMIN_ID:
        await message.answer("👋 Вітаю! Бот працює в штатному режимі.")
        return

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="➕ Опублікувати об'єкт / пост", callback_data="add_property"))
    builder.row(types.InlineKeyboardButton(text="🎉 Зафіксувати угоду", callback_data="add_deal"))
    builder.row(types.InlineKeyboardButton(text="📊 Бухгалтерія", callback_data="view_accounting"))
    builder.row(types.InlineKeyboardButton(text="📋 Звіти за сьогодні", callback_data="view_daily_reports"))

    await message.answer("🛠 <b>Панель управління Nestima Real Estate:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

# ==========================================
# 6. ПУБЛИКАЦИЯ ОБЪЕКТА (БАЗА + КАНАЛ)
# ==========================================
@dp.callback_query(F.data == "add_property")
async def start_add_property(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🔗 <b>Надішліть повний текст оголошення (з усіма деталями, ціною та контактами):</b>", parse_mode="HTML")
    await state.set_state(FormStates.waiting_for_input)
    await callback.answer()

@dp.message(FormStates.waiting_for_input)
async def process_property_input(message: types.Message, state: FSMContext):
    input_text = message.text or message.caption or "Об'єкт без тексту"
    clean_text = clean_sensitive_info(input_text)
    
    phone_match = re.search(r'\+?\d[\d\s-]{8,}\d', input_text)
    phone = phone_match.group(0) if phone_match else "Не вказано"

    obj_id = f"ID-{datetime.now().strftime('%M%S')}"
    telegraph_url = await create_telegraph_page(f"Об'єкт нерухомості ({obj_id})", clean_text)
    
    PENDING_POSTS[obj_id] = {
        "raw_text": input_text,
        "clean_text": clean_text,
        "phone": phone,
        "telegraph_url": telegraph_url,
        "object_id": obj_id
    }

    internal_text = (
        f"📥 <b>НОВИЙ ОБ'ЄКТ У БАЗІ</b> (ID: {obj_id})\n\n"
        f"👤 <b>Контакт власника:</b> {html.quote(phone)}\n"
        f"📄 <b>Telegraph:</b> <a href='{telegraph_url}'>Посилання на повний огляд</a>\n\n"
        f"📝 <b>Повний текст оголошення:</b>\n{html.quote(input_text)}\n\n"
        f"#Nestima #База"
    )

    try:
        await bot.send_message(
            chat_id=INTERNAL_BASE_ID, 
            text=internal_text, 
            parse_mode="HTML",
            link_preview_options={"is_disabled": True}
        )
    except Exception as e:
        logging.error(f"Помилка відправки у внутрішню базу ({INTERNAL_BASE_ID}): {e}")
        await message.answer(f"⚠️ Помилка відправки у базу ріелторів! Перевір правильність `INTERNAL_BASE_ID` у коді.")

    preview_text = (
        f"✅ <b>Об'єкт {obj_id} успішно оброблено!</b>\n\n"
        f"Уся інформація збережена та передана у базу. Натисніть кнопку нижче для публікації в канал:"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📢 Опублікувати в Публічний канал", callback_data=f"publish_public_{obj_id}"))

    await message.answer(preview_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data.startswith("publish_public_"))
async def publish_to_public_channel(callback: types.CallbackQuery):
    obj_id = callback.data.split("publish_public_")[1]
    data = PENDING_POSTS.get(obj_id)

    if not data:
        await callback.answer("⚠️ Дані застаріли через перезапуск бота. Створіть запит заново через /start", show_alert=True)
        return

    public_text = (
        f"🏢 <b>Новий об'єкт нерухомості у Дніпрі</b>\n\n"
        f"{html.quote(data['clean_text'][:800])}...\n\n"
        f"📄 <b>Повні фото та деталі:</b> <a href='{data['telegraph_url']}'>Дивитися огляд</a>\n\n"
        f"#Оренда #Дніпро #Nestima"
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📝 Записатися на перегляд", url=WEBAPP_FORM_URL))

    try:
        await bot.send_message(
            chat_id=PUBLIC_CHANNEL_ID, 
            text=public_text, 
            reply_markup=builder.as_markup(), 
            parse_mode="HTML",
            link_preview_options={"is_disabled": True}
        )
        await callback.message.edit_text(f"🚀 <b>Об'єкт {obj_id} успішно опубліковано в Публічний канал!</b>", parse_mode="HTML")
    except Exception as e:
        logging.error(f"Помилка публікації в канал: {e}")
        await callback.message.answer(f"⚠️ Помилка публікації: {e}")
    
    await callback.answer()

# ==========================================
# 7. ЗВІТИ ТА УГОДИ
# ==========================================
@dp.callback_query(F.data == "view_daily_reports")
async def view_reports_admin(callback: types.CallbackQuery):
    rows = db.get_today_reports()
    today_str = datetime.now().strftime("%Y-%m-%d")

    if not rows:
        await callback.message.answer(f"📊 <b>Звіти за сьогодні ({today_str}):</b>\n\nПоки немає звітів.", parse_mode="HTML")
        await callback.answer()
        return

    report_text = f"📊 <b>Звіти за сьогодні ({today_str}):</b>\n\n"
    for row in rows:
        report_text += f"👤 <b>{html.quote(row[1])} ({row[0]}):</b>\n💬 {html.quote(row[2])}\n\n"

    await callback.message.answer(report_text, parse_mode="HTML")
    await callback.answer()

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
        val = float(re.sub(r'[^\d.]', '', message.text))
    except ValueError:
        val = 0.0
    await state.update_data(price=val)
    await message.answer("💵 Вкажіть комісію агентства ($):")
    await state.set_state(FormStates.waiting_for_deal_commission)

@dp.message(FormStates.waiting_for_deal_commission)
async def process_deal_commission(message: types.Message, state: FSMContext):
    try:
        val = float(re.sub(r'[^\d.]', '', message.text))
    except ValueError:
        val = 0.0
    await state.update_data(commission=val)
    await message.answer("🔗 Вкажіть посилання на Telegraph-огляд:")
    await state.set_state(FormStates.waiting_for_deal_telegraph)

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

    await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        message_thread_id=DEALS_TOPIC_ID,
        text=deal_card_text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

    await message.answer("✅ Угоду успішно опубліковано у темі «Сделки»!")
    await state.clear()

# ==========================================
# 8. БУХГАЛТЕРИЯ
# ==========================================
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
    data = await state.get_data()
    try:
        val = float(re.sub(r'[^\d.]', '', message.text))
    except ValueError:
        val = 0.0
    db.save_expense(data['desc'], val)
    await message.answer("✅ Витрату внесено!")
    await state.clear()

@dp.callback_query(F.data == "generate_fin_report")
async def generate_financial_report(callback: types.CallbackQuery):
    income, expenses = db.get_financial_summary()
    net_profit = income - expenses

    report = (
        f"📊 <b>ЗВЕДЕНИЙ ФІНАНСОВИЙ ЗВІТ</b>\n\n"
        f"📥 <b>Дохід (комісія):</b> ${income:,.2f}\n"
        f"📤 <b>Витрати:</b> ${expenses:,.2f}\n"
        f"-----------------------------------\n"
        f"💰 <b>ЧИСТИЙ ПРИБУТОК:</b> ${net_profit:,.2f}"
    )

    await callback.message.answer(report, parse_mode="HTML")
    await callback.answer()

# ==========================================
# 9. ЗАПУСК БОТА И ВЕБ-СЕРВЕРА (С ЗАЩИТОЙ ОТ КОНФЛИКТОВ)
# ==========================================
async def main():
    db.init_db()
    await start_web_server()

    # ЖЕСТКАЯ ОЧИСТКА: Сбрасываем зависшие старые сессии Telegram перед запуском
    await bot.delete_webhook(drop_pending_updates=True)

    logging.info("Bot started successfully!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
