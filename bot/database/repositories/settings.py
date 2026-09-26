from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import AppSetting, utcnow


async def get_value(session: AsyncSession, key: str) -> str | None:
    row = await session.get(AppSetting, key)
    return row.value if row else None


async def get_all(session: AsyncSession) -> dict[str, str | None]:
    rows = (await session.scalars(select(AppSetting))).all()
    return {r.key: r.value for r in rows}


async def set_value(session: AsyncSession, key: str, value: str | None) -> None:
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await session.flush()


async def claim_once(session: AsyncSession, key: str, marker: str) -> bool:
    """Атомарно «захватить» маркер (например, дату рассылки). True — если захватили мы.

    Используется, чтобы ежедневная рассылка не ушла дважды (несколько инстансов, cron + scheduler).
    """
    if await session.get(AppSetting, key) is None:
        try:
            async with session.begin_nested():
                session.add(AppSetting(key=key, value=None))
        except IntegrityError:
            pass  # гонка вставки: строку уже создал другой процесс
    result = await session.execute(
        update(AppSetting)
        .where(AppSetting.key == key)
        .where((AppSetting.value.is_(None)) | (AppSetting.value != marker))
        .values(value=marker, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    return result.rowcount == 1
