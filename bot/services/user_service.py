"""Управление сотрудниками: добавление по Telegram ID, одноразовые приглашения, блокировка."""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Invite, Role, User, UserStatus, utcnow
from bot.database.repositories import users as users_repo

logger = logging.getLogger(__name__)

INVITE_TTL = timedelta(days=2)


class UserError(Exception):
    pass


async def add_realtor(session: AsyncSession, telegram_id: int, owner_telegram_id: int) -> tuple[User, bool]:
    """Добавляет (или восстанавливает) риелтора. Возвращает (user, created)."""
    if telegram_id == owner_telegram_id:
        raise UserError("Это ID владельца.")
    if telegram_id <= 0:
        raise UserError("Telegram ID пользователя — положительное число.")
    user = await users_repo.get_by_telegram_id(session, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id, role=Role.REALTOR, status=UserStatus.ACTIVE)
        session.add(user)
        await session.flush()
        logger.info("Добавлен риелтор user_id=%s", user.id)
        return user, True
    user.role = Role.REALTOR if user.role != Role.ADMIN else user.role
    user.status = UserStatus.ACTIVE
    logger.info("Риелтор user_id=%s восстановлен", user.id)
    return user, False


async def create_invite(session: AsyncSession, created_by_id: int | None) -> Invite:
    invite = Invite(token=secrets.token_urlsafe(16), created_by_id=created_by_id, expires_at=utcnow() + INVITE_TTL)
    session.add(invite)
    await session.flush()
    return invite


async def accept_invite(session: AsyncSession, token: str, telegram_id: int, username: str | None, first_name: str | None, owner_telegram_id: int) -> User:
    """Приглашение одноразовое: атомарно помечаем использованным, затем создаем/активируем риелтора."""
    now = utcnow()
    result = await session.execute(
        update(Invite)
        .where(Invite.token == token, Invite.used_at.is_(None), Invite.expires_at > now)
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise UserError("Приглашение недействительно или уже использовано. Попросите владельца выдать новое.")
    existing = await users_repo.get_by_telegram_id(session, telegram_id)
    if existing is not None and existing.status == UserStatus.BLOCKED:
        raise UserError("Ваш доступ заблокирован владельцем.")
    user, _ = await add_realtor(session, telegram_id, owner_telegram_id)
    user.username, user.first_name = username, first_name
    await session.execute(
        update(Invite).where(Invite.token == token).values(used_by_id=user.id).execution_options(synchronize_session=False)
    )
    logger.info("Приглашение использовано, риелтор user_id=%s", user.id)
    return user


async def set_status(session: AsyncSession, user: User, status: UserStatus) -> None:
    if user.role == Role.OWNER:
        raise UserError("Нельзя изменить статус владельца.")
    user.status = status
    logger.info("Сотрудник user_id=%s: статус %s", user.id, status.value)
