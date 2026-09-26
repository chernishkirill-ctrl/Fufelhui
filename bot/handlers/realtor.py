"""Личный кабинет риелтора (меню). Доступ — только активным сотрудникам (фильтр IsRealtor)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers import deals as deals_h
from bot.handlers import leads as leads_h
from bot.handlers import properties as props_h
from bot.handlers import reports as reports_h
from bot.handlers import stats as stats_h
from bot.keyboards import menus
from bot.middlewares.filters import IsRealtor
from bot.services.auth import Actor
from bot.services.context import AppContext
from bot.services.settings_service import RuntimeSettings
from bot.utils.timeutils import make_period

router = Router(name="realtor")
router.message.filter(F.chat.type == "private", IsRealtor())


@router.message(StateFilter("*"), F.text == menus.R_MY_PROPS)
async def my_props(message: Message, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await props_h.show_properties_menu(message, actor, session)


@router.message(StateFilter("*"), F.text == menus.R_ADD)
async def add_prop(message: Message, actor: Actor, state: FSMContext) -> None:
    await props_h.start_add(message, actor, state)


@router.message(StateFilter("*"), F.text == menus.R_IMPORT)
async def import_prop(message: Message, actor: Actor, state: FSMContext) -> None:
    await props_h.start_import(message, actor, state)


@router.message(StateFilter("*"), F.text == menus.R_LEADS)
async def my_leads(message: Message, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await leads_h.show_leads_menu(message, actor, session)


@router.message(StateFilter("*"), F.text == menus.R_DEAL)
async def new_deal(message: Message, actor: Actor, session: AsyncSession, state: FSMContext) -> None:
    await deals_h.start_deal(message, actor, session, state)


@router.message(StateFilter("*"), F.text == menus.R_STATS)
async def my_stats(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, rs: RuntimeSettings, state: FSMContext) -> None:
    await state.clear()
    await stats_h.show_stats(message, actor, session, ctx, rs, make_period("month", ctx.config.tz))


@router.message(StateFilter("*"), F.text == menus.R_REPORT)
async def daily_report(message: Message, actor: Actor, session: AsyncSession, ctx: AppContext, state: FSMContext) -> None:
    await reports_h.start_report(message, actor, session, ctx, state)
