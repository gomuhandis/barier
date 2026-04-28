"""Query-parameter helpers.

The admin filter form on /logs renders empty inputs as `?year=&month=&day=`
which FastAPI's default int validator rejects with HTTP 422. Wrapping the
parameter type in a `BeforeValidator` that converts empty strings to None
keeps the filter buttons (and downstream Excel export links) working
without forcing the template to omit empty params.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator


def _empty_to_none(v: Any) -> Any:
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


OptInt = Annotated[int | None, BeforeValidator(_empty_to_none)]
OptStr = Annotated[str | None, BeforeValidator(_empty_to_none)]


def opt_int(v: Any) -> int | None:
    """Imperative variant — used in templates / route bodies that take str."""
    cleaned = _empty_to_none(v)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except (TypeError, ValueError):
        return None


def opt_str(v: Any) -> str | None:
    cleaned = _empty_to_none(v)
    return None if cleaned is None else str(cleaned)


def parse_iso_date(v: Any) -> tuple[int | None, int | None, int | None]:
    """Parse a YYYY-MM-DD string from an HTML <input type="date"> into a
    (year, month, day) triple. Returns (None, None, None) for empty / bad
    inputs so callers can plug it straight into the existing year/month/day
    filter logic.
    """
    cleaned = _empty_to_none(v)
    if cleaned is None:
        return None, None, None
    from datetime import date as _date

    try:
        d = _date.fromisoformat(str(cleaned))
    except ValueError:
        return None, None, None
    return d.year, d.month, d.day
