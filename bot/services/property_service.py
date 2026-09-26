"""Бизнес-логика объектов: создание, изменение, статусы, история, поиск."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import (
    ACTIVE_STATUSES,
    ARCHIVE_STATUSES,
    Deal,
    DealStatus,
    Lead,
    OfferType,
    Property,
    PropertyHistory,
    PropertyStatus,
    User,
    utcnow,
)
from bot.utils.text import detect_currency, parse_decimal, parse_int

logger = logging.getLogger(__name__)

PAGE_SIZE = 8

STATUS_LABELS = {
    PropertyStatus.DRAFT: "📝 Черновик",
    PropertyStatus.ACTIVE: "🟢 Активен",
    PropertyStatus.PUBLISHED: "📢 Опубликован",
    PropertyStatus.RESERVED: "⏳ Резерв",
    PropertyStatus.SOLD: "💰 Продан",
    PropertyStatus.RENTED: "🔑 Сдан",
    PropertyStatus.INACTIVE: "⚪ Неактуален",
    PropertyStatus.ARCHIVED: "📦 В архиве",
}

OFFER_LABELS = {OfferType.SALE: "продажа", OfferType.RENT: "аренда"}

PROPERTY_TYPES = ["Квартира", "Дом", "Комната", "Коммерция", "Участок", "Другое"]


class PropertyError(Exception):
    pass


def parse_property_code(raw: str) -> int | None:
    """«OBJ-000125», «obj125», «№125», «125» -> 125."""
    m = re.fullmatch(r"\s*(?:obj[-\s]?|№\s*|#)?0*(\d{1,9})\s*", raw, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ---------- редактируемые поля ----------

def _parse_text(limit: int) -> Callable[[str], Any]:
    def parser(raw: str) -> str | None:
        raw = raw.strip()
        if raw in {"-", "—"}:
            return None
        if len(raw) > limit:
            raise PropertyError(f"Слишком длинно (максимум {limit} символов)")
        return raw

    return parser


def _parse_positive_decimal(raw: str) -> Decimal | None:
    if raw.strip() in {"-", "—"}:
        return None
    value = parse_decimal(raw)
    if value is None or value < 0:
        raise PropertyError("Введите число, например 45000")
    return value


def _parse_small_int(raw: str) -> int | None:
    if raw.strip() in {"-", "—"}:
        return None
    value = parse_int(raw)
    if value is None or not (0 <= value <= 200):
        raise PropertyError("Введите целое число")
    return value


def _parse_currency(raw: str) -> str:
    code = raw.strip().upper()
    if code in {"USD", "EUR", "UAH"}:
        return code
    detected = detect_currency(raw, "")
    if not detected:
        raise PropertyError("Валюта: USD, EUR или UAH")
    return detected


def _parse_offer(raw: str) -> OfferType:
    low = raw.strip().lower()
    if low in {"sale", "продажа", "продаж", "продаю"}:
        return OfferType.SALE
    if low in {"rent", "аренда", "оренда"}:
        return OfferType.RENT
    raise PropertyError("Укажите «продажа» или «аренда»")


def _parse_bool(raw: str) -> bool:
    low = raw.strip().lower()
    if low in {"да", "yes", "1", "так", "true", "+"}:
        return True
    if low in {"нет", "no", "0", "ні", "false", "-"}:
        return False
    raise PropertyError("Ответьте «да» или «нет»")


@dataclass(frozen=True)
class EditableField:
    label: str
    parser: Callable[[str], Any]
    hint: str


EDITABLE_FIELDS: dict[str, EditableField] = {
    "title": EditableField("Заголовок", _parse_text(256), "Короткий заголовок объекта"),
    "offer_type": EditableField("Тип предложения", _parse_offer, "продажа / аренда"),
    "property_type": EditableField("Тип недвижимости", _parse_text(64), "Квартира, Дом, Комната…"),
    "price": EditableField("Цена", _parse_positive_decimal, "Число, например 45000"),
    "currency": EditableField("Валюта", _parse_currency, "USD / EUR / UAH"),
    "rooms": EditableField("Комнат", _parse_small_int, "Число"),
    "area": EditableField("Площадь, м²", _parse_positive_decimal, "Число, например 42.5"),
    "floor": EditableField("Этаж", _parse_small_int, "Число"),
    "floors_total": EditableField("Этажность", _parse_small_int, "Число"),
    "city": EditableField("Город", _parse_text(128), "Например: Днепр"),
    "district": EditableField("Район", _parse_text(128), "Например: Соборный"),
    "address": EditableField("Адрес", _parse_text(256), "Улица, дом (не публикуется, если скрыт)"),
    "coords": EditableField("Координаты", lambda raw: raw, "«48.4647, 35.0462» или «-» чтобы очистить"),
    "show_exact_location": EditableField("Точная точка на карте", _parse_bool, "да / нет"),
    "description": EditableField("Описание", _parse_text(4000), "Текст для публикации"),
    "owner_name": EditableField("Имя собственника 🔒", _parse_text(128), "Не публикуется"),
    "owner_phone": EditableField("Телефон собственника 🔒", _parse_text(64), "Не публикуется"),
    "internal_comment": EditableField("Внутренний комментарий 🔒", _parse_text(2000), "Не публикуется"),
}


def _history_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


async def add_history(
    session: AsyncSession,
    property_id: int,
    action: str,
    user_id: int | None,
    field: str | None = None,
    old: Any = None,
    new: Any = None,
) -> None:
    session.add(
        PropertyHistory(
            property_id=property_id,
            action=action,
            field=field,
            old_value=_history_value(old),
            new_value=_history_value(new),
            user_id=user_id,
        )
    )


# ---------- CRUD ----------

PROPERTY_INPUT_FIELDS = {
    "external_id", "source_url", "source_name", "offer_type", "property_type", "title", "description", "price",
    "currency", "city", "address", "district", "latitude", "longitude", "rooms", "area", "floor", "floors_total",
    "features", "photos", "owner_name", "owner_phone", "internal_comment", "show_exact_location",
}


async def create_property(
    session: AsyncSession,
    data: dict[str, Any],
    *,
    created_by_id: int | None,
    responsible_id: int | None,
    status: PropertyStatus = PropertyStatus.ACTIVE,
) -> Property:
    values = {k: v for k, v in data.items() if k in PROPERTY_INPUT_FIELDS and v not in (None, "", [])}
    if isinstance(values.get("offer_type"), str):
        values["offer_type"] = OfferType(values["offer_type"])
    prop = Property(**values, status=status, created_by_id=created_by_id, responsible_realtor_id=responsible_id)
    session.add(prop)
    await session.flush()
    source = data.get("source_name") or ("импорт" if data.get("source_url") else "вручную")
    await add_history(session, prop.id, "created", created_by_id, "source", None, source)
    if responsible_id:
        await add_history(session, prop.id, "assigned", created_by_id, "responsible_realtor_id", None, responsible_id)
    await session.refresh(prop)
    logger.info("Создан объект %s (источник: %s)", prop.code, source)
    return prop


async def get_property(session: AsyncSession, property_id: int) -> Property | None:
    return await session.get(Property, property_id)


async def update_field(session: AsyncSession, prop: Property, field: str, raw: str, user_id: int | None) -> None:
    spec = EDITABLE_FIELDS.get(field)
    if spec is None:
        raise PropertyError("Это поле нельзя изменить")
    if field == "coords":
        if raw.strip() in {"-", "—"}:
            lat = lon = None
        else:
            m = re.fullmatch(r"\s*(-?\d{1,3}(?:[.,]\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:[.,]\d+)?)\s*", raw)
            if not m:
                raise PropertyError("Формат: 48.4647, 35.0462")
            lat, lon = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise PropertyError("Координаты вне диапазона")
        old = f"{prop.latitude}, {prop.longitude}" if prop.latitude is not None else None
        prop.latitude, prop.longitude = lat, lon
        await add_history(session, prop.id, "updated", user_id, "coords", old, f"{lat}, {lon}" if lat is not None else None)
        return
    value = spec.parser(raw)
    old = getattr(prop, field)
    setattr(prop, field, value)
    # Приватные поля не пишем в историю открытым текстом
    private = field in {"owner_phone", "owner_name"}
    await add_history(
        session, prop.id, "updated", user_id, field,
        "•••" if private and old else old, "•••" if private and value else value,
    )


async def set_photos(session: AsyncSession, prop: Property, photos: list[str], user_id: int | None) -> None:
    old_count = len(prop.photos or [])
    prop.photos = photos[:30]
    await add_history(session, prop.id, "updated", user_id, "photos", old_count, len(prop.photos))


async def change_status(session: AsyncSession, prop: Property, status: PropertyStatus, user_id: int | None) -> PropertyStatus:
    old = prop.status
    if old == status:
        return old
    prop.status = status
    if status in ARCHIVE_STATUSES and old not in ARCHIVE_STATUSES:
        prop.archived_at = utcnow()
        await add_history(session, prop.id, "archived", user_id, "status", old, status)
    elif status not in ARCHIVE_STATUSES and old in ARCHIVE_STATUSES:
        prop.archived_at = None
        await add_history(session, prop.id, "unarchived", user_id, "status", old, status)
    else:
        await add_history(session, prop.id, "status", user_id, "status", old, status)
    if status == PropertyStatus.PUBLISHED and prop.published_at is None:
        prop.published_at = utcnow()
    logger.info("Объект %s: статус %s -> %s", prop.code, old.value, status.value)
    return old


async def assign_realtor(session: AsyncSession, prop: Property, realtor: User | None, user_id: int | None) -> None:
    old = prop.responsible_realtor_id
    new = realtor.id if realtor else None
    if old == new:
        return
    prop.responsible_realtor_id = new
    await add_history(session, prop.id, "assigned", user_id, "responsible_realtor_id", old, new)
    await session.flush()
    await session.refresh(prop, ["responsible"])


async def delete_property(session: AsyncSession, prop: Property) -> None:
    deals = await session.scalar(select(func.count(Deal.id)).where(Deal.property_id == prop.id))
    if deals:
        raise PropertyError("По объекту есть сделки — удаление запрещено, используйте архив.")
    logger.info("Удален объект %s", prop.code)
    await session.delete(prop)
    await session.flush()


# ---------- выборки ----------

SCOPES = {
    "active": ACTIVE_STATUSES,
    "published": frozenset({PropertyStatus.PUBLISHED}),
    "archive": ARCHIVE_STATUSES,
    "all": frozenset(PropertyStatus),
}


async def list_properties(
    session: AsyncSession,
    *,
    scope: str = "active",
    responsible_id: int | None = None,
    page: int = 0,
    query: str | None = None,
) -> tuple[list[Property], int]:
    statuses = SCOPES.get(scope, ACTIVE_STATUSES)
    conditions = [Property.status.in_(statuses)]
    if responsible_id is not None:
        conditions.append(Property.responsible_realtor_id == responsible_id)
    if query:
        code = parse_property_code(query)
        # ILIKE в PostgreSQL регистронезависим и для кириллицы
        like = f"%{query.strip()}%"
        text_cond = or_(
            Property.title.ilike(like),
            Property.address.ilike(like),
            Property.district.ilike(like),
            Property.city.ilike(like),
            Property.description.ilike(like),
            Property.source_url.ilike(like),
            Property.external_id.ilike(like),
            cast(Property.id, String).like(like),
        )
        conditions.append(or_(Property.id == code, text_cond) if code else text_cond)
    total = int(await session.scalar(select(func.count(Property.id)).where(*conditions)) or 0)
    rows = (
        await session.scalars(
            select(Property).where(*conditions).order_by(Property.id.desc()).limit(PAGE_SIZE).offset(page * PAGE_SIZE)
        )
    ).unique().all()
    return list(rows), total


async def get_history(session: AsyncSession, property_id: int, limit: int = 40) -> list[PropertyHistory]:
    rows = await session.scalars(
        select(PropertyHistory)
        .where(PropertyHistory.property_id == property_id)
        .order_by(PropertyHistory.created_at.desc(), PropertyHistory.id.desc())
        .limit(limit)
    )
    return list(reversed(rows.unique().all()))


@dataclass
class PropertySummary:
    leads_total: int
    leads_taken: int
    viewings: int
    deals: list[Deal]


async def get_summary(session: AsyncSession, property_id: int) -> PropertySummary:
    leads_total = int(await session.scalar(select(func.count(Lead.id)).where(Lead.property_id == property_id)) or 0)
    leads_taken = int(
        await session.scalar(
            select(func.count(Lead.id)).where(Lead.property_id == property_id, Lead.assigned_realtor_id.is_not(None))
        )
        or 0
    )
    viewings = int(
        await session.scalar(
            select(func.count(Lead.id)).where(Lead.property_id == property_id, Lead.viewing_done_at.is_not(None))
        )
        or 0
    )
    deals = (
        await session.scalars(
            select(Deal).where(Deal.property_id == property_id, Deal.status == DealStatus.CONFIRMED).order_by(Deal.id)
        )
    ).unique().all()
    return PropertySummary(leads_total, leads_taken, viewings, list(deals))
