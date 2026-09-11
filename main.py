import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import asyncio

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("Бот успешно запущен и работает! 🚀")

async def main():
    if not TOKEN:
        print("Ошибка: Токен бота не задан!")
        return
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
