import os
import re
import html
import logging
import asyncio
import urllib.parse
from datetime import datetime
from threading import Thread

import requests
from bs4 import BeautifulSoup
from flask import Flask

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telegraph import Telegraph


# ============================================================
# НАСТРОЙКИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("nestima")


# ============================================================
# ENV
# ============================================================

# В Render создай:
#
# BOT_TOKEN = твой текущий токен
#
# Остальные значения можно также вынести в ENV,
# но здесь оставлены значения из твоего оригинального кода.

TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "Не найден BOT_TOKEN. Добавь BOT_TOKEN в Environment Variables Render."
    )

PUBLIC_CHANNEL_ID = int(
    os.environ.get("PUBLIC_CHANNEL_ID", "-1003889243376")
)

AGENT_WORK_CHAT_ID = int(
    os.environ.get("AGENT_WORK_CHAT_ID", "-1004428877093")
)

MY_ADMIN_ID = int(
    os.environ.get("MY_ADMIN_ID", "8799145351")
)


# ============================================================
# FLASK ДЛЯ RENDER
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Nestima Bot is running!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Nestima"
    }


def run_web():
    port = int(os.environ.get("PORT", 10000))

    logger.info(f"Starting web server on port {port}")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


def keep_alive():
    thread = Thread(
        target=run_web,
        daemon=True
    )
    thread.start()


# ============================================================
# TELEGRAM
# ============================================================

bot = Bot(token=TOKEN)

storage = MemoryStorage()

dp = Dispatcher(storage=storage)


# ============================================================
# TELEGRAPH
# ============================================================

telegraph = Telegraph()


def init_telegraph():
    """
    Создаём Telegraph account.
    Если Telegraph временно недоступен — приложение
    не падает сразу.
    """

    try:
        result = telegraph.create_account(
            short_name="NestimaRealEstate"
        )

        logger.info(
            "Telegraph account created successfully"
        )

        return result

    except Exception as e:
        logger.error(
            f"Telegraph account error: {e}"
        )

        return None


# ============================================================
# FSM
# ============================================================

class FormStates(StatesGroup):
    waiting_for_object_data = State()


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def is_admin(user_id: int) -> bool:
    return user_id == MY_ADMIN_ID


def extract_url(text: str):
    """
    Достаёт первую нормальную http/https ссылку из сообщения.
    """

    if not text:
        return None

    match = re.search(
        r'https?://[^\s<>"\']+',
        text
    )

    if not match:
        return None

    url = match.group(0).rstrip(".,!?;:)")

    try:
        parsed = urllib.parse.urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return None

        if not parsed.netloc:
            return None

        return url

    except Exception:
        return None


def clean_text(value: str) -> str:
    """
    Чистим лишние пробелы, но сохраняем переносы строк.
    """

    if not value:
        return ""

    value = value.replace("\r\n", "\n")
    value = value.replace("\r", "\n")

    lines = []

    for line in value.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()

        if line:
            lines.append(line)

    return "\n".join(lines).strip()


def make_object_id() -> str:
    """
    Уникальный ID объекта.
    """
    return datetime.now().strftime("%d%H%M%S")


def normalize_photo_url(src: str, base_url: str):
    """
    Превращает относительный URL картинки в абсолютный.
    """

    if not src:
        return None

    src = src.strip()

    if src.startswith("data:"):
        return None

    return urllib.parse.urljoin(
        base_url,
        src
    )


# ============================================================
# ПАРСИНГ
# ============================================================

