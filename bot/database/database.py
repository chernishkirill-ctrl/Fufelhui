"""Подключение к БД. PostgreSQL в production, SQLite допускается для локальной разработки и тестов."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)


def create_engine(url: str) -> AsyncEngine:
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs.update(pool_size=5, max_overflow=5, pool_recycle=1800)
    return create_async_engine(url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def wait_for_database(engine: AsyncEngine, attempts: int = 10, delay: float = 3.0) -> None:
    """Ждём доступности БД при старте, вместо того чтобы падать сразу."""
    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("База данных доступна")
            return
        except Exception as exc:  # noqa: BLE001 - драйверы бросают разные типы
            logger.warning("БД недоступна (попытка %s/%s): %s", attempt, attempts, type(exc).__name__)
            if attempt == attempts:
                raise
            await asyncio.sleep(delay * attempt)
