"""Local-timezone helpers.

The project runs in Tashkent (GMT+5, no DST). All timestamps stored in the
database are timezone-aware UTC; everything user-visible (admin panel,
Telegram notifications, Excel exports, log filters by year/month/day) is
shown in local time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Offset is hardcoded — Uzbekistan does not observe DST.
LOCAL_TZ = timezone(timedelta(hours=5), name="GMT+5")


def now_local() -> datetime:
    """Current wall-clock time as a tz-aware datetime in GMT+5."""
    return datetime.now(LOCAL_TZ)


def to_local(value: datetime | None) -> datetime | None:
    """Convert any datetime to GMT+5 (assumes UTC if naive)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TZ)


def fmt_local(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Convert to GMT+5 and format. Empty string for None."""
    if value is None:
        return ""
    return to_local(value).strftime(fmt)


def local_day_range(year: int, month: int, day: int) -> tuple[datetime, datetime]:
    """Inclusive [start, end) datetime range covering one local-time day."""
    start_local = datetime(year, month, day, 0, 0, 0, tzinfo=LOCAL_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local, end_local


def local_month_range(year: int, month: int) -> tuple[datetime, datetime]:
    start_local = datetime(year, month, 1, tzinfo=LOCAL_TZ)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=LOCAL_TZ)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=LOCAL_TZ)
    return start_local, end_local


def local_year_range(year: int) -> tuple[datetime, datetime]:
    return (
        datetime(year, 1, 1, tzinfo=LOCAL_TZ),
        datetime(year + 1, 1, 1, tzinfo=LOCAL_TZ),
    )
