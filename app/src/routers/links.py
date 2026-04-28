from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models import Plate, PlatePhoneLink, TelegramUser
from src.schemas import LinkCreate, LinkOut
from src.security import require_admin

router = APIRouter(prefix="/api/links", tags=["links"])


@router.get("", response_model=list[LinkOut])
async def list_links(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[PlatePhoneLink]:
    require_admin(request)
    stmt = select(PlatePhoneLink).order_by(PlatePhoneLink.created_at.desc())
    return list((await db.scalars(stmt)).all())


@router.post("", response_model=LinkOut, status_code=201)
async def create_link(
    payload: LinkCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PlatePhoneLink:
    require_admin(request)
    if not await db.get(Plate, payload.plate_id):
        raise HTTPException(status_code=404, detail="plate not found")
    if not await db.get(TelegramUser, payload.telegram_user_id):
        raise HTTPException(status_code=404, detail="telegram user not found")
    link = PlatePhoneLink(
        plate_id=payload.plate_id,
        telegram_user_id=payload.telegram_user_id,
    )
    db.add(link)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="link already exists")
    await db.refresh(link)
    return link


@router.delete("/{link_id}", status_code=204)
async def delete_link(
    link_id: int, request: Request, db: AsyncSession = Depends(get_db)
) -> None:
    require_admin(request)
    link = await db.get(PlatePhoneLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="not found")
    await db.delete(link)
    await db.commit()
