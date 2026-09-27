"""HTTP-часть: Mini App «Записаться на просмотр» и раздача фото для Telegraph.

Mini App открывается кнопкой в публичном канале (ссылка t.me/<бот>/<app>?startapp=lead_<id>&mode=compact)
компактным окном поверх канала. Клиент вводит имя и телефон, заявка уходит в рабочую группу.
Подлинность клиента проверяется по подписи initData (HMAC от токена бота) — подделать заявку
от чужого Telegram-аккаунта нельзя.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web

from bot.services import cards, lead_actions, lead_service, property_service, settings_service
from bot.services.context import AppContext
from bot.utils import media
from bot.utils.text import find_phone

logger = logging.getLogger(__name__)

INIT_DATA_MAX_AGE = 24 * 3600
PAGE = Path(__file__).with_name("webapp_static") / "lead.html"
MAX_BODY = 16 * 1024


def _json_error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def _parse_property_id(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    if raw.startswith("lead_"):
        raw = raw[5:]
    return int(raw) if raw.isdigit() else None


def setup_webapp(app: web.Application, ctx: AppContext) -> None:
    async def lead_page(request: web.Request) -> web.Response:
        return web.Response(
            text=PAGE.read_text(encoding="utf-8"),
            content_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    async def property_info(request: web.Request) -> web.Response:
        prop_id = _parse_property_id(request.match_info.get("prop_id"))
        if prop_id is None:
            return _json_error("bad id", 404)
        async with ctx.session_factory() as session:
            prop = await property_service.get_property(session, prop_id)
            if prop is None or prop.is_archived:
                return _json_error("Об'єкт вже неактуальний", 404)
            # Только публичные данные
            details = []
            if prop.area:
                details.append(f"{cards.fmt_number(prop.area)} м²")
            floor = cards.floor_label_uk(prop)
            if floor:
                details.append(f"поверх {floor}")
            return web.json_response(
                {
                    "ok": True,
                    "code": prop.code,
                    "title": cards.headline_uk(prop),
                    "price": cards.price_label_uk(prop),
                    "location": cards.location_line_uk(prop),
                    "details": " · ".join(details),
                }
            )

    async def submit(request: web.Request) -> web.Response:
        if request.content_length and request.content_length > MAX_BODY:
            return _json_error("too large", 413)
        try:
            body = await request.json()
        except ValueError:
            return _json_error("bad json")
        try:
            init = safe_parse_webapp_init_data(ctx.config.bot_token, str(body.get("init_data") or ""))
        except ValueError:
            logger.warning("Mini App: неверная подпись initData")
            return _json_error("Відкрийте форму з Telegram", 403)
        auth_ts = int(init.auth_date.timestamp())
        if time.time() - auth_ts > INIT_DATA_MAX_AGE:
            return _json_error("Сесія застаріла, відкрийте форму ще раз", 403)
        user = init.user
        if user is None:
            return _json_error("Відкрийте форму з Telegram", 403)

        prop_id = _parse_property_id(str(body.get("property_id") or init.start_param or ""))
        name = str(body.get("name") or "").strip()[:128]
        phone_raw = str(body.get("phone") or "").strip()[:64]
        comment = str(body.get("comment") or "").strip()[:1000] or None
        if prop_id is None:
            return _json_error("Не вказано об'єкт")
        if len(name) < 2:
            return _json_error("Вкажіть ім'я")
        phone = find_phone(phone_raw)
        if not phone:
            return _json_error("Вкажіть номер телефону, наприклад +380 67 123 45 67")

        async with ctx.session_factory() as session:
            prop = await property_service.get_property(session, prop_id)
            if prop is None or prop.is_archived:
                return _json_error("Об'єкт вже неактуальний", 404)
            rs = await settings_service.load(session, ctx.config)
            try:
                lead = await lead_actions.submit_lead(
                    ctx, session, rs, prop,
                    client_telegram_id=user.id,
                    client_username=user.username,
                    client_name=name,
                    phone=phone,
                    comment=comment,
                    source="mini-app",
                )
            except lead_service.LeadError as exc:
                return _json_error(str(exc), 409)
        return web.json_response({"ok": True, "code": lead.code})

    async def media_file(request: web.Request) -> web.StreamResponse:
        sig = request.match_info["sig"]
        file_id = request.match_info["file_id"]
        if not media.verify(ctx.config.bot_token, sig, file_id):
            raise web.HTTPNotFound()
        try:
            file = await ctx.bot.get_file(file_id)
            buf = await ctx.bot.download_file(file.file_path)
        except Exception:  # noqa: BLE001
            raise web.HTTPNotFound() from None
        return web.Response(
            body=buf.read() if buf else b"",
            content_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    app.router.add_get("/webapp/lead", lead_page)
    app.router.add_get("/api/webapp/property/{prop_id}", property_info)
    app.router.add_post("/api/webapp/lead", submit)
    app.router.add_get("/media/{sig}/{file_id}.jpg", media_file)
