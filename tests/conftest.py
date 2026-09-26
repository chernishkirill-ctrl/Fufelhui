"""Общие фикстуры: БД (SQLite; PostgreSQL если задан TEST_DATABASE_URL), мок Telegram Bot API, фабрика апдейтов."""
from __future__ import annotations

import itertools
import os
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    ForumTopic,
    Message,
    Update,
    User as TgUser,
)
from sqlalchemy import text

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("OWNER_ID", "1000")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from bot.config import Config  # noqa: E402
from bot.database.database import create_engine, create_session_factory  # noqa: E402
from bot.database.models import Base  # noqa: E402
from bot.services.context import AppContext  # noqa: E402

OWNER_ID = 1000
WORK_CHAT = -1001234567890
CHANNEL = -1009876543210


class MockSession(BaseSession):
    """Записывает все вызовы Bot API и возвращает правдоподобные ответы."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._ids = itertools.count(100)
        self.fail_methods: set[str] = set()

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    def _message(self, method: TelegramMethod) -> Message:
        chat_id = getattr(method, "chat_id", 0)
        chat_type = "private" if isinstance(chat_id, int) and chat_id > 0 else "supergroup"
        return Message(
            message_id=next(self._ids),
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id if isinstance(chat_id, int) else -100, type=chat_type),
            text=getattr(method, "text", None) or getattr(method, "caption", None) or "",
        )

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.calls.append(method)
        name = type(method).__name__
        if name in self.fail_methods:
            from aiogram.exceptions import TelegramBadRequest

            raise TelegramBadRequest(method=method, message="Bad Request: mocked failure")
        if name == "GetMe":
            return TgUser(id=999, is_bot=True, first_name="CRM", username="crm_test_bot")
        if name in {"SendMessage", "SendPhoto", "EditMessageText", "EditMessageCaption"}:
            return self._message(method).as_(bot)
        if name == "SendMediaGroup":
            return [self._message(method).as_(bot) for _ in method.media]
        if name == "GetChat":
            from aiogram.types import ChatFullInfo

            # model_construct: набор обязательных полей ChatFullInfo меняется между версиями Bot API
            return ChatFullInfo.model_construct(id=method.chat_id, type="supergroup", title="Test chat", is_forum=True)
        if name == "GetChatMember":
            from aiogram.types import ChatMemberAdministrator

            return ChatMemberAdministrator.model_construct(status="administrator", user=TgUser(id=999, is_bot=True, first_name="CRM"))
        if name == "CreateForumTopic":
            return ForumTopic(message_thread_id=next(self._ids), name=method.name, icon_color=0)
        return True

    # помощники для проверок
    def of(self, name: str) -> list[TelegramMethod]:
        return [c for c in self.calls if type(c).__name__ == name]

    def texts(self, chat_id: int | None = None) -> list[str]:
        out = []
        for c in self.calls:
            if type(c).__name__ in {"SendMessage", "EditMessageText"} and (chat_id is None or c.chat_id == chat_id):
                out.append(c.text)
            if type(c).__name__ == "AnswerCallbackQuery" and c.text and chat_id is None:
                out.append(c.text)
        return out

    def last_text(self, chat_id: int | None = None) -> str:
        texts = self.texts(chat_id)
        return texts[-1] if texts else ""


def make_config(**overrides) -> Config:
    values = dict(
        bot_token="123456:TEST",
        owner_id=OWNER_ID,
        database_url="sqlite+aiosqlite:///:memory:",
        work_chat_id=WORK_CHAT,
        public_channel_id=CHANNEL,
        base_topic_id=11,
        archive_topic_id=12,
        deals_topic_id=13,
        chat_topic_id=14,
        leads_topic_id=15,
        default_city="Днепр",
        timezone="Europe/Kyiv",
    )
    values.update(overrides)
    return Config(**values)


@pytest_asyncio.fixture
async def engine(tmp_path):
    url = os.getenv("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    eng = create_engine(url)
    async with eng.begin() as conn:
        if url.startswith("postgresql"):
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return create_session_factory(engine)


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def mock_session() -> MockSession:
    return MockSession()


@pytest_asyncio.fixture
async def ctx(session_factory, mock_session) -> AppContext:
    bot = Bot(token="123456:TEST", session=mock_session, default=DefaultBotProperties(parse_mode="HTML"))
    return AppContext(bot=bot, config=make_config(), session_factory=session_factory, bot_username="crm_test_bot")


class UpdateFactory:
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    def user(self, uid: int, username: str | None = None, name: str = "User") -> TgUser:
        return TgUser(id=uid, is_bot=False, first_name=name, username=username)

    def message(self, uid: int, text: str | None = None, *, chat_id: int | None = None, chat_type: str = "private",
                thread_id: int | None = None, username: str | None = None, **extra) -> Update:
        chat = Chat(id=chat_id if chat_id is not None else uid, type=chat_type, is_forum=chat_type == "supergroup" or None)
        msg = Message(
            message_id=next(self._ids),
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=self.user(uid, username),
            text=text,
            message_thread_id=thread_id,
            **extra,
        )
        return Update(update_id=next(self._ids), message=msg)

    def callback(self, uid: int, data: str, *, chat_id: int | None = None, chat_type: str = "private",
                 message_text: str = "…", username: str | None = None) -> Update:
        chat = Chat(id=chat_id if chat_id is not None else uid, type=chat_type)
        msg = Message(message_id=next(self._ids), date=datetime.now(timezone.utc), chat=chat, text=message_text)
        cq = CallbackQuery(id=str(next(self._ids)), from_user=self.user(uid, username), chat_instance="ci", data=data, message=msg)
        return Update(update_id=next(self._ids), callback_query=cq)


@pytest.fixture
def updates() -> UpdateFactory:
    return UpdateFactory()


@pytest_asyncio.fixture
async def app(ctx):
    """Диспетчер со всеми роутерами и мок-ботом."""
    from bot.handlers import common, deals, leads, owner, properties, public, realtor, reports, stats
    from bot.main import build_dispatcher

    # Роутеры — модульные синглтоны; между тестами отвязываем их от предыдущего диспетчера
    for module in (common, owner, realtor, properties, leads, deals, reports, stats, public):
        module.router._parent_router = None
    common.fallback_router._parent_router = None
    dp = build_dispatcher(ctx)

    class App:
        def __init__(self) -> None:
            self.dp, self.ctx, self.api = dp, ctx, ctx.bot.session

        async def feed(self, update: Update) -> None:
            await self.dp.feed_update(self.ctx.bot, update)

    return App()
