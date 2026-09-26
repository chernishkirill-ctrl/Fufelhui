"""ORM-модели CRM. Все даты хранятся в UTC (naive), отображаются в часовом поясе агентства."""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    # native_enum=False -> VARCHAR + CHECK: проще мигрировать при добавлении новых значений
    return Enum(
        cls,
        name=name,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


class Role(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    REALTOR = "realtor"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    REMOVED = "removed"


class PropertyStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PUBLISHED = "published"
    RESERVED = "reserved"
    SOLD = "sold"
    RENTED = "rented"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


# Статусы, при которых объект считается «в архиве»
ARCHIVE_STATUSES = frozenset({PropertyStatus.SOLD, PropertyStatus.RENTED, PropertyStatus.INACTIVE, PropertyStatus.ARCHIVED})
# Статусы «в работе»
ACTIVE_STATUSES = frozenset({PropertyStatus.DRAFT, PropertyStatus.ACTIVE, PropertyStatus.PUBLISHED, PropertyStatus.RESERVED})


class OfferType(str, enum.Enum):
    SALE = "sale"
    RENT = "rent"


class LeadStatus(str, enum.Enum):
    NEW = "new"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    VIEWING_SCHEDULED = "viewing_scheduled"
    VIEWING_DONE = "viewing_done"
    CLOSED = "closed"
    CANCELLED = "cancelled"


OPEN_LEAD_STATUSES = frozenset(
    {LeadStatus.NEW, LeadStatus.ASSIGNED, LeadStatus.IN_PROGRESS, LeadStatus.VIEWING_SCHEDULED, LeadStatus.VIEWING_DONE}
)


class DealType(str, enum.Enum):
    SALE = "sale"
    RENT = "rent"
    OTHER = "other"


class DealStatus(str, enum.Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"), default=Role.REALTOR)
    status: Mapped[UserStatus] = mapped_column(_enum(UserStatus, "user_status"), default=UserStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE

    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.first_name or f"id{self.telegram_id}"


class Invite(Base):
    """Одноразовые приглашения риелторов (только роль realtor)."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    used_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(String(64))
    offer_type: Mapped[OfferType] = mapped_column(_enum(OfferType, "offer_type"), default=OfferType.SALE)
    property_type: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    city: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(String(256))
    district: Mapped[str | None] = mapped_column(String(128))
    latitude: Mapped[float | None] = mapped_column()
    longitude: Mapped[float | None] = mapped_column()
    show_exact_location: Mapped[bool] = mapped_column(Boolean, default=False)
    rooms: Mapped[int | None] = mapped_column(Integer)
    area: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    floor: Mapped[int | None] = mapped_column(Integer)
    floors_total: Mapped[int | None] = mapped_column(Integer)
    features: Mapped[dict | None] = mapped_column(JSON)
    photos: Mapped[list | None] = mapped_column(JSON)

    # Приватные данные: никогда не публикуются
    owner_name: Mapped[str | None] = mapped_column(String(128))
    owner_phone: Mapped[str | None] = mapped_column(String(64))
    internal_comment: Mapped[str | None] = mapped_column(Text)

    status: Mapped[PropertyStatus] = mapped_column(
        _enum(PropertyStatus, "property_status"), default=PropertyStatus.ACTIVE, index=True
    )
    responsible_realtor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    telegraph_url: Mapped[str | None] = mapped_column(Text)
    public_message_id: Mapped[int | None] = mapped_column(BigInteger)
    # ID сообщений альбома (если публикация — альбом из нескольких фото)
    public_extra_ids: Mapped[list | None] = mapped_column(JSON)
    base_message_id: Mapped[int | None] = mapped_column(BigInteger)
    archive_message_id: Mapped[int | None] = mapped_column(BigInteger)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    responsible: Mapped[User | None] = relationship(foreign_keys=[responsible_realtor_id], lazy="joined")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id], lazy="joined")

    @property
    def code(self) -> str:
        return format_property_code(self.id)

    @property
    def is_archived(self) -> bool:
        return self.status in ARCHIVE_STATUSES


def format_property_code(property_id: int) -> str:
    return f"OBJ-{property_id:06d}"


class PropertyHistory(Base):
    """Журнал изменений объекта. Также сюда пишутся события заявок и сделок по объекту."""

    __tablename__ = "property_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    field: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    user: Mapped[User | None] = relationship(lazy="joined")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id", ondelete="SET NULL"), index=True)
    client_telegram_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    client_username: Mapped[str | None] = mapped_column(String(64))
    client_name: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(64))
    preferred_time: Mapped[str | None] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(Text)
    internal_comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[LeadStatus] = mapped_column(_enum(LeadStatus, "lead_status"), default=LeadStatus.NEW, index=True)
    assigned_realtor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    work_message_id: Mapped[int | None] = mapped_column(BigInteger)
    viewing_at: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime)
    viewing_done_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    prop: Mapped[Property | None] = relationship(lazy="joined")
    assigned_realtor: Mapped[User | None] = relationship(lazy="joined")

    @property
    def code(self) -> str:
        return f"L-{self.id:05d}"


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id", ondelete="SET NULL"), index=True)
    realtor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # client_id -> заявка клиента (клиенты не являются пользователями CRM)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    deal_type: Mapped[DealType] = mapped_column(_enum(DealType, "deal_type"))
    deal_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    commission: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    deal_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[DealStatus] = mapped_column(_enum(DealStatus, "deal_status"), default=DealStatus.CONFIRMED, index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    deals_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    prop: Mapped[Property | None] = relationship(lazy="joined")
    realtor: Mapped[User | None] = relationship(foreign_keys=[realtor_id], lazy="joined")
    lead: Mapped[Lead | None] = relationship(foreign_keys=[client_id], lazy="joined")

    @property
    def code(self) -> str:
        return f"D-{self.id:05d}"


class DailyReport(Base):
    __tablename__ = "daily_reports"
    __table_args__ = (UniqueConstraint("realtor_id", "report_date", name="uq_daily_report_realtor_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    realtor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    new_clients: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    leads_processed: Mapped[int] = mapped_column(Integer, default=0)
    viewings: Mapped[int] = mapped_column(Integer, default=0)
    new_properties: Mapped[int] = mapped_column(Integer, default=0)
    deals_closed: Mapped[int] = mapped_column(Integer, default=0)
    report_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    realtor: Mapped[User] = relationship(lazy="joined")


class AppSetting(Base):
    """Настройки, изменяемые из бота (переключатели, ID тем, созданных ботом, токен Telegraph)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


Index("ix_deals_realtor_date", Deal.realtor_id, Deal.deal_date)
