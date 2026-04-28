from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models import BarrierActionLog, EntryExitLog
from src.schemas import EntryExitLogOut
from src.security import require_admin
from src.utils.query import OptInt, OptStr
from src.utils.tz import (
    local_day_range,
    local_month_range,
    local_year_range,
    to_local,
)

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _build_entry_query(
    plate: str | None,
    direction: str | None,
    year: int | None,
    month: int | None,
    day: int | None,
    start: datetime | None,
    end: datetime | None,
    camera: str | None = None,
):
    stmt = select(EntryExitLog).order_by(EntryExitLog.event_time.desc())
    if plate:
        stmt = stmt.where(EntryExitLog.plate_number.ilike(f"%{plate.upper()}%"))
    if direction in {"entry", "exit"}:
        stmt = stmt.where(EntryExitLog.direction == direction)
    if camera:
        stmt = stmt.where(EntryExitLog.camera_name.ilike(f"%{camera}%"))
    if year and month and day:
        s, e = local_day_range(year, month, day)
        stmt = stmt.where(EntryExitLog.event_time >= s, EntryExitLog.event_time < e)
    elif year and month:
        s, e = local_month_range(year, month)
        stmt = stmt.where(EntryExitLog.event_time >= s, EntryExitLog.event_time < e)
    elif year:
        s, e = local_year_range(year)
        stmt = stmt.where(EntryExitLog.event_time >= s, EntryExitLog.event_time < e)
    if start:
        stmt = stmt.where(EntryExitLog.event_time >= start)
    if end:
        stmt = stmt.where(EntryExitLog.event_time <= end)
    return stmt


@router.get("/entries", response_model=list[EntryExitLogOut])
async def list_entries(
    request: Request,
    plate: OptStr = None,
    direction: OptStr = None,
    camera: OptStr = None,
    year: OptInt = None,
    month: OptInt = None,
    day: OptInt = None,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=200, le=2000),
    db: AsyncSession = Depends(get_db),
) -> list[EntryExitLog]:
    require_admin(request)
    stmt = _build_entry_query(
        plate, direction, year, month, day, start, end, camera
    ).limit(limit)
    return list((await db.scalars(stmt)).all())


@router.get("/barrier")
async def list_barrier_actions(
    request: Request,
    limit: int = Query(default=200, le=2000),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    require_admin(request)
    stmt = (
        select(BarrierActionLog)
        .order_by(BarrierActionLog.created_at.desc())
        .limit(limit)
    )
    rows = list((await db.scalars(stmt)).all())
    return [
        {
            "id": r.id,
            "camera_name": r.camera_name,
            "action": r.action.value,
            "actor": r.actor.value,
            "actor_detail": r.actor_detail,
            "plate_number": r.plate_number,
            "status_code": r.status_code,
            "status_message": r.status_message,
            "created_at": to_local(r.created_at).isoformat() if r.created_at else None,
        }
        for r in rows
    ]