def parse_listing(url: str):
    """
    Парсит объявление.

    Функция синхронная, но вызывается через asyncio.to_thread(),
    поэтому не блокирует Telegram-бота.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,"
            "image/webp,*/*;q=0.8"
        ),
        "Connection": "keep-alive",
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
        "description": (
            "Сучасна квартира в зручному районі міста."
        ),
        "photos": [],
        "phone": "Не вказано",
    }

    try:

        logger.info(f"Parsing URL: {url}")

        response = requests.get(
            url,
            headers=headers,
            timeout=15,
            allow_redirects=True
        )

        logger.info(
            f"HTTP {response.status_code}: {response.url}"
        )

        if response.status_code != 200:
            logger.warning(
                f"Website returned HTTP {response.status_code}"
            )
            return data

        # Для большинства современных сайтов
        response.encoding = response.apparent_encoding

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # ----------------------------------------------------
        # TITLE
        # ----------------------------------------------------

        h1 = soup.find("h1")

        if h1:
            title = clean_text(
                h1.get_text(" ", strip=True)
            )

            if title:
                data["title"] = title

        # ----------------------------------------------------
        # DESCRIPTION
        # ----------------------------------------------------

        description_selectors = [
            ("div", {"class": "page-description"}),
            ("div", {"class": "description"}),
            ("div", {"class": "realty-description"}),
            ("div", {"class": "property-description"}),
            ("div", {"class": "offer-description"}),
            ("meta", {"property": "og:description"}),
        ]

        description_found = False

        for tag_name, attrs in description_selectors:

            element = soup.find(
                tag_name,
                attrs
            )

            if not element:
                continue

            if tag_name == "meta":
                text = element.get("content", "")
            else:
                text = element.get_text(
                    separator="\n",
                    strip=True
                )

            text = clean_text(text)

            if text:
                data["description"] = text
                description_found = True
                break

        # OpenGraph description
        if not description_found:

            og_description = soup.find(
                "meta",
                property="og:description"
            )

            if og_description:
                text = clean_text(
                    og_description.get("content", "")
                )

                if text:
                    data["description"] = text

        # ----------------------------------------------------
        # ADDRESS
        # ----------------------------------------------------

        address_selectors = [
            ("span", {"class": "realty-address"}),
            ("div", {"class": "address"}),
            ("span", {"class": "address"}),
            ("div", {"class": "property-address"}),
            ("span", {"class": "property-address"}),
        ]

        for tag_name, attrs in address_selectors:

            address_tag = soup.find(
                tag_name,
                attrs
            )

            if not address_tag:
                continue

            addr_text = clean_text(
                address_tag.get_text(
                    " ",
                    strip=True
                )
            )

            if addr_text:
                data["address"] = addr_text
                break

        # ----------------------------------------------------
        # ADDRESS ИЗ META
        # ----------------------------------------------------

        if data["address"] == "Дніпро":

            meta_address = (
                soup.find(
                    "meta",
                    property="og:street-address"
                )
                or soup.find(
                    "meta",
                    attrs={"name": "address"}
                )
            )

            if meta_address:

                value = clean_text(
                    meta_address.get("content", "")
                )

                if value:
                    data["address"] = value

        # ----------------------------------------------------
        # БЕРЕГ
        # ----------------------------------------------------

        lower_addr = data["address"].lower()

        right_bank_words = [
            "правобереж",
            "центр",
            "топол",
            "перемог",
            "парус",
            "нагорівк",
            "побед",
            "шевченківськ",
            "шевченковск",
            "соборн",
            "чечелівськ",
            "чечеловск",
        ]

        if any(
            word in lower_addr
            for word in right_bank_words
        ):

            data["bank"] = "Правий Берег"
            data["bank_hashtag"] = "#ПравийБерег"

        else:

            data["bank"] = "Лівий Берег"
            data["bank_hashtag"] = "#ЛівийБерег"

        # ----------------------------------------------------
        # PRICE
        # ----------------------------------------------------

        price_selectors = [
            ("span", {"class": "price"}),
            ("div", {"class": "price"}),
            ("span", {"class": "property-price"}),
            ("div", {"class": "property-price"}),
            ("span", {"class": "offer-price"}),
        ]

        for tag_name, attrs in price_selectors:

            price_tag = soup.find(
                tag_name,
                attrs
            )

            if not price_tag:
                continue

            price = clean_text(
                price_tag.get_text(
                    " ",
                    strip=True
                )
            )

            if price:
                data["price"] = price
                break

        # ----------------------------------------------------
        # FULL PAGE TEXT
        # ----------------------------------------------------

        full_text_page = clean_text(
            soup.get_text(
                separator="\n",
                strip=True
            )
        )

        # ----------------------------------------------------
        # КОЛИЧЕСТВО КОМНАТ
        # ----------------------------------------------------

        rooms_match = re.search(
            r'(\d{1,2})\s*[-]?\s*'
            r'(?:кімн|комн|кімнати|комнати|кімнат)',
            full_text_page,
            re.IGNORECASE
        )

        if not rooms_match:

            rooms_match = re.search(
                r'(\d{1,2})\s*кімн',
                full_text_page,
                re.IGNORECASE
            )

        if rooms_match:

            rooms_num = rooms_match.group(1)

            data["rooms"] = f"{rooms_num}к"

            data["district_hashtag"] = (
                f"#Дніпро{rooms_num}к"
            )

        # ----------------------------------------------------
        # ПЛОЩАДЬ
        # ----------------------------------------------------

        area_match = re.search(
            r'(\d+(?:[.,]\d+)?)\s*'
            r'(?:м²|м2|кв\.?\s*м|кв\.м)',
            full_text_page,
            re.IGNORECASE
        )

        if area_match:

            area = area_match.group(1)

            data["area"] = f"{area}м²"

        # ----------------------------------------------------
        # ЭТАЖ
        # ----------------------------------------------------

        floor_match = re.search(
            r'(\d{1,2})\s*/\s*(\d{1,2})\s*'
            r'(?:пов|этаж|эт)',
            full_text_page,
            re.IGNORECASE
        )

        if floor_match:

            data["floor"] = (
                f"{floor_match.group(1)}/"
                f"{floor_match.group(2)}"
            )

        # Дополнительный вариант:
        # "5 поверх з 9"

        if not data["floor"]:

            floor_match = re.search(
                r'(\d{1,2})\s+'
                r'(?:поверх|этаж)'
                r'.{0,10}?'
                r'(\d{1,2})',
                full_text_page,
                re.IGNORECASE
            )

            if floor_match:

                data["floor"] = (
                    f"{floor_match.group(1)}/"
                    f"{floor_match.group(2)}"
                )

        # ----------------------------------------------------
        # PHONE
        # ----------------------------------------------------

        phone_selectors = [
            ("span", {"class": "phone"}),
            ("a", {"class": "phone"}),
            ("span", {"class": "phone-number"}),
            ("a", {"class": "phone-number"}),
        ]

        for tag_name, attrs in phone_selectors:

            phone_tag = soup.find(
                tag_name,
                attrs
            )

            if not phone_tag:
                continue

            phone = clean_text(
                phone_tag.get_text(
                    " ",
                    strip=True
                )
            )

            if phone:
                data["phone"] = phone
                break

        # ----------------------------------------------------
        # PHONE ИЗ ТЕКСТА
        # ----------------------------------------------------

        if data["phone"] == "Не вказано":

            phone_match = re.search(
                r'(\+380[\s\-\(\)\d]{9,})',
                full_text_page
            )

            if phone_match:
                data["phone"] = clean_text(
                    phone_match.group(1)
                )

        # ----------------------------------------------------
        # PHOTOS
        # ----------------------------------------------------

        photo_urls = []

        for img in soup.find_all("img"):

            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-original")
                or img.get("data-lazy-src")
            )

            if not src:
                continue

            src = normalize_photo_url(
                src,
                response.url
            )

            if not src:
                continue

            src_lower = src.lower()

            valid_image = (
                any(
                    keyword in src_lower
                    for keyword in [
                        "photos",
                        "photo",
                        "images",
                        "image",
                        "realty",
                        "property",
                        "dom.ria",
                        "ria",
                        "upload",
                        "cdn"
                    ]
                )
                or re.search(
                    r'\.(jpg|jpeg|png|webp)(\?|$)',
                    src_lower
                )
            )

            if valid_image and src not in photo_urls:
                photo_urls.append(src)

        data["photos"] = photo_urls[:20]

        logger.info(
            "Parsing finished: "
            f"title={data['title'][:50]}, "
            f"photos={len(data['photos'])}, "
            f"address={data['address']}"
        )

    except requests.RequestException as e:

        logger.error(
            f"HTTP error while parsing {url}: {e}"
        )

    except Exception as e:

        logger.exception(
            f"Unexpected parsing error: {e}"
        )

    return data


# ============================================================
# TELEGRAPH
# ============================================================

def create_telegraph_page(parsed_info, obj_id):
    """
    Создаёт страницу Telegraph.
    """

    title = html.escape(
        parsed_info["title"]
    )

    address = html.escape(
        parsed_info["address"]
    )

    price = html.escape(
        parsed_info["price"]
    )

    description = html.escape(
        parsed_info["description"]
    ).replace("\n", "<br>")

    telegraph_html = (
        f"<h3>{title}</h3>"
    )

    # Фотографии
    for photo in parsed_info["photos"]:

        safe_photo = html.escape(
            photo,
            quote=True
        )

        telegraph_html += (
            f'<img src="{safe_photo}"/>'
        )

    telegraph_html += (
        f"<p><b>Локація:</b> {address}</p>"
        f"<p><b>Ціна:</b> {price}</p>"
    )

    if parsed_info["floor"]:

        floor = html.escape(
            parsed_info["floor"]
        )

        telegraph_html += (
            f"<p><b>Поверх:</b> {floor}</p>"
        )

    if parsed_info["rooms"]:

        rooms = html.escape(
            parsed_info["rooms"]
        )

        telegraph_html += (
            f"<p><b>Кімнати:</b> {rooms}</p>"
        )

    if parsed_info["area"]:

        area = html.escape(
            parsed_info["area"]
        )

        telegraph_html += (
            f"<p><b>Площа:</b> {area}</p>"
        )

    telegraph_html += (
        "<h4>Детальний опис та характеристики:</h4>"
        f"<p>{description}</p>"
    )

    response = telegraph.create_page(
        title=f"Obj №{obj_id}",
        html_content=telegraph_html
    )

    if isinstance(response, dict):

        page_path = response.get("path")

    else:

        page_path = response

    if not page_path:
        raise RuntimeError(
            "Telegraph не повернув path сторінки."
        )

    return (
        f"https://telegra.ph/{page_path}"
    )


# ============================================================
# /START
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):

    if not is_admin(message.from_user.id):

        await message.answer(
            "Цей бот є закритим пунктом управління "
            "агентством нерухомості Nestima."
        )

        return

    builder = InlineKeyboardBuilder()

    builder.row(
        types.InlineKeyboardButton(
            text="➕ Додати об'єкт за посиланням",
            callback_data="add_object"
        )
    )

    await message.answer(
        "Вітаю, босе! Це пульт управління "
        "агентством **Nestima**.\n\n"
        "Обери необхідну дію:",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


# ============================================================
# ДОБАВЛЕНИЕ ОБЪЕКТА
# ============================================================

@dp.callback_query(F.data == "add_object")
async def process_add_object(
    callback: types.CallbackQuery,
    state: FSMContext
):

    if not is_admin(callback.from_user.id):

        await callback.answer(
            "⛔ Немає доступу.",
            show_alert=True
        )

        return

    await callback.message.answer(
        "Надішли мені **посилання на оголошення**.\n\n"
        "Бот спарсить параметри, сформує "
        "мінімалістичний публічний пост "
        "та збере службову інформацію."
    )

    await state.set_state(
        FormStates.waiting_for_object_data
    )

    await callback.answer()


# ============================================================
# ОБРАБОТКА ССЫЛКИ
# ============================================================

@dp.message(FormStates.waiting_for_object_data)
async def handle_object_data(
    message: types.Message,
    state: FSMContext
):

    if not is_admin(message.from_user.id):
        return

    user_input = (
        message.text
        or message.caption
        or ""
    ).strip()

    url = extract_url(user_input)

    if not url:

        await message.answer(
            "❌ Надішли коректне посилання "
            "на оголошення!"
        )

        return

    processing_message = await message.answer(
        "⏳ Обробляю оголошення...\n\n"
        "🔎 Отримую дані\n"
        "📸 Збираю фотографії\n"
        "📄 Формую Telegraph\n"
        "📢 Готую публікацію"
    )

    try:

        # ----------------------------------------------------
        # PARSING В ОКРЕМОМ ПОТОЦІ
        # ----------------------------------------------------

        parsed_info = await asyncio.to_thread(
            parse_listing,
            url
        )

        # ----------------------------------------------------
        # OBJECT ID
        # ----------------------------------------------------

        obj_id = make_object_id()

        # ----------------------------------------------------
        # TELEGRAPH
        # ----------------------------------------------------

        telegraph_url = await asyncio.to_thread(
            create_telegraph_page,
            parsed_info,
            obj_id
        )

        # ----------------------------------------------------
        # PUBLIC CHANNEL TEXT
        # ----------------------------------------------------

        channel_parts = []

        if parsed_info["rooms"]:

            channel_parts.append(
                f"🏠 {parsed_info['rooms']}"
            )

        if parsed_info["area"]:

            channel_parts.append(
                f"📐 {parsed_info['area']}"
            )

        if parsed_info["price"]:

            channel_parts.append(
                f"💵 {parsed_info['price']}"
            )

        if parsed_info["address"]:

            channel_parts.append(
                f"📍 {parsed_info['address']}"
            )

        channel_text = (
            "\n".join(channel_parts)
            + "\n\n"
            + f"📄 **Всі деталі та фото:** "
            + telegraph_url
            + "\n\n"
            + parsed_info["district_hashtag"]
            + "\n"
            + parsed_info["bank_hashtag"]
            + "\n"
            + "#Nestima"
        )

        # ----------------------------------------------------
        # GOOGLE MAPS
        # ----------------------------------------------------

        maps_query = (
            parsed_info["address"]
            or "Дніпро"
        )

        maps_url = (
            "https://www.google.com/maps/search/"
            "?api=1&query="
            + urllib.parse.quote(
                maps_query
            )
        )

        # ----------------------------------------------------
        # КНОПКИ
        # ----------------------------------------------------

        builder = InlineKeyboardBuilder()

        builder.row(
            types.InlineKeyboardButton(
                text="📍 На мапі",
                url=maps_url
            ),
            types.InlineKeyboardButton(
                text="📝 Записатися на перегляд",
                callback_data=f"book_{obj_id}"
            )
        )

        # ----------------------------------------------------
        # PUBLIC CHANNEL
        # ----------------------------------------------------

        await bot.send_message(
            chat_id=PUBLIC_CHANNEL_ID,
            text=channel_text,
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )

        # ----------------------------------------------------
        # WORK CHAT
        # ----------------------------------------------------

        description = (
            parsed_info["description"]
        )

        # Telegram лимит
        if len(description) > 3000:

            description = (
                description[:3000]
                + "\n\n…"
            )

        work_chat_text = (
            f"📥 **Новий об'єкт №{obj_id} "
            f"у робочій базі!**\n\n"

            f"📌 **Назва:** "
            f"{parsed_info['title']}\n"

            f"📍 **Адреса:** "
            f"{parsed_info['address']}\n"

            f"💵 **Ціна:** "
            f"{parsed_info['price']}\n"

            f"📐 **Площа:** "
            f"{parsed_info['area'] or 'Не вказано'}\n"

            f"🏠 **Кімнати:** "
            f"{parsed_info['rooms'] or 'Не вказано'}\n"

            f"🏢 **Поверх:** "
            f"{parsed_info['floor'] or 'Не вказано'}\n"

            f"📞 **Телефон власника / ріелтора:** "
            f"`{parsed_info['phone']}`\n\n"

            f"📄 **Telegraph:** "
            f"{telegraph_url}\n\n"

            f"📄 **Опис:**\n"
            f"{description}\n\n"

            f"🔗 **Посилання на джерело:**\n"
            f"{url}"
        )

        await bot.send_message(
            chat_id=AGENT_WORK_CHAT_ID,
            text=work_chat_text,
            parse_mode="Markdown"
        )

        # ----------------------------------------------------
        # PHOTOS В WORK CHAT
        # ----------------------------------------------------

        photos = parsed_info["photos"][:10]

        if photos:

            media = []

            for photo in photos:

                try:

                    media.append(
                        types.InputMediaPhoto(
                            media=photo
                        )
                    )

                except Exception as photo_error:

                    logger.warning(
                        f"Photo skipped: "
                        f"{photo} | "
                        f"{photo_error}"
                    )

            if media:

                try:

                    await bot.send_media_group(
                        chat_id=AGENT_WORK_CHAT_ID,
                        media=media
                    )

                except Exception as media_error:

                    logger.error(
                        f"Media group error: "
                        f"{media_error}"
                    )

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        await processing_message.edit_text(
            f"✅ Успішно!\n\n"
            f"Об'єкт №{obj_id}\n"
            f"Пост опубліковано у канал.\n"
            f"Фото та службова інформація "
            f"надіслані в робочий чат."
        )

    except Exception as e:

        logger.exception(
            "Object processing error"
        )

        try:

            await processing_message.edit_text(
                "❌ Не вдалося обробити оголошення.\n\n"
                f"Помилка: {str(e)[:1000]}"
            )

        except Exception:

            await message.answer(
                "❌ Сталася помилка під час обробки."
            )

    finally:

        await state.clear()


# ============================================================
# КЛИЕНТ НАЖАЛ "ЗАПИСАТИСЯ"
# ============================================================

@dp.callback_query(
    F.data.startswith("book_")
)
async def process_channel_booking(
    callback: types.CallbackQuery
):

    try:

        obj_id = callback.data.split(
            "_",
            1
        )[1]

    except Exception:

        await callback.answer(
            "❌ Помилка заявки.",
            show_alert=True
        )

        return

    user = callback.from_user

    client_name = (
        user.full_name
        or "Не вказано"
    )

    client_username = (
        f"@{user.username}"
        if user.username
        else "Не вказано"
    )

    builder = InlineKeyboardBuilder()

    builder.row(
        types.InlineKeyboardButton(
            text="🟢 Прийняти заявку",
            callback_data=(
                f"claim_lead_"
                f"{obj_id}_"
                f"{user.id}"
            )
        )
    )

    await bot.send_message(
        chat_id=AGENT_WORK_CHAT_ID,
        text=(
            f"😱😱 **ЗАЯВКА НА ПЕРЕГЛЯД** "
            f"**(Об'єкт №{obj_id})** 😱😱\n\n"

            f"👤 **Клієнт:** "
            f"{client_name}\n"

            f"💬 **Telegram:** "
            f"{client_username}\n"

            f"🆔 **ID:** "
            f"`{user.id}`"
        ),
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

    await callback.answer(
        "✅ Заявку надіслано! "
        "Ріелтор зв'яжеться з вами.",
        show_alert=True
    )


# ============================================================
# АГЕНТ ЗАБИРАЕТ ЗАЯВКУ
# ============================================================

@dp.callback_query(
    F.data.startswith("claim_lead_")
)
async def claim_lead_action(
    callback: types.CallbackQuery
):

    # --------------------------------------------------------
    # Проверка агента
    #
    # Сейчас разрешаем брать заявку участникам
    # рабочего чата.
    #
    # Telegram сам гарантирует callback от реального
    # пользователя.
    #
    # Дополнительную проверку можно позже сделать через БД.
    # --------------------------------------------------------

    parts = callback.data.split("_")

    if len(parts) != 4:

        await callback.answer(
            "❌ Некоректна заявка.",
            show_alert=True
        )

        return

    obj_id = parts[2]
    client_id = parts[3]

    agent_name = (
        callback.from_user.full_name
        or "Агент"
    )

    # --------------------------------------------------------
    # Бронируем заявку
    # --------------------------------------------------------

    await callback.message.edit_text(
        text=(
            f"😱😱 **ЗАЯВКА НА ПЕРЕГЛЯД** "
            f"**(Об'єкт №{obj_id})** 😱😱\n\n"

            f"🔒 **Заброньовано агентом:** "
            f"{agent_name}\n"

            f"🆔 **ID клієнта:** "
            f"`{client_id}`"
        ),
        reply_markup=None,
        parse_mode="Markdown"
    )

    await callback.answer(
        "✅ Заявку успішно закріплено за вами.",
        show_alert=True
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@dp.errors()
async def global_error_handler(
    event
):

    logger.exception(
        f"Unhandled aiogram error: {event.exception}"
    )

    return True


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "Starting Nestima Telegram Bot..."
    )

    # Flask для Render
    keep_alive()

    # Telegraph
    await asyncio.to_thread(
        init_telegraph
    )

    # Удаляем старый webhook
    await bot.delete_webhook(
        drop_pending_updates=True
    )

    logger.info(
        "Telegram webhook removed."
    )

    logger.info(
        "Starting polling..."
    )

    try:

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )

    finally:

        await bot.session.close()

        logger.info(
            "Bot stopped."
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped manually."
        )