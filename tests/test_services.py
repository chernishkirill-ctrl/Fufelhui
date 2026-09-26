"""Бизнес-логика: объекты, история, заявки (гонки), сделки, отчеты, статистика, права."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from bot.database.models import (
    DealStatus,
    DealType,
    LeadStatus,
    PropertyStatus,
    Role,
    User,
    UserStatus,
)
from bot.database.repositories import settings as settings_repo
from bot.services import (
    auth,
    deal_service,
    lead_service,
    property_service,
    report_service,
    stats_service,
    user_service,
)
from bot.utils.timeutils import local_today, make_period

TZ = ZoneInfo("Europe/Kyiv")


async def make_user(session, tg_id: int, role=Role.REALTOR, username: str | None = None) -> User:
    user = User(telegram_id=tg_id, role=role, username=username or f"r{tg_id}", first_name=f"R{tg_id}", status=UserStatus.ACTIVE)
    session.add(user)
    await session.flush()
    return user


async def make_property(session, creator: User | None = None, **fields):
    data = {"offer_type": "sale", "property_type": "Квартира", "rooms": 1, "area": Decimal("42"), "price": Decimal("45000"),
            "currency": "USD", "city": "Днепр", "district": "Слобожанский", "address": "ул. Секретная, 1",
            "owner_phone": "+380671112233", "owner_name": "Иван"}
    data.update(fields)
    return await property_service.create_property(
        session, data, created_by_id=creator.id if creator else None, responsible_id=creator.id if creator else None
    )


async def test_property_code_and_history(session):
    realtor = await make_user(session, 1)
    prop = await make_property(session, realtor)
    assert prop.code == f"OBJ-{prop.id:06d}"
    assert property_service.parse_property_code("OBJ-000125") == 125
    assert property_service.parse_property_code("№ 7") == 7
    assert property_service.parse_property_code("abc") is None

    await property_service.update_field(session, prop, "price", "50 000", realtor.id)
    assert prop.price == Decimal("50000")
    await property_service.update_field(session, prop, "owner_phone", "+380500000000", realtor.id)
    await property_service.change_status(session, prop, PropertyStatus.ARCHIVED, realtor.id)
    assert prop.is_archived and prop.archived_at is not None
    await session.flush()
    history = await property_service.get_history(session, prop.id)
    actions = [h.action for h in history]
    assert actions[:2] == ["created", "assigned"]
    assert "archived" in actions
    phone_rows = [h for h in history if h.field == "owner_phone"]
    assert phone_rows and "+380" not in (phone_rows[0].new_value or "")  # приватное поле не пишется открытым текстом


async def test_update_field_validation(session):
    prop = await make_property(session)
    with pytest.raises(property_service.PropertyError):
        await property_service.update_field(session, prop, "price", "дорого", None)
    with pytest.raises(property_service.PropertyError):
        await property_service.update_field(session, prop, "responsible_realtor_id", "5", None)
    await property_service.update_field(session, prop, "coords", "48.46, 35.04", None)
    assert prop.latitude == pytest.approx(48.46)


async def test_list_and_search(session):
    r1 = await make_user(session, 1)
    r2 = await make_user(session, 2)
    p1 = await make_property(session, r1, district="Соборный")
    await make_property(session, r2, district="Победа")
    items, total = await property_service.list_properties(session, scope="active", responsible_id=r1.id)
    assert total == 1 and items[0].id == p1.id
    items, total = await property_service.list_properties(session, scope="all", query="Собор")
    assert total == 1
    items, total = await property_service.list_properties(session, scope="all", query=p1.code)
    assert items[0].id == p1.id


async def test_delete_blocked_by_deal(session):
    realtor = await make_user(session, 1)
    prop = await make_property(session, realtor)
    await deal_service.create_deal(
        session, prop=prop, realtor_id=realtor.id, deal_type=DealType.OTHER, amount=Decimal(1), commission=Decimal(1),
        currency="USD", deal_date=date.today(), comment=None, lead_id=None, created_by_id=realtor.id,
    )
    with pytest.raises(property_service.PropertyError):
        await property_service.delete_property(session, prop)


async def test_permissions(session):
    owner = await make_user(session, 1000, role=Role.OWNER)
    r1 = await make_user(session, 1)
    r2 = await make_user(session, 2)
    prop = await make_property(session, r1)
    a_owner = auth.Actor(1000, owner, True)
    a1 = auth.Actor(1, r1, False)
    a2 = auth.Actor(2, r2, False)
    stranger = auth.Actor(5, None, False)
    assert auth.can_edit_property(a_owner, prop)
    assert auth.can_edit_property(a1, prop)
    assert not auth.can_edit_property(a2, prop)
    assert auth.can_view_property(a2, prop)
    assert not auth.can_view_property(stranger, prop)
    assert not auth.can_delete_property(a1)
    assert not auth.can_publish_property(a1, prop, realtor_can_publish=False)
    r1.status = UserStatus.BLOCKED
    assert not a1.is_staff and not auth.can_edit_property(a1, prop)

    lead = await lead_service.create_lead(session, property_id=prop.id, client_telegram_id=77, client_username="c",
                                          client_name="Клиент", phone="+380", preferred_time=None, comment=None)
    r1.status = UserStatus.ACTIVE
    assert not auth.can_view_lead_private(a1, lead)
    result = await lead_service.take_lead(session, lead.id, r1)
    assert result.ok
    assert auth.can_view_lead_private(a1, lead)
    assert not auth.can_view_lead_private(a2, lead)
    assert auth.can_view_lead_private(a_owner, lead)


async def test_take_lead_once(session):
    r1 = await make_user(session, 1)
    r2 = await make_user(session, 2)
    prop = await make_property(session, r1)
    lead = await lead_service.create_lead(session, property_id=prop.id, client_telegram_id=5, client_username=None,
                                          client_name="A", phone=None, preferred_time=None, comment=None)
    first = await lead_service.take_lead(session, lead.id, r2)
    second = await lead_service.take_lead(session, lead.id, r1)
    assert first.ok and not second.ok and second.reason == "taken"
    assert lead.assigned_realtor_id == r2.id and lead.status == LeadStatus.ASSIGNED
    missing = await lead_service.take_lead(session, 999999, r1)
    assert missing.reason == "not_found"


async def test_take_lead_race_concurrent_sessions(session_factory):
    """Много риелторов одновременно жмут «Взять клиента» в отдельных транзакциях — победитель ровно один."""
    async with session_factory() as s:
        realtors = [await make_user(s, 100 + i) for i in range(8)]
        prop = await make_property(s, realtors[0])
        lead = await lead_service.create_lead(s, property_id=prop.id, client_telegram_id=5, client_username=None,
                                              client_name="A", phone=None, preferred_time=None, comment=None)
        await s.commit()
        realtor_ids = [r.id for r in realtors]
        lead_id = lead.id

    async def attempt(rid: int) -> bool:
        async with session_factory() as s:
            user = await s.get(User, rid)
            res = await lead_service.take_lead(s, lead_id, user)
            await s.commit()
            return res.ok

    results = await asyncio.gather(*(attempt(rid) for rid in realtor_ids))
    assert sum(results) == 1
    async with session_factory() as s:
        lead = await lead_service.get_lead(s, lead_id)
        assert lead.assigned_realtor_id == realtor_ids[results.index(True)]


async def test_lead_antispam(session):
    prop = await make_property(session)
    kwargs = dict(property_id=prop.id, client_telegram_id=9, client_username=None, client_name="A", phone=None,
                  preferred_time=None, comment=None)
    await lead_service.create_lead(session, **kwargs)
    with pytest.raises(lead_service.LeadError):
        await lead_service.create_lead(session, **kwargs)


async def test_deal_flow_and_cancel(session):
    realtor = await make_user(session, 1)
    prop = await make_property(session, realtor)
    lead = await lead_service.create_lead(session, property_id=prop.id, client_telegram_id=5, client_username=None,
                                          client_name="A", phone=None, preferred_time=None, comment=None)
    await lead_service.take_lead(session, lead.id, realtor)
    deal, old = await deal_service.create_deal(
        session, prop=prop, realtor_id=realtor.id, deal_type=DealType.SALE, amount=Decimal("45000"),
        commission=Decimal("1500"), currency="USD", deal_date=local_today(TZ), comment="ok", lead_id=lead.id,
        created_by_id=realtor.id,
    )
    assert old == PropertyStatus.ACTIVE and prop.status == PropertyStatus.SOLD and prop.is_archived
    await session.refresh(lead)
    assert lead.status == LeadStatus.CLOSED
    stats = await stats_service.collect(session, make_period("today", TZ), TZ)
    assert stats.deals == 1 and stats.commission == {"USD": Decimal("1500")}
    assert stats.archived_properties == 1
    mine = await stats_service.collect(session, make_period("month", TZ), TZ, realtor_id=realtor.id)
    assert mine.deals == 1 and mine.leads_taken == 1
    rows = await stats_service.per_realtor(session, make_period("all", TZ))
    assert rows[0].realtor.id == realtor.id and rows[0].commission["USD"] == Decimal("1500")

    await deal_service.cancel_deal(session, deal, None, restore_property=True)
    assert deal.status == DealStatus.CANCELLED and prop.status == PropertyStatus.ACTIVE
    await session.flush()
    stats = await stats_service.collect(session, make_period("today", TZ), TZ)
    assert stats.deals == 0 and stats.commission == {}
    with pytest.raises(deal_service.DealError):
        await deal_service.cancel_deal(session, deal, None, restore_property=False)


async def test_reports_upsert_and_missing(session):
    r1 = await make_user(session, 1)
    r2 = await make_user(session, 2)
    day = date(2026, 9, 25)
    await report_service.save_report(session, r1.id, day, {"calls": 5}, "ok")
    await report_service.save_report(session, r1.id, day, {"calls": 7}, "upd")
    statuses = await report_service.reports_for_date(session, day)
    by_id = {s.realtor.id: s for s in statuses}
    assert by_id[r1.id].report.calls == 7 and by_id[r1.id].report.report_text == "upd"
    assert by_id[r2.id].report is None


async def test_claim_once(session):
    assert await settings_repo.claim_once(session, "k", "2026-09-26")
    assert not await settings_repo.claim_once(session, "k", "2026-09-26")
    assert await settings_repo.claim_once(session, "k", "2026-09-27")


async def test_invites_single_use(session):
    invite = await user_service.create_invite(session, None)
    user = await user_service.accept_invite(session, invite.token, 555, "new", "New", owner_telegram_id=1000)
    assert user.role == Role.REALTOR and user.status == UserStatus.ACTIVE
    with pytest.raises(user_service.UserError):
        await user_service.accept_invite(session, invite.token, 556, "x", "X", owner_telegram_id=1000)
    with pytest.raises(user_service.UserError):
        await user_service.add_realtor(session, 1000, owner_telegram_id=1000)


async def test_new_properties_period(session):
    r = await make_user(session, 1)
    await make_property(session, r)
    stats = await stats_service.collect(session, make_period("today", TZ), TZ)
    assert stats.new_properties == 1 and stats.active_properties == 1 and stats.realtors == 1
    yesterday = make_period("yesterday", TZ)
    assert (await stats_service.collect(session, yesterday, TZ)).new_properties == 0
    assert yesterday.start == local_today(TZ) - timedelta(days=1)
