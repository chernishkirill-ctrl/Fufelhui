"""Новый формат поста в канале и Mini App «Записаться на просмотр»."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from decimal import Decimal
from urllib.parse import urlencode

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from bot.database.models import Lead, LeadStatus, Property
from bot.services import property_service, publication_service, settings_service
from bot.webapp import setup_webapp
from tests.conftest import CHANNEL, WORK_CHAT

TOKEN = "123456:TEST"


def signed_init_data(user_id: int = 4004, start_param: str = "lead_1", token: str = TOKEN, age: int = 0) -> str:
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAE",
        "start_param": start_param,
        "user": json.dumps({"id": user_id, "first_name": "Анна", "username": "anna_client"}, ensure_ascii=False),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def make_property(session_factory, **extra) -> int:
    async with session_factory() as s:
        data = {"offer_type": "sale", "property_type": "Квартира", "rooms": 2, "area": Decimal("56"), "floor": 5,
                "floors_total": 9, "price": Decimal("45000"), "currency": "USD", "city": "Днепр", "district": "Соборный",
                "address": "ул. Тайная, 7", "owner_name": "Петр", "owner_phone": "+380671112233",
                "description": "Светлая квартира с ремонтом. Звоните +380671112233",
                "photos": ["https://example.com/1.jpg", "https://example.com/2.jpg"]}
        data.update(extra)
        prop = await property_service.create_property(s, data, created_by_id=None, responsible_id=None)
        await s.commit()
        return prop.id


async def test_channel_post_short_text_with_telegraph_preview(ctx, session_factory, monkeypatch):
    ctx.config = replace(ctx.config, webapp_short_name="zapis")
    pid = await make_property(session_factory)

    async def fake_page(ctx_, session, prop):
        return "https://telegra.ph/Kvartira-09-26"

    monkeypatch.setattr(publication_service, "create_telegraph_page", fake_page)
    async with session_factory() as s:
        prop = await s.get(Property, pid)
        rs = await settings_service.load(s, ctx.config)
        await publication_service.publish(ctx, s, rs, prop, None)
        await s.commit()
    api = ctx.bot.session
    post = [c for c in api.of("SendMessage") if c.chat_id == CHANNEL][-1]
    assert not api.of("SendPhoto")  # фото — в превью Telegraph, а не отдельным постом
    assert post.link_preview_options.url == "https://telegra.ph/Kvartira-09-26"
    assert post.link_preview_options.prefer_large_media and not post.link_preview_options.show_above_text
    text = post.text
    assert "2-кімнатна квартира" in text and "56 м²" in text and "поверх 5/9" in text and "$45 000" in text
    assert "#Соборний" in text and "#Дніпро" in text and "#2кімн" in text and "#продаж" in text
    assert "Соборний район, Дніпро" in text and "📸 Фото та детальний опис" in text
    for secret in ("Петр", "+380", "Тайная", "Светлая квартира"):
        assert secret not in text  # описание и приватные данные — не в посте
    buttons = post.reply_markup.inline_keyboard
    assert len(buttons) == 1 and [b.text for b in buttons[0]] == ["📅 Записатися на перегляд", "📍 На мапі"]
    assert buttons[0][0].url == f"https://t.me/crm_test_bot/zapis?startapp=lead_{pid}&mode=compact"


async def test_booking_link_falls_back_to_bot_chat(ctx):
    assert ctx.booking_link(7) == "https://t.me/crm_test_bot?start=lead_7"


async def _client(ctx) -> TestClient:
    app = web.Application()
    setup_webapp(app, ctx)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_mini_app_submit_creates_lead_in_work_group(ctx, session_factory):
    pid = await make_property(session_factory)
    client = await _client(ctx)
    try:
        page = await client.get("/webapp/lead")
        assert page.status == 200 and "telegram-web-app.js" in await page.text()

        info = await (await client.get(f"/api/webapp/property/lead_{pid}")).json()
        assert info["ok"] and info["price"] == "$45 000" and "Тайная" not in json.dumps(info, ensure_ascii=False)

        payload = {"init_data": signed_init_data(start_param=f"lead_{pid}"), "property_id": f"lead_{pid}",
                   "name": "Анна", "phone": "+380 50 123 45 67", "comment": "Вечером"}
        resp = await client.post("/api/webapp/lead", json=payload)
        assert resp.status == 200 and (await resp.json())["ok"]

        async with session_factory() as s:
            lead = await s.scalar(select(Lead))
            assert lead.property_id == pid and lead.client_telegram_id == 4004 and lead.client_username == "anna_client"
            assert lead.phone == "+380 50 123 45 67" and lead.status == LeadStatus.NEW and lead.work_message_id
        group = [c for c in ctx.bot.session.of("SendMessage") if c.chat_id == WORK_CHAT][-1]
        assert group.message_thread_id == 15 and "Новая заявка" in group.text and "+380 50" not in group.text
        assert group.reply_markup.inline_keyboard[0][0].text == "🙋 Взять клиента"

        # повторная заявка на тот же объект — отказ (антиспам)
        again = await client.post("/api/webapp/lead", json=payload)
        assert again.status == 409
    finally:
        await client.close()


async def test_mini_app_rejects_forged_or_invalid_requests(ctx, session_factory):
    pid = await make_property(session_factory)
    client = await _client(ctx)
    try:
        base = {"property_id": str(pid), "name": "Анна", "phone": "+380501234567"}
        forged = await client.post("/api/webapp/lead", json={**base, "init_data": signed_init_data(token="999:OTHER")})
        assert forged.status == 403
        stale = await client.post("/api/webapp/lead", json={**base, "init_data": signed_init_data(age=3 * 86400)})
        assert stale.status == 403
        no_phone = await client.post("/api/webapp/lead", json={**base, "phone": "123", "init_data": signed_init_data()})
        assert no_phone.status == 400
        async with session_factory() as s:
            assert await s.scalar(select(Lead)) is None
    finally:
        await client.close()


async def test_media_proxy_requires_signature(ctx):
    client = await _client(ctx)
    try:
        assert (await client.get("/media/bad/ABC.jpg")).status == 404
    finally:
        await client.close()
