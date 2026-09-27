"""Точка входа: `python -m bot.main`.

Режимы:
  * webhook — если задан WEBHOOK_BASE_URL (на Render подставляется RENDER_EXTERNAL_URL автоматически);
  * polling — для локального запуска (BOT_MODE=polling).
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.config import Config, load_config
from bot.database.database import create_engine, create_session_factory, wait_for_database
from bot.handlers import common, deals, leads, owner, properties, public, realtor, reports, stats
from bot.middlewares.auth import DbSessionMiddleware
from bot.services import settings_service
from bot.services.context import AppContext
from bot.services.scheduler import ReportScheduler
from bot.webapp import setup_webapp

logger = logging.getLogger("bot")

ALLOWED_UPDATES = ["message", "callback_query"]


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    # Не пишем в лог тексты запросов/ответов Telegram и SQL (там могут быть персональные данные)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def build_dispatcher(ctx: AppContext, scheduler: ReportScheduler | None = None) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), scheduler=scheduler)
    middleware = DbSessionMiddleware(ctx)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.include_routers(
        common.router,
        owner.router,
        realtor.router,
        properties.router,
        leads.router,
        deals.router,
        reports.router,
        stats.router,
        public.router,
        common.fallback_router,
    )

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        logger.error("Необработанная ошибка: %s", type(event.exception).__name__, exc_info=event.exception)
        update = event.update
        try:
            if update.callback_query:
                await update.callback_query.answer("⚠️ Сталася помилка. Спробуйте ще раз.", show_alert=True)
            elif update.message and update.message.chat.type == "private":
                await update.message.answer("⚠️ Сталася помилка. Спробуйте ще раз або натисніть /cancel.")
        except Exception:  # noqa: BLE001
            pass
        return True

    return dp


async def set_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="menu", description="Головне меню"),
                BotCommand(command="cancel", description="Скасувати дію"),
                BotCommand(command="help", description="Допомога"),
                BotCommand(command="id", description="Мій Telegram ID"),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось установить команды: %s", exc)


def cron_routes(app: web.Application, ctx: AppContext) -> None:
    """HTTP-эндпоинты для внешнего планировщика (Render Cron Job) — дублируют встроенный scheduler."""

    def authorized(request: web.Request) -> bool:
        secret = ctx.config.cron_secret
        provided = request.headers.get("X-Cron-Secret") or request.query.get("key") or ""
        return bool(secret) and hmac.compare_digest(provided, secret)

    async def reminders(request: web.Request) -> web.Response:
        if not authorized(request):
            return web.Response(status=403, text="forbidden")
        sent = await reports.send_report_reminders(ctx)
        return web.json_response({"sent": sent})

    async def summary(request: web.Request) -> web.Response:
        if not authorized(request):
            return web.Response(status=403, text="forbidden")
        await reports.send_report_summary(ctx)
        return web.json_response({"ok": True})

    app.router.add_post("/cron/report-reminders", reminders)
    app.router.add_post("/cron/report-summary", summary)


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def run(config: Config) -> None:
    engine = create_engine(config.database_url)
    await wait_for_database(engine)
    session_factory = create_session_factory(engine)
    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    ctx = AppContext(bot=bot, config=config, session_factory=session_factory, bot_username=me.username or "")
    logger.info("Бот @%s запущен, режим: %s", me.username, "webhook" if config.use_webhook else "polling")

    async with session_factory() as session:
        rs = await settings_service.load(session, config)
    scheduler = ReportScheduler(ctx)
    dp = build_dispatcher(ctx, scheduler)
    await set_commands(bot)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    cron_routes(app, ctx)
    setup_webapp(app, ctx)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    scheduler.start(rs.report_time)
    runner: web.AppRunner | None = None
    polling: asyncio.Task | None = None
    try:
        if config.use_webhook:
            SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=config.webhook_secret).register(app, path=config.webhook_path)
            setup_application(app, dp, bot=bot)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", config.port).start()
            url = f"{config.webhook_base_url}{config.webhook_path}"
            await bot.set_webhook(url, secret_token=config.webhook_secret, allowed_updates=ALLOWED_UPDATES, drop_pending_updates=False)
            logger.info("Webhook установлен, HTTP-сервер слушает порт %s", config.port)
            await stop.wait()
        else:
            # В polling-режиме HTTP-сервер нужен только для health-check (если платформа требует порт)
            if os.getenv("PORT"):
                runner = web.AppRunner(app)
                await runner.setup()
                await web.TCPSite(runner, "0.0.0.0", config.port).start()
            await bot.delete_webhook(drop_pending_updates=False)
            polling = asyncio.create_task(dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES, handle_signals=False))
            await asyncio.wait({polling, asyncio.create_task(stop.wait())}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        logger.info("Остановка…")
        scheduler.shutdown()
        if polling and not polling.done():
            await dp.stop_polling()
        if runner:
            await runner.cleanup()
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    config = load_config()
    setup_logging(config.log_level)
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
