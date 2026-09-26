"""Роли и права доступа. Все проверки выполняются на сервере по telegram_id — никогда по username."""
from __future__ import annotations

from dataclasses import dataclass

from bot.database.models import Deal, Lead, Property, Role, User, UserStatus


@dataclass
class Actor:
    telegram_id: int
    user: User | None
    is_owner: bool

    @property
    def is_staff(self) -> bool:
        """Владелец или активный риелтор."""
        if self.is_owner:
            return True
        return self.user is not None and self.user.status == UserStatus.ACTIVE and self.user.role in (Role.REALTOR, Role.ADMIN)

    @property
    def is_realtor(self) -> bool:
        return self.is_staff and not self.is_owner

    @property
    def user_id(self) -> int | None:
        return self.user.id if self.user else None

    @property
    def role_name(self) -> str:
        if self.is_owner:
            return "owner"
        if self.is_staff:
            return "realtor"
        return "guest"


def can_view_property(actor: Actor, prop: Property) -> bool:
    # Общая база объектов доступна всем сотрудникам
    return actor.is_staff


def can_edit_property(actor: Actor, prop: Property) -> bool:
    if actor.is_owner:
        return True
    return actor.is_staff and prop.responsible_realtor_id is not None and prop.responsible_realtor_id == actor.user_id


def can_publish_property(actor: Actor, prop: Property, realtor_can_publish: bool) -> bool:
    if actor.is_owner:
        return True
    return realtor_can_publish and can_edit_property(actor, prop)


def can_delete_property(actor: Actor) -> bool:
    return actor.is_owner


def can_assign_property(actor: Actor) -> bool:
    return actor.is_owner


def can_view_lead_private(actor: Actor, lead: Lead) -> bool:
    """Контакты клиента видят только владелец и риелтор, забравший заявку."""
    if actor.is_owner:
        return True
    return actor.is_staff and lead.assigned_realtor_id is not None and lead.assigned_realtor_id == actor.user_id


def can_manage_lead(actor: Actor, lead: Lead) -> bool:
    return can_view_lead_private(actor, lead)


def can_view_deal(actor: Actor, deal: Deal) -> bool:
    if actor.is_owner:
        return True
    return actor.is_staff and deal.realtor_id == actor.user_id


def can_edit_deal(actor: Actor) -> bool:
    return actor.is_owner
