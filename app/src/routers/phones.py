from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models import TelegramUser
from src.schemas import TelegramUserCreate, TelegramUserOut, TelegramUserUpdate
from src.security import require_admin

router = APIRouter(prefix="/api/phones", tags=["phones"])


def _normalize(phone: str) -> str:
    return phone.strip().replace(" ", "")


@router.get("", response_model=list[TelegramUserOut])
async def list_phones(
    request: Request,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[TelegramUser]:
    require_admin(request)
    stmt = select(TelegramUser).order_by(TelegramUser.created_at.desc())
    if q:
        stmt = stmt.where(TelegramUser.phone_number.ilike(f"%{q}%"))
    return list((await db.scalars(stmt)).all())


@router.post("", response_model=TelegramUserOut, status_code=201)
async def create_phone(
    payload: TelegramUserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TelegramUser:
    require_admin(request)
    user = TelegramUser(
        phone_number=_normalize(payload.phone_number),
        full_name=payload.full_name,
        is_active=payload.is_active,
        can_control_barrier=payload.can_control_barrier,
        can_view_own_events=payload.can_view_own_events,
        receive_all_events=payload.receive_all_events,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="phone already exists")
    await db.refresh(user)
    return user


@router.get("/{user_id}", response_model=TelegramUserOut)
async def get_phone(
    user_id: int, request: Request, db: AsyncSession = Depends(get_db)
) -> TelegramUser:
    require_admin(request)
    user = await db.get(TelegramUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")
    return user


@router.put("/{user_id}", response_model=TelegramUserOut)
async def update_phone(
    user_id: int,
    payload: TelegramUserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TelegramUser:
    require_admin(request)
    user = await db.get(TelegramUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")
    data = payload.model_dump(exclude_unset=True)
    if "phone_number" in data and data["phone_number"]:
        data["phone_number"] = _normalize(data["phone_number"])
    for k, v in data.items():
        setattr(user, k, v)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="duplicate phone number")
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
async def delete_phone(
    user_id: int, request: Request, db: AsyncSession = Depends(get_db)
) -> None:
    require_admin(request)
    user = await db.get(TelegramUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")
    await db.delete(user)
    await db.commit()
