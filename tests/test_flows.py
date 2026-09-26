"""Сквозные сценарии через настоящий Dispatcher: права доступа, объекты, импорт, публикация, заявки, сделки, отчеты."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from bot.database.models import Deal, DealStatus, Lead, LeadStatus, Property, PropertyStatus, User, UserStatus
from bot.handlers import reports as reports_h
from bot.keyboards import menus
from bot.keyboards.callbacks import DealCB, FormCB, LeadCB, PropCB, RealtorCB, ReportCB, SettingsCB
from bot.services import parser_service
from tests.conftest import CHANNEL, OWNER_ID, WORK_CHAT
from tests.test_parsers_cards import olx_html

R1, R2, STRANGER, CLIENT = 2001, 2002, 3003, 4004


async def add_realtors(session_factory, *ids):
    async with session_factory() as s:
        for tg in ids:
            s.add(User(telegram_id=tg, username=f"realtor{tg}", first_name=f"R{tg}", status=UserStatus.ACTIVE))
        await s.commit()


def alerts(api) -> list[str]:
    return [c.text for c in api.of("AnswerCallbackQuery") if c.text]


async def test_access_control(app, updates, session_factory):
    await app.feed(updates.message(STRANGER, "/start"))
    assert "Записаться на просмотр" in app.api.last_text(STRANGER)

    # чужой пользователь жмет админские кнопки и шлет тексты меню
    await app.feed(updates.callback(STRANGER, RealtorCB(action="list").pack()))
    await app.feed(updates.callback(STRANGER, SettingsCB(action="toggle", key="auto_publish").pack()))
    await app.feed(updates.callback(STRANGER, PropCB(action="menu").pack()))
    assert alerts(app.api)[-3:] == ["Доступ запрещен."] * 3
    await app.feed(updates.message(STRANGER, menus.OWNER_STATS))
    assert "Статистика" not in app.api.last_text(STRANGER)
    await app.feed(updates.message(STRANGER, "/start obj_1"))
    assert app.api.last_text(STRANGER) == "Доступ запрещен."
    await app.feed(updates.message(STRANGER, "/admin"))
    assert app.api.last_text(STRANGER) == "Доступ запрещен."

    # риелтор не может пользоваться пультом владельца
    await add_realtors(session_factory, R1)
    await app.feed(updates.callback(R1, SettingsCB(action="toggle", key="auto_publish").pack()))
    assert alerts(app.api)[-1] == "Доступ запрещен."
    await app.feed(updates.message(R1, menus.OWNER_REALTORS))
    assert "Риелторы" not in app.api.last_text(R1)
    async with session_factory() as s:
        assert (await s.scalar(select(func.count()).select_from(User).where(User.telegram_id == STRANGER))) == 0

    # владелец — только по OWNER_ID; username значения не имеет
    await app.feed(updates.message(OWNER_ID, "/start", username="someone"))
    assert "Пульт управления" in app.api.last_text(OWNER_ID)
    await app.feed(updates.message(5555, "/start", username="someone"))
    assert "Пульт" not in app.api.last_text(5555)


async def test_owner_adds_and_blocks_realtor(app, updates, session_factory):
    await app.feed(updates.callback(OWNER_ID, RealtorCB(action="add").pack()))
    await app.feed(updates.message(OWNER_ID, str(R1)))
    async with session_factory() as s:
        user = await s.scalar(select(User).where(User.telegram_id == R1))
        assert user and user.status == UserStatus.ACTIVE
        uid = user.id
    await app.feed(updates.message(R1, "/start"))
    assert "кабинет риелтора" in app.api.last_text(R1)

    await app.feed(updates.callback(OWNER_ID, RealtorCB(action="block", id=uid).pack()))
    await app.feed(updates.message(R1, "/start"))
    assert app.api.last_text(R1) == "Доступ запрещен."
    await app.feed(updates.message(R1, menus.R_MY_PROPS))
    assert "Мои объекты" not in app.api.last_text(R1)


async def test_invite_link(app, updates, session_factory):
    await app.feed(updates.callback(OWNER_ID, RealtorCB(action="invite").pack()))
    text = app.api.last_text(OWNER_ID)
    token = text.split("start=inv_")[1].split("<")[0].strip()
    await app.feed(updates.message(R2, f"/start inv_{token}"))
    assert "Доступ открыт" in app.api.last_text(R2)
    await app.feed(updates.message(STRANGER, f"/start inv_{token}"))
    assert "недействительно" in app.api.last_text(STRANGER)


async def _manual_add(app, updates, uid):
    await app.feed(updates.message(uid, menus.R_ADD))
    await app.feed(updates.callback(uid, FormCB(field="offer", value="sale").pack()))
    await app.feed(updates.callback(uid, FormCB(field="ptype", value="Квартира").pack()))
    await app.feed(updates.callback(uid, FormCB(field="rooms", value="1").pack()))
    await app.feed(updates.message(uid, "42"))
    await app.feed(updates.message(uid, "5/9"))
    await app.feed(updates.message(uid, "45000$"))
    await app.feed(updates.callback(uid, FormCB(field="city", value="Днепр").pack()))
    await app.feed(updates.message(uid, "Слобожанский"))
    await app.feed(updates.message(uid, "ул. Тайная, 7"))
    await app.feed(updates.message(uid, "Светлая квартира с ремонтом"))
    await app.feed(updates.message(uid, "Петр +380671112233"))
    await app.feed(updates.callback(uid, FormCB(field="done").pack()))
    await app.feed(updates.callback(uid, FormCB(field="addsave").pack()))


async def test_realtor_manual_add_and_permissions(app, updates, session_factory):
    await add_realtors(session_factory, R1, R2)
    await _manual_add(app, updates, R1)
    async with session_factory() as s:
        prop = await s.scalar(select(Property))
        assert prop.rooms == 1 and prop.area == Decimal("42") and (prop.floor, prop.floors_total) == (5, 9)
        assert prop.price == Decimal("45000") and prop.owner_phone == "+380671112233" and prop.owner_name == "Петр"
        assert prop.base_message_id is not None and prop.status == PropertyStatus.ACTIVE
        r1 = await s.scalar(select(User).where(User.telegram_id == R1))
        assert prop.responsible_realtor_id == r1.id
        pid = prop.id
    base = [c for c in app.api.of("SendMessage") if c.chat_id == WORK_CHAT]
    assert base and base[-1].message_thread_id == 11 and f"OBJ-{pid:06d}" in base[-1].text
    assert "+380" not in base[-1].text

    # второй риелтор видит карточку, но не может менять/архивировать/удалять
    await app.feed(updates.callback(R2, PropCB(action="ef", id=pid, arg="price").pack()))
    await app.feed(updates.callback(R2, PropCB(action="ss", id=pid, arg="archived").pack()))
    await app.feed(updates.callback(R2, PropCB(action="dely", id=pid).pack()))
    assert alerts(app.api)[-3:] == ["Доступ запрещен."] * 3
    await app.feed(updates.message(R2, f"/start arch_{pid}"))
    assert app.api.last_text(R2) == "Доступ запрещен."

    # ответственный риелтор архивирует — объект остается в БД, карточка уходит в тему «Архив»
    await app.feed(updates.callback(R1, PropCB(action="ss", id=pid, arg="archived").pack()))
    async with session_factory() as s:
        prop = await s.get(Property, pid)
        assert prop.status == PropertyStatus.ARCHIVED and prop.archive_message_id
    arch = [c for c in app.api.of("SendMessage") if c.chat_id == WORK_CHAT and c.message_thread_id == 12]
    assert arch and "Архив" in arch[-1].text
    await app.feed(updates.callback(R1, PropCB(action="unarch", id=pid).pack()))
    async with session_factory() as s:
        assert (await s.get(Property, pid)).status == PropertyStatus.ACTIVE


async def test_import_publish_lead_take_deal(app, updates, session_factory, monkeypatch):
    await add_realtors(session_factory, R1, R2)

    async def fake_fetch(url):
        return olx_html()

    monkeypatch.setattr(parser_service, "fetch_html", fake_fetch)

    # --- импорт и публикация ---
    await app.feed(updates.message(R1, menus.R_IMPORT))
    await app.feed(updates.message(R1, "вот https://www.olx.ua/d/uk/obyavlenie/prodam-IDabc.html"))
    assert "Проверьте данные объекта" in app.api.last_text(R1)
    await app.feed(updates.callback(R1, FormCB(field="impsavepub").pack()))
    async with session_factory() as s:
        prop = await s.scalar(select(Property))
        pid = prop.id
        assert prop.status == PropertyStatus.PUBLISHED and prop.public_message_id and prop.source_name == "OLX"
    photo = app.api.of("SendPhoto")[-1]
    assert photo.chat_id == CHANNEL
    for secret in ("+380", "Иван", "ivan_owner", "realtor"):
        assert secret not in photo.caption
    buttons = [b for row in photo.reply_markup.inline_keyboard for b in row]
    assert any(b.text == "📅 Записаться на просмотр" and b.url.endswith(f"start=lead_{pid}") for b in buttons)
    assert any(b.text == "📍 На карте" for b in buttons)

    # --- клиент записывается на просмотр ---
    await app.feed(updates.message(CLIENT, f"/start lead_{pid}", username="client_x"))
    await app.feed(updates.callback(CLIENT, FormCB(field="bname", value="tg").pack()))
    await app.feed(updates.message(CLIENT, "+380 50 123 45 67"))
    await app.feed(updates.callback(CLIENT, FormCB(field="btime", value="Завтра").pack()))
    await app.feed(updates.callback(CLIENT, FormCB(field="bskip", value="1").pack()))
    await app.feed(updates.callback(CLIENT, FormCB(field="bsend").pack()))
    async with session_factory() as s:
        lead = await s.scalar(select(Lead))
        assert lead.status == LeadStatus.NEW and lead.client_telegram_id == CLIENT and lead.phone
        lid = lead.id
    group = [c for c in app.api.of("SendMessage") if c.chat_id == WORK_CHAT and c.message_thread_id == 15]
    assert group and "Новая заявка" in group[-1].text
    assert "+380 50" not in group[-1].text and "client_x" not in group[-1].text and str(CLIENT) not in group[-1].text

    # --- «Взять клиента»: первый получает, второй — нет ---
    await app.feed(updates.callback(R2, LeadCB(action="take", id=lid).pack(), chat_id=WORK_CHAT, chat_type="supergroup"))
    await app.feed(updates.callback(R1, LeadCB(action="take", id=lid).pack(), chat_id=WORK_CHAT, chat_type="supergroup"))
    assert "Заявку уже забрал другой риелтор." in alerts(app.api)
    async with session_factory() as s:
        lead = await s.get(Lead, lid)
        r2 = await s.scalar(select(User).where(User.telegram_id == R2))
        assert lead.assigned_realtor_id == r2.id and lead.status == LeadStatus.ASSIGNED
    dm = [c for c in app.api.of("SendMessage") if c.chat_id == R2]
    assert dm and "+380 50 123 45 67" in dm[-1].text  # данные клиента — назначенному риелтору
    await app.feed(updates.callback(R1, LeadCB(action="open", id=lid).pack()))
    assert alerts(app.api)[-1] == "Доступ запрещен."  # другим риелторам — нет
    await app.feed(updates.callback(STRANGER, LeadCB(action="take", id=lid).pack()))
    assert alerts(app.api)[-1] == "Доступ запрещен."

    # --- сделку фиксирует R2 (объект чужой, клиент — его) ---
    await app.feed(updates.callback(R2, LeadCB(action="st", id=lid, arg="viewing_scheduled").pack()))
    await app.feed(updates.message(R2, "28.09 18:30"))
    await app.feed(updates.callback(R2, LeadCB(action="st", id=lid, arg="viewing_done").pack()))
    await app.feed(updates.callback(R2, DealCB(action="new", arg=f"l{lid}").pack()))
    await app.feed(updates.callback(R2, FormCB(field="dtype", value="sale").pack()))
    await app.feed(updates.message(R2, "44000"))
    await app.feed(updates.message(R2, "3%"))
    await app.feed(updates.callback(R2, FormCB(field="ddate", value="today").pack()))
    await app.feed(updates.callback(R2, FormCB(field="skip").pack()))
    await app.feed(updates.callback(R2, FormCB(field="dconfirm").pack()))
    async with session_factory() as s:
        deal = await s.scalar(select(Deal))
        assert deal.commission == Decimal("1320.00") and deal.currency == "USD" and deal.status == DealStatus.CONFIRMED
        assert deal.client_id == lid and deal.realtor_id == r2.id
        prop = await s.get(Property, pid)
        assert prop.status == PropertyStatus.SOLD and prop.public_message_id is None
        lead = await s.get(Lead, lid)
        assert lead.status == LeadStatus.CLOSED and lead.viewing_done_at is not None
        did = deal.id
    deals_topic = [c for c in app.api.of("SendMessage") if c.chat_id == WORK_CHAT and c.message_thread_id == 13]
    assert deals_topic and "СДЕЛКА ЗАКРЫТА" in deals_topic[-1].text and "$1 320" in deals_topic[-1].text
    assert "44 000" not in deals_topic[-1].text
    assert any(c.chat_id == CHANNEL for c in app.api.of("DeleteMessage"))
    assert [c for c in app.api.of("SendMessage") if c.chat_id == WORK_CHAT and c.message_thread_id == 12]

    # --- статистика: владелец видит всё, риелтор — только свое ---
    await app.feed(updates.message(OWNER_ID, menus.OWNER_STATS))
    text = app.api.last_text(OWNER_ID)
    assert "Сделок: 1" in text and "$1 320" in text and "Просмотров: 1" in text
    await app.feed(updates.message(R1, menus.R_STATS))
    text = app.api.last_text(R1)
    assert "Сделок: 0" in text and "1 320" not in text and "realtor2002" not in text

    # --- риелтор не может править/отменять сделку, владелец может ---
    await app.feed(updates.callback(R2, DealCB(action="cancely", id=did, arg="restore").pack()))
    assert alerts(app.api)[-1] == "Доступ запрещен."
    await app.feed(updates.callback(R1, DealCB(action="open", id=did).pack()))
    assert alerts(app.api)[-1] == "Доступ запрещен."
    await app.feed(updates.callback(OWNER_ID, DealCB(action="cancely", id=did, arg="restore").pack()))
    async with session_factory() as s:
        assert (await s.get(Deal, did)).status == DealStatus.CANCELLED
        assert (await s.get(Property, pid)).status == PropertyStatus.ACTIVE

    # --- история объекта содержит всю цепочку ---
    await app.feed(updates.callback(OWNER_ID, PropCB(action="hist", id=pid).pack()))
    history = app.api.last_text(OWNER_ID)
    for marker in ("создан", "опубликован", "новая заявка", "клиента взял", "сделка", "в архив", "сделка отменена"):
        assert marker in history, marker


async def test_reports(app, updates, session_factory, ctx):
    await add_realtors(session_factory, R1, R2)
    assert await reports_h.send_report_reminders(ctx) == 2
    assert await reports_h.send_report_reminders(ctx) == 0  # идемпотентно в течение дня
    reminder = [c for c in app.api.of("SendMessage") if c.chat_id == R1][-1]
    assert "ежедневного отчета" in reminder.text

    await app.feed(updates.callback(R1, ReportCB(action="fill").pack()))
    for value in ("2", "10", "3", "1", "0", "0"):
        await app.feed(updates.callback(R1, FormCB(field="rnum", value=value).pack()))
    await app.feed(updates.message(R1, "Провел 1 показ"))
    await app.feed(updates.callback(R1, FormCB(field="rsend").pack()))
    assert "Отчет сохранен" in app.api.last_text(R1)

    await app.feed(updates.message(OWNER_ID, menus.OWNER_REPORTS))
    text = app.api.last_text(OWNER_ID)
    assert "✅ User: клиенты 2, звонки 10" in text  # имя обновилось из профиля Telegram
    assert "❌ @realtor2002: Отчет не предоставлен" in text


async def test_group_chat_is_quiet(app, updates):
    await app.feed(updates.message(R1, "обычное сообщение в теме Чат", chat_id=WORK_CHAT, chat_type="supergroup", thread_id=14))
    assert app.api.calls == []
    await app.feed(updates.message(OWNER_ID, "/chatinfo", chat_id=WORK_CHAT, chat_type="supergroup", thread_id=14))
    assert "message_thread_id" in app.api.last_text(WORK_CHAT)
    await app.feed(updates.message(R1, "/chatinfo", chat_id=WORK_CHAT, chat_type="supergroup", thread_id=14))
    assert len(app.api.of("SendMessage")) == 1


async def test_owner_screens(app, updates, session_factory, ctx):
    from bot.keyboards.callbacks import StatsCB

    await add_realtors(session_factory, R1)
    # настройки: экран, переключатель, время отчетов, создание тем, проверка
    await app.feed(updates.message(OWNER_ID, menus.OWNER_SETTINGS))
    assert "Настройки CRM" in app.api.last_text(OWNER_ID)
    await app.feed(updates.callback(OWNER_ID, SettingsCB(action="toggle", key="auto_publish").pack()))
    await app.feed(updates.callback(OWNER_ID, SettingsCB(action="report_time").pack()))
    await app.feed(updates.message(OWNER_ID, "21:30"))
    assert "21:30" in app.api.last_text(OWNER_ID)
    await app.feed(updates.callback(OWNER_ID, SettingsCB(action="check").pack()))
    assert "Проверка подключения" in app.api.last_text(OWNER_ID)
    await app.feed(updates.callback(OWNER_ID, SettingsCB(action="remind_now").pack()))
    assert "Запрос отчета отправлен риелторам: 1" in app.api.last_text(OWNER_ID)

    # объект владельца: автопубликация включена выше
    async with session_factory() as s:
        from bot.services import property_service

        prop = await property_service.create_property(
            s, {"offer_type": "rent", "rooms": 2, "price": Decimal("500"), "currency": "USD", "district": "Центр",
                "photos": ["https://example.com/1.jpg", "https://example.com/2.jpg"]},
            created_by_id=None, responsible_id=None,
        )
        await s.commit()
        pid = prop.id
    await app.feed(updates.callback(OWNER_ID, PropCB(action="open", id=pid).pack()))
    assert f"OBJ-{pid:06d}" in app.api.last_text(OWNER_ID)
    await app.feed(updates.callback(OWNER_ID, PropCB(action="ef", id=pid, arg="price").pack()))
    await app.feed(updates.message(OWNER_ID, "650"))
    async with session_factory() as s:
        assert (await s.get(Property, pid)).price == Decimal("650")
    await app.feed(updates.callback(OWNER_ID, PropCB(action="asg", id=pid).pack()))
    async with session_factory() as s:
        r1 = await s.scalar(select(User).where(User.telegram_id == R1))
    await app.feed(updates.callback(OWNER_ID, PropCB(action="asgs", id=pid, arg=str(r1.id)).pack()))
    assert any(c.chat_id == R1 and "ответственным" in c.text for c in app.api.of("SendMessage"))

    # публикация альбомом и снятие
    await app.feed(updates.callback(OWNER_ID, SettingsCB(action="toggle", key="publish_album").pack()))
    await app.feed(updates.callback(OWNER_ID, PropCB(action="pub", id=pid).pack()))
    assert app.api.of("SendMediaGroup") and app.api.of("SendMediaGroup")[-1].chat_id == CHANNEL
    async with session_factory() as s:
        prop = await s.get(Property, pid)
        assert prop.status == PropertyStatus.PUBLISHED and len(prop.public_extra_ids) == 2
    await app.feed(updates.callback(OWNER_ID, PropCB(action="unpub", id=pid).pack()))
    assert len([c for c in app.api.of("DeleteMessage") if c.chat_id == CHANNEL]) == 3
    async with session_factory() as s:
        assert (await s.get(Property, pid)).status == PropertyStatus.ACTIVE

    # поиск по номеру и тексту
    await app.feed(updates.callback(OWNER_ID, PropCB(action="search").pack()))
    await app.feed(updates.message(OWNER_ID, f"OBJ-{pid:06d}"))
    assert f"OBJ-{pid:06d}" in app.api.last_text(OWNER_ID)
    await app.feed(updates.callback(OWNER_ID, PropCB(action="search").pack()))
    await app.feed(updates.message(OWNER_ID, "Центр"))
    assert "найдено 1" in app.api.last_text(OWNER_ID)

    # сделка владельцем на риелтора и правка комиссии
    await app.feed(updates.message(OWNER_ID, menus.OWNER_DEALS))
    await app.feed(updates.callback(OWNER_ID, DealCB(action="new").pack()))
    await app.feed(updates.callback(OWNER_ID, FormCB(field="dprop", value=str(pid)).pack()))
    await app.feed(updates.callback(OWNER_ID, FormCB(field="drealtor", value=str(r1.id)).pack()))
    await app.feed(updates.callback(OWNER_ID, FormCB(field="dtype", value="rent").pack()))
    await app.feed(updates.message(OWNER_ID, "650"))
    await app.feed(updates.message(OWNER_ID, "325"))
    await app.feed(updates.message(OWNER_ID, "25.09.2026"))
    await app.feed(updates.message(OWNER_ID, "Первый месяц"))
    await app.feed(updates.callback(OWNER_ID, FormCB(field="dconfirm").pack()))
    async with session_factory() as s:
        deal = await s.scalar(select(Deal))
        assert deal.realtor_id == r1.id and deal.commission == Decimal("325") and deal.comment == "Первый месяц"
        assert (await s.get(Property, pid)).status == PropertyStatus.RENTED
    await app.feed(updates.callback(OWNER_ID, DealCB(action="ed", id=deal.id, arg="commission").pack()))
    await app.feed(updates.message(OWNER_ID, "300"))
    async with session_factory() as s:
        assert (await s.get(Deal, deal.id)).commission == Decimal("300")

    # статистика за произвольный период и карточка риелтора
    await app.feed(updates.callback(OWNER_ID, StatsCB(period="custom").pack()))
    await app.feed(updates.message(OWNER_ID, "01.09.2026-30.09.2026"))
    assert "$300" in app.api.last_text(OWNER_ID)
    await app.feed(updates.callback(OWNER_ID, RealtorCB(action="open", id=r1.id).pack()))
    assert "Telegram ID" in app.api.last_text(OWNER_ID) and "$300" in app.api.last_text(OWNER_ID)

    # удаление запрещено при наличии сделки
    await app.feed(updates.callback(OWNER_ID, PropCB(action="dely", id=pid).pack()))
    assert "удаление запрещено" in alerts(app.api)[-1]


async def test_create_topics(app, updates, session_factory, ctx):
    from dataclasses import replace

    ctx.config = replace(ctx.config, base_topic_id=None, archive_topic_id=None, deals_topic_id=None,
                         chat_topic_id=None, leads_topic_id=None)
    await app.feed(updates.callback(OWNER_ID, SettingsCB(action="topics").pack()))
    assert len(app.api.of("CreateForumTopic")) == 5
    from bot.services import settings_service

    async with session_factory() as s:
        rs = await settings_service.load(s, ctx.config)
        assert all(rs.topics[k] for k in ("base", "archive", "deals", "chat", "leads"))
