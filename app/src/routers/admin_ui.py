"""Server-rendered admin panel (Jinja2).

Login posts credentials, we mint a JWT and store it in an HttpOnly cookie
named `access_token`. The same cookie is read by `JWTAuthMiddleware` on
every subsequent request.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.database import get_db
from src.models import (
    BarrierAction,
    BarrierActionLog,
    BarrierActor,
    Direction,
    EntryExitLog,
    Plate,
    PlatePhoneLink,
    TelegramUser,
)
from src.security import (
    ACCESS_COOKIE,
    check_admin_credentials,
    create_access_token,
    require_admin,
)
from src.utils.tz import fmt_local, local_day_range, now_local, to_local

templates = Jinja2Templates(directory="templates")
templates.env.filters["localtime"] = fmt_local
templates.env.filters["to_local"] = to_local
router = APIRouter(include_in_schema=False)


def _set_token_cookie(response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        # Set Secure when serving over HTTPS in front of this app.
        secure=False,
        max_age=s.jwt_expire_minutes * 60,
        path="/",
    )


# ---------- auth ----------
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if getattr(request.state, "user", None):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if not check_admin_credentials(username, password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Login yoki parol noto‘g‘ri"}
        )
    token = create_access_token(username)
    response = RedirectResponse("/", status_code=302)
    _set_token_cookie(response, token)
    return response


@router.post("/logout")
async def logout(request: Request):
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(ACCESS_COOKIE, path="/")
    return response


# ---------- dashboard ----------
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    require_admin(request)

    today = now_local()
    day_start, day_end = local_day_range(today.year, today.month, today.day)

    plates_total = await db.scalar(select(func.count(Plate.id))) or 0
    plates_allowed = (
        await db.scalar(select(func.count(Plate.id)).where(Plate.is_allowed.is_(True)))
        or 0
    )
    plates_blocked = plates_total - plates_allowed

    users_total = await db.scalar(select(func.count(TelegramUser.id))) or 0
    users_linked = (
        await db.scalar(
            select(func.count(TelegramUser.id)).where(
                TelegramUser.telegram_chat_id.is_not(None)
            )
        )
        or 0
    )

    today_window = (
        EntryExitLog.event_time >= day_start,
        EntryExitLog.event_time < day_end,
    )
    today_entries = (
        await db.scalar(
            select(func.count(EntryExitLog.id)).where(
                *today_window, EntryExitLog.direction == Direction.ENTRY
            )
        )
        or 0
    )
    today_exits = (
        await db.scalar(
            select(func.count(EntryExitLog.id)).where(
                *today_window, EntryExitLog.direction == Direction.EXIT
            )
        )
        or 0
    )
    today_allowed = (
        await db.scalar(
            select(func.count(EntryExitLog.id)).where(
                *today_window, EntryExitLog.is_allowed.is_(True)
            )
        )
        or 0
    )
    today_denied = (
        await db.scalar(
            select(func.count(EntryExitLog.id)).where(
                *today_window, EntryExitLog.is_allowed.is_(False)
            )
        )
        or 0
    )

    # Statistics ignore AUTO_LOOP entirely — those are the periodic system
    # close pings and shouldn't count as "user-triggered" barrier actions.
    barrier_window = (
        BarrierActionLog.created_at >= day_start,
        BarrierActionLog.created_at < day_end,
        BarrierActionLog.actor != BarrierActor.AUTO_LOOP,
    )
    today_opens = (
        await db.scalar(
            select(func.count(BarrierActionLog.id)).where(
                *barrier_window, BarrierActionLog.action == BarrierAction.OPEN
            )
        )
        or 0
    )
    today_closes = (
        await db.scalar(
            select(func.count(BarrierActionLog.id)).where(
                *barrier_window, BarrierActionLog.action == BarrierAction.CLOSE
            )
        )
        or 0
    )

    recent_entries = list(
        (
            await db.scalars(
                select(EntryExitLog).order_by(EntryExitLog.event_time.desc()).limit(10)
            )
        ).all()
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": {
                "plates_total": plates_total,
                "plates_allowed": plates_allowed,
                "plates_blocked": plates_blocked,
                "users_total": users_total,
                "users_linked": users_linked,
                "today_entries": today_entries,
                "today_exits": today_exits,
                "today_total": today_entries + today_exits,
                "today_allowed": today_allowed,
                "today_denied": today_denied,
                "today_opens": today_opens,
                "today_closes": today_closes,
            },
            "today": today,
            "recent_entries": recent_entries,
        },
    )


# ---------- plates page ----------
@router.get("/plates", response_class=HTMLResponse)
async def plates_page(request: Request, db: AsyncSession = Depends(get_db)):
    require_admin(request)
    rows = list(
        (await db.scalars(select(Plate).order_by(Plate.created_at.desc()))).all()
    )
    return templates.TemplateResponse(request, "plates.html", {"plates": rows})


# ---------- users page ----------
@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, db: AsyncSession = Depends(get_db)):
    require_admin(request)
    rows = list(
        (
            await db.scalars(
                select(TelegramUser).order_by(TelegramUser.created_at.desc())
            )
        ).all()
    )
    return templates.TemplateResponse(request, "users.html", {"users": rows})


# ---------- links page ----------
@router.get("/links", response_class=HTMLResponse)
async def links_page(request: Request, db: AsyncSession = Depends(get_db)):
    require_admin(request)
    from sqlalchemy.orm import selectinload

    links = list(
        (
            await db.scalars(
                select(PlatePhoneLink)
                .options(
                    selectinload(PlatePhoneLink.plate),
                    selectinload(PlatePhoneLink.telegram_user),
                )
                .order_by(PlatePhoneLink.created_at.desc())
            )
        ).all()
    )
    plates = list((await db.scalars(select(Plate).order_by(Plate.plate_number))).all())
    users = list(
        (await db.scalars(select(TelegramUser).order_by(TelegramUser.phone_number))).all()
    )
    return templates.TemplateResponse(
        request,
        "links.html",
        {"links": links, "plates": plates, "users": users},
    )


# ---------- logs page ----------
@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    plate: str | None = None,
    direction: str | None = None,
    camera: str | None = None,
    date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    require_admin(request)
    from src.routers.logs import _build_entry_query
    from src.utils.query import opt_str, parse_iso_date

    plate_v = opt_str(plate)
    direction_v = opt_str(direction)
    camera_v = opt_str(camera)
    date_v = opt_str(date)
    year_v, month_v, day_v = parse_iso_date(date_v)

    stmt = _build_entry_query(
        plate_v, direction_v, year_v, month_v, day_v, None, None, camera_v
    ).limit(500)
    rows = list((await db.scalars(stmt)).all())
    barrier_rows = list(
        (
            await db.scalars(
                select(BarrierActionLog)
                .where(BarrierActionLog.actor != BarrierActor.AUTO_LOOP)
                .order_by(BarrierActionLog.created_at.desc())
                .limit(100)
            )
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "entries": rows,
            "barrier": barrier_rows,
            "filters": {
                "plate": plate_v or "",
                "direction": direction_v or "",
                "camera": camera_v or "",
                "date": date_v or "",
            },
        },
    )


# ---------- cameras/barrier page ----------
@router.get("/cameras", response_class=HTMLResponse)
async def cameras_page(request: Request):
    require_admin(request)
    from src.isapi.registry import registry

    cams = [
        {
            "name": c.cam.name,
            "role": c.cam.role,
            "host": c.cam.host,
            "port": c.cam.port,
            "channel": c.cam.channel,
        }
        for c in registry.all()
    ]
    return templates.TemplateResponse(request, "cameras.html", {"cameras": cams})
