from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Role, User, UserStatus


async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def get(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def list_staff(session: AsyncSession, *, include_inactive: bool = False, include_owner: bool = False) -> list[User]:
    roles = [Role.REALTOR, Role.ADMIN] + ([Role.OWNER] if include_owner else [])
    stmt = select(User).where(User.role.in_(roles))
    if not include_inactive:
        stmt = stmt.where(User.status == UserStatus.ACTIVE)
    else:
        stmt = stmt.where(User.status != UserStatus.REMOVED)
    stmt = stmt.order_by(User.status, User.first_name, User.id)
    return list((await session.scalars(stmt)).all())


async def count_realtors(session: AsyncSession, *, active_only: bool = True) -> int:
    stmt = select(func.count(User.id)).where(User.role.in_([Role.REALTOR, Role.ADMIN]))
    if active_only:
        stmt = stmt.where(User.status == UserStatus.ACTIVE)
    else:
        stmt = stmt.where(User.status != UserStatus.REMOVED)
    return int(await session.scalar(stmt) or 0)


async def ensure_owner(session: AsyncSession, telegram_id: int, username: str | None, first_name: str | None) -> User:
    """Владелец определяется только по OWNER_ID из окружения; запись в users нужна для связей (created_by и т.п.)."""
    user = await get_by_telegram_id(session, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name, role=Role.OWNER, status=UserStatus.ACTIVE)
        session.add(user)
        await session.flush()
    else:
        user.role = Role.OWNER
        user.status = UserStatus.ACTIVE
    return user


def touch_profile(user: User, username: str | None, first_name: str | None) -> None:
    """Обновляем username/имя для отображения. Они НИКОГДА не используются для проверки прав."""
    if username != user.username:
        user.username = username
    if first_name and first_name != user.first_name:
        user.first_name = first_name
