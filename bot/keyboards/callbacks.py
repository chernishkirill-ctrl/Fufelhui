"""Фабрики callback_data. Любой callback перепроверяет права на сервере — кнопки лишь навигация."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="m"):
    section: str


class PropCB(CallbackData, prefix="p"):
    action: str
    id: int = 0
    page: int = 0
    arg: str = ""


class LeadCB(CallbackData, prefix="l"):
    action: str
    id: int = 0
    page: int = 0
    arg: str = ""


class DealCB(CallbackData, prefix="d"):
    action: str
    id: int = 0
    page: int = 0
    arg: str = ""


class RealtorCB(CallbackData, prefix="r"):
    action: str
    id: int = 0
    page: int = 0


class StatsCB(CallbackData, prefix="s"):
    period: str
    scope: str = "all"  # all | me | realtors


class ReportCB(CallbackData, prefix="rep"):
    action: str
    id: int = 0
    day: str = ""  # YYYYMMDD


class SettingsCB(CallbackData, prefix="cfg"):
    action: str
    key: str = ""


class FormCB(CallbackData, prefix="f"):
    """Выбор вариантов внутри FSM-диалогов."""

    field: str
    value: str = ""
