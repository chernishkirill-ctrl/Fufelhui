"""Работа со временем: в БД — UTC (naive), для людей — часовой пояс агентства."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def local_now(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def local_today(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()


def to_local(dt: datetime | None, tz: ZoneInfo) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def local_midnight_utc(d: date, tz: ZoneInfo) -> datetime:
    """Начало локальных суток d в UTC (naive) — для сравнения с полями created_at."""
    return datetime.combine(d, time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def fmt_dt(dt: datetime | None, tz: ZoneInfo) -> str:
    loc = to_local(dt, tz)
    return loc.strftime("%d.%m.%Y %H:%M") if loc else "—"


def fmt_date(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"


def parse_date(raw: str, today: date | None = None) -> date | None:
    raw = raw.strip().lower()
    if today and raw in {"сегодня", "today"}:
        return today
    if today and raw in {"вчера", "yesterday"}:
        return today - timedelta(days=1)
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", raw)
    if not m:
        m2 = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
        if not m2:
            return None
        y, mo, d = map(int, m2.groups())
    else:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else (today or date.today()).year
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_hhmm(raw: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", raw)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return h, mi
    return None


@dataclass(frozen=True)
class Period:
    key: str
    title: str
    start: date  # включительно, локальная дата
    end: date  # включительно, локальная дата

    def utc_bounds(self, tz: ZoneInfo) -> tuple[datetime, datetime]:
        return local_midnight_utc(self.start, tz), local_midnight_utc(self.end + timedelta(days=1), tz)


PERIOD_TITLES = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "week": "7 дней",
    "month": "Этот месяц",
    "all": "Всё время",
}


def make_period(key: str, tz: ZoneInfo) -> Period:
    today = local_today(tz)
    if key == "today":
        return Period(key, PERIOD_TITLES[key], today, today)
    if key == "yesterday":
        y = today - timedelta(days=1)
        return Period(key, PERIOD_TITLES[key], y, y)
    if key == "week":
        return Period(key, PERIOD_TITLES[key], today - timedelta(days=6), today)
    if key == "month":
        return Period(key, PERIOD_TITLES[key], today.replace(day=1), today)
    if key == "all":
        return Period(key, PERIOD_TITLES[key], date(2000, 1, 1), today)
    raise ValueError(f"Неизвестный период: {key}")


def parse_custom_period(raw: str, tz: ZoneInfo) -> Period | None:
    """«01.09.2026-15.09.2026» или «01.09 - 15.09»."""
    parts = re.split(r"\s*(?:—|–|-|\s)\s*", raw.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    today = local_today(tz)
    start, end = parse_date(parts[0], today), parse_date(parts[1], today)
    if not start or not end:
        return None
    if start > end:
        start, end = end, start
    return Period("custom", f"{fmt_date(start)} — {fmt_date(end)}", start, end)
