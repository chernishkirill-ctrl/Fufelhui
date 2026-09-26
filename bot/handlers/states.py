"""FSM-состояния сложных диалогов."""
from aiogram.fsm.state import State, StatesGroup


class PropertyAdd(StatesGroup):
    offer_type = State()
    property_type = State()
    rooms = State()
    area = State()
    floor = State()
    price = State()
    city = State()
    district = State()
    address = State()
    description = State()
    owner_contact = State()
    photos = State()
    confirm = State()


class PropertyImport(StatesGroup):
    url = State()
    confirm = State()
    edit_value = State()


class PropertyEdit(StatesGroup):
    value = State()
    photos = State()


class PropertySearch(StatesGroup):
    query = State()


class LeadBooking(StatesGroup):
    name = State()
    phone = State()
    time = State()
    comment = State()
    confirm = State()


class LeadWork(StatesGroup):
    viewing_time = State()
    note = State()


class DealCreate(StatesGroup):
    property = State()
    realtor = State()
    lead = State()
    deal_type = State()
    amount = State()
    commission = State()
    date = State()
    comment = State()
    confirm = State()


class DealEdit(StatesGroup):
    value = State()


class ReportFill(StatesGroup):
    number = State()
    comment = State()
    confirm = State()


class StatsCustom(StatesGroup):
    period = State()


class RealtorAdd(StatesGroup):
    telegram_id = State()


class SettingsEdit(StatesGroup):
    report_time = State()
