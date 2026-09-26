"""Публикация объектов в публичный канал и Telegraph-страницы."""
from __future__ import annotations

import json
import logging

import httpx
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Property, PropertyStatus, utcnow
from bot.database.repositories import settings as settings_repo
from bot.services import cards, property_service
from bot.services.context import AppContext
from bot.services.parser_service import HEADERS
from bot.services.settings_service import RuntimeSettings
from bot.services.workchat_service import public_post_url
from bot.utils import media
from bot.utils.telegram import safe_call

logger = logging.getLogger(__name__)

TELEGRAPH_API = "https://api.telegra.ph"
MAX_PHOTO_BYTES = 9 * 1024 * 1024


class PublicationError(Exception):
    pass


def photo_urls(prop: Property) -> list[str]:
    return [p for p in (prop.photos or []) if isinstance(p, str) and p.startswith("http")]


def photo_refs(prop: Property) -> list[str]:
    """Фото для Telegram: URL или file_id (фото, отправленные в бот, хранятся как 'tg:<file_id>')."""
    refs = []
    for p in prop.photos or []:
        if isinstance(p, str):
            refs.append(p[3:] if p.startswith("tg:") else p)
    return refs


# ---------------- Telegraph ----------------

async def _telegraph_token(ctx: AppContext, session: AsyncSession, client: httpx.AsyncClient) -> str | None:
    if ctx.config.telegraph_token:
        return ctx.config.telegraph_token
    token = await settings_repo.get_value(session, "telegraph_token")
    if token:
        return token
    resp = await client.post(
        f"{TELEGRAPH_API}/createAccount",
        data={"short_name": ctx.config.agency_name[:32], "author_name": ctx.config.agency_name[:128]},
    )
    data = resp.json()
    token = (data.get("result") or {}).get("access_token")
    if token:
        await settings_repo.set_value(session, "telegraph_token", token)
    return token


def telegraph_title(prop: Property) -> str:
    parts = [cards.headline(prop)]
    if prop.area:
        parts.append(f"{cards.fmt_number(prop.area)} м²")
    return (", ".join(parts) + f" — {cards.price_label(prop)}")[:256]


async def _upload_to_telegraph(client: httpx.AsyncClient, data: bytes) -> str | None:
    try:
        resp = await client.post("https://telegra.ph/upload", files={"file": ("photo.jpg", data, "image/jpeg")})
        result = resp.json()
        if isinstance(result, list) and result and result[0].get("src"):
            return "https://telegra.ph" + result[0]["src"]
    except (httpx.HTTPError, ValueError):
        pass
    return None


async def telegraph_photo_urls(ctx: AppContext, client: httpx.AsyncClient, prop: Property) -> list[str]:
    """URL фото для Telegraph. Фото с сайтов берутся как есть; фото, отправленные в бота,
    загружаются в Telegraph, а если это недоступно — отдаются через подписанную ссылку нашего сервера."""
    urls: list[str] = []
    for ref in (prop.photos or [])[:20]:
        if not isinstance(ref, str):
            continue
        if ref.startswith("http"):
            urls.append(ref)
            continue
        if not ref.startswith("tg:"):
            continue
        file_id = ref[3:]
        url = None
        try:
            file = await ctx.bot.get_file(file_id)
            buf = await ctx.bot.download_file(file.file_path)
            if buf is not None:
                url = await _upload_to_telegraph(client, buf.read())
        except Exception as exc:  # noqa: BLE001 - фото не должно ломать публикацию
            logger.warning("Telegraph: не удалось загрузить фото из Telegram: %s", type(exc).__name__)
        if url is None:
            url = ctx.public_url(media.media_path(ctx.config.bot_token, file_id))
        if url:
            urls.append(url)
    return urls


