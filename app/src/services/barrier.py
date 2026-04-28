"""High-level barrier service: calls ISAPI, writes an audit log row, and
resets the alternating-close scheduler on open/unlock so we don't slam the
barrier down right after someone opened it."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.isapi.client import CtrlMode, ISAPIClient, ISAPIResponse
from src.isapi.registry import registry
from src.models import BarrierAction, BarrierActionLog, BarrierActor
from src.scheduler.alternating_close import scheduler

logger = logging.getLogger(__name__)

_OPENING_MODES = {"open", "unlock"}


def _maybe_reset(mode: CtrlMode, actor: BarrierActor, detail: str | None) -> None:
    if mode in _OPENING_MODES:
        scheduler.notify_opened(reason=f"{actor.value}:{mode} {detail or ''}".strip())


async def perform(
    session: AsyncSession,
    *,
    role: str,
    mode: CtrlMode,
    actor: BarrierActor,
    actor_detail: str | None = None,
    plate_number: str | None = None,
) -> ISAPIResponse:
    client: ISAPIClient | None = registry.by_role(role)
    if client is None:
        logger.warning("No camera for role=%s", role)
        resp = ISAPIResponse(ok=False, status_code=0, message="unknown camera role", raw="")
    else:
        resp = await client.barrier_control(mode)

    if resp.ok:
        _maybe_reset(mode, actor, actor_detail)

    log = BarrierActionLog(
        camera_name=client.cam.name if client else role,
        action=BarrierAction(mode),
        actor=actor,
        actor_detail=actor_detail,
        plate_number=plate_number,
        status_code=resp.status_code,
        status_message=resp.message[:256],
    )
    session.add(log)
    await session.commit()
    return resp


async def perform_on_all(
    session: AsyncSession,
    *,
    mode: CtrlMode,
    actor: BarrierActor,
    actor_detail: str | None = None,
    plate_number: str | None = None,
) -> list[tuple[str, ISAPIResponse]]:
    out: list[tuple[str, ISAPIResponse]] = []
    any_ok = False
    for client in registry.all():
        resp = await client.barrier_control(mode)
        out.append((client.cam.role, resp))
        if resp.ok:
            any_ok = True
        session.add(
            BarrierActionLog(
                camera_name=client.cam.name,
                action=BarrierAction(mode),
                actor=actor,
                actor_detail=actor_detail,
                plate_number=plate_number,
                status_code=resp.status_code,
                status_message=resp.message[:256],
            )
        )
    if any_ok:
        _maybe_reset(mode, actor, actor_detail)
    await session.commit()
    return out
