"""Handle an ANPR event from the camera:
  * store entry/exit log row
  * reset the alternating-close scheduler on every event so the periodic
    close request restarts from 0 (driver is at the gate — don't slam it)
  * if the plate is on the allowlist, explicitly send `open` to that camera's
    barrier via the ISAPI barrier service
  * send Telegram notifications to linked users
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import CameraConfig
from src.isapi.parser import ANPREvent
from src.models import (
    BarrierActor,
    Direction,
    EntryExitLog,
    Plate,
    PlatePhoneLink,
    TelegramUser,
)
from src.scheduler.alternating_close import scheduler
from src.services import barrier as barrier_service
from src.services.telegram_notify import send_message
from src.utils.tz import fmt_local

logger = logging.getLogger(__name__)


async def handle_event(
    session: AsyncSession,
    cam: CameraConfig,
    event: ANPREvent,
) -> None:
    plate = (
        await session.scalars(
            select(Plate)
            .options(
                selectinload(Plate.links).selectinload(PlatePhoneLink.telegram_user)
            )
            .where(Plate.plate_number == event.plate_number)
        )
    ).first()

    is_allowed = bool(plate and plate.is_allowed)

    log = EntryExitLog(
        plate_number=event.plate_number,
        direction=Direction(cam.role),
        camera_name=cam.name,
        camera_host=cam.host,
        confidence=event.confidence,
        is_allowed=is_allowed,
        plate_color=event.plate_color,
        event_time=event.event_time,
        raw_payload=event.raw_xml[:4000],
    )
    session.add(log)
    await session.commit()

    # Camera saw a car at the gate — restart the periodic close countdown
    # from 0 so the next close fires a full interval from now, regardless
    # of whether the plate is allowlisted.
    scheduler.notify_opened(
        reason=f"anpr:{cam.role} plate={event.plate_number}"
    )

    # Allowlisted plate → explicitly send the open command to that camera's
    # barrier. The Hikvision device also auto-opens via its on-board allowlist
    # (ISAPI §10.2), but issuing it from the server guarantees the open even
    # if the device-side list drifts and gives us an audit log row.
    if is_allowed:
        try:
            await barrier_service.perform(
                session,
                role=cam.role,
                mode="open",
                actor=BarrierActor.AUTO_ANPR,
                actor_detail=f"allowlist plate={event.plate_number}",
                plate_number=event.plate_number,
            )
        except Exception as exc:
            logger.warning(
                "auto-open failed for plate=%s role=%s: %s",
                event.plate_number,
                cam.role,
                exc,
            )

    # ----- Telegram notifications -----
    label = "🚗 Kirdi" if cam.role == "entry" else "🅿️ Chiqdi"
    when = fmt_local(event.event_time)
    allow_badge = "✅ Ruxsat" if is_allowed else "⛔ Ruxsat yo‘q"
    owner = (plate.owner_name if plate and plate.owner_name else "—")
    base_text = (
        f"{label}\n"
        f"<b>Moshina:</b> {event.plate_number}\n"
        f"<b>Egasi:</b> {owner}\n"
        f"<b>Kamera:</b> {cam.name}\n"
        f"<b>Holat:</b> {allow_badge}\n"
        f"<b>Vaqt:</b> {when}"
    )

    sent_chat_ids: set[int] = set()

    # 1. Per-plate links — owners/relatives subscribed to a specific car.
    if plate is not None:
        for link in plate.links:
            user = link.telegram_user
            if user.telegram_chat_id and user.is_active:
                if user.telegram_chat_id in sent_chat_ids:
                    continue
                sent_chat_ids.add(user.telegram_chat_id)
                await send_message(user.telegram_chat_id, base_text)

    # 2. Broadcast users — receive every ANPR event (admins, security desk).
    broadcast_users = list(
        (
            await session.scalars(
                select(TelegramUser).where(
                    TelegramUser.receive_all_events.is_(True),
                    TelegramUser.is_active.is_(True),
                    TelegramUser.telegram_chat_id.is_not(None),
                )
            )
        ).all()
    )
    for user in broadcast_users:
        if user.telegram_chat_id in sent_chat_ids:
            continue
        sent_chat_ids.add(user.telegram_chat_id)
        await send_message(user.telegram_chat_id, base_text)