async def create_telegraph_page(ctx: AppContext, session: AsyncSession, prop: Property) -> str | None:
    """Создает страницу с фото и описанием. Внутренние данные CRM не передаются."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            token = await _telegraph_token(ctx, session, client)
            if not token:
                logger.warning("Telegraph: не удалось получить токен")
                return None
            photos = await telegraph_photo_urls(ctx, client, prop)
            content = cards.telegraph_nodes(prop, photos, booking_url=ctx.booking_link(prop.id))
            title = telegraph_title(prop)
            resp = await client.post(
                f"{TELEGRAPH_API}/createPage",
                data={
                    "access_token": token,
                    "title": title,
                    "author_name": ctx.config.agency_name[:128],
                    "content": json.dumps(content, ensure_ascii=False),
                    "return_content": "false",
                },
            )
            data = resp.json()
            if data.get("ok"):
                return data["result"]["url"]
            logger.warning("Telegraph: ошибка createPage: %s", data.get("error"))
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Telegraph недоступен: %s", type(exc).__name__)
    return None


# ---------------- публичный канал ----------------

def public_keyboard(ctx: AppContext, prop: Property) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="📅 Записаться на просмотр", url=ctx.booking_link(prop.id))]
    map_link = cards.map_url(prop)
    if map_link:
        row.append(InlineKeyboardButton(text="📍 На карте", url=map_link))
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def _download(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and len(resp.content) <= MAX_PHOTO_BYTES and resp.headers.get("content-type", "").startswith("image"):
                return resp.content
    except httpx.HTTPError:
        pass
    return None


async def _send_photo_with_fallback(ctx: AppContext, chat_id, ref: str, **kwargs):
    msg = await safe_call("publish_photo", ctx.bot.send_photo, chat_id=chat_id, photo=ref, **kwargs)
    if msg or not ref.startswith("http"):
        return msg
    # Telegram не смог скачать фото по URL (hotlink-защита) — загружаем сами
    data = await _download(ref)
    if data:
        return await safe_call(
            "publish_photo_upload", ctx.bot.send_photo, chat_id=chat_id, photo=BufferedInputFile(data, "photo.jpg"), **kwargs
        )
    return None


async def publish(ctx: AppContext, session: AsyncSession, rs: RuntimeSettings, prop: Property, user_id: int | None) -> str:
    channel = ctx.config.public_channel_id
    if not channel:
        raise PublicationError("Не настроен PUBLIC_CHANNEL_ID")
    if prop.is_archived:
        raise PublicationError("Объект в архиве — сначала верните его из архива")
    if prop.public_message_id:
        await unpublish(ctx, session, prop, user_id, record=False)

    if rs.create_telegraph:
        url = await create_telegraph_page(ctx, session, prop)
        if url:
            prop.telegraph_url = url

    keyboard = public_keyboard(ctx, prop)
    main_msg = None
    extra_ids: list[int] = []

    if prop.telegraph_url:
        # Основной формат: короткий текст, под ним превью Telegraph-страницы с фото, ниже кнопки
        main_msg = await safe_call(
            "publish_post",
            ctx.bot.send_message,
            chat_id=channel,
            text=cards.public_post_text(prop, ctx.config.agency_hashtag, prop.telegraph_url),
            reply_markup=keyboard,
            link_preview_options=LinkPreviewOptions(
                url=prop.telegraph_url, prefer_large_media=True, show_above_text=False
            ),
        )

    # Запасной формат (Telegraph недоступен): фото с подписью
    caption = cards.public_caption(prop, ctx.config.agency_hashtag)
    refs = photo_refs(prop)
    if main_msg is None and rs.publish_album and len(refs) >= 2:
        # Объекты aiogram неизменяемы — подпись задаем при создании первого элемента
        media_items = [
            InputMediaPhoto(media=r, caption=caption, parse_mode="HTML") if i == 0 else InputMediaPhoto(media=r)
            for i, r in enumerate(refs[:10])
        ]
        album = await safe_call("publish_album", ctx.bot.send_media_group, chat_id=channel, media=media_items)
        if album:
            extra_ids = [m.message_id for m in album]
            main_msg = await safe_call(
                "publish_buttons",
                ctx.bot.send_message,
                chat_id=channel,
                text=f"👆 {prop.code} · {cards.price_label(prop)}",
                reply_markup=keyboard,
            )
    if main_msg is None:
        for ref in refs[:3]:
            main_msg = await _send_photo_with_fallback(ctx, channel, ref, caption=caption, reply_markup=keyboard)
            if main_msg:
                break
    if main_msg is None:
        main_msg = await safe_call(
            "publish_text", ctx.bot.send_message, chat_id=channel, text=caption, reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    if main_msg is None:
        for mid in extra_ids:
            await safe_call("publish_rollback", ctx.bot.delete_message, chat_id=channel, message_id=mid)
        raise PublicationError("Telegram не принял публикацию. Проверьте, что бот — администратор канала.")

    prop.public_message_id = main_msg.message_id
    prop.public_extra_ids = extra_ids or None
    prop.published_at = utcnow()
    await property_service.add_history(session, prop.id, "published", user_id, None, None, "канал")
    if prop.status in (PropertyStatus.ACTIVE, PropertyStatus.DRAFT):
        await property_service.change_status(session, prop, PropertyStatus.PUBLISHED, user_id)
    logger.info("Объект %s опубликован в канал", prop.code)
    return public_post_url(ctx, prop) or ""


async def unpublish(ctx: AppContext, session: AsyncSession, prop: Property, user_id: int | None, record: bool = True) -> None:
    channel = ctx.config.public_channel_id
    if channel and prop.public_message_id:
        ids = [prop.public_message_id, *(prop.public_extra_ids or [])]
        for mid in ids:
            ok = await safe_call("unpublish", ctx.bot.delete_message, chat_id=channel, message_id=mid)
            if not ok and mid == prop.public_message_id:
                # Удалить не удалось (например, старое сообщение) — помечаем пост как неактуальный
                await safe_call(
                    "unpublish_edit", ctx.bot.edit_message_reply_markup, chat_id=channel, message_id=mid, reply_markup=None
                )
    prop.public_message_id = None
    prop.public_extra_ids = None
    if record:
        await property_service.add_history(session, prop.id, "unpublished", user_id)
        if prop.status == PropertyStatus.PUBLISHED:
            await property_service.change_status(session, prop, PropertyStatus.ACTIVE, user_id)
    logger.info("Объект %s снят с публикации", prop.code)
