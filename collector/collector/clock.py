"""The business day. Departures are Tashkent dates, so "today" is the date in Asia/Tashkent, not in UTC.

The server (Docker) runs in UTC: between 19:00 and 24:00 UTC the Tashkent date is already the next day.
Timestamps written to the database stay UTC (timestamptz); only date logic uses this.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    TASHKENT = ZoneInfo("Asia/Tashkent")
except ZoneInfoNotFoundError:  # no tz database in the image: Tashkent is UTC+5 all year (no DST since 1992)
    TASHKENT = timezone(timedelta(hours=5), "Asia/Tashkent")


def today_tashkent(now: datetime | None = None) -> date:
    """`now` (timezone-aware) is only for tests."""
    return (now or datetime.now(timezone.utc)).astimezone(TASHKENT).date()
