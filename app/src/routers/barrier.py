from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.isapi.registry import registry
from src.models import BarrierActor
from src.schemas import BarrierCommand, BarrierResult
from src.security import require_admin
from src.services import barrier as barrier_service

router = APIRouter(prefix="/api/barrier", tags=["barrier"])

_VALID_MODES = {"open", "close", "lock", "unlock"}


@router.post("/command", response_model=BarrierResult)
async def send_command(
    payload: BarrierCommand,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BarrierResult:
    admin = require_admin(request)
    if payload.mode not in _VALID_MODES:
        raise HTTPException(status_code=400, detail="invalid mode")
    if payload.role not in {"entry", "exit"}:
        raise HTTPException(status_code=400, detail="invalid role")
    resp = await barrier_service.perform(
        db,
        role=payload.role,
        mode=payload.mode,  # type: ignore[arg-type]
        actor=BarrierActor.ADMIN,
        actor_detail=admin,
    )
    return BarrierResult(ok=resp.ok, status_code=resp.status_code, message=resp.message)


@router.get("/status")
async def status(request: Request) -> dict:
    require_admin(request)
    out: dict = {}
    for client in registry.all():
        resp, state = await client.barrier_status()
        out[client.cam.role] = {
            "camera": client.cam.name,
            "host": client.cam.host,
            "ok": resp.ok,
            "status_code": resp.status_code,
            "message": resp.message,
            "barrier_state": state,
        }
    return out
