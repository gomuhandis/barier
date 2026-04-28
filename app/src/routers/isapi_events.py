"""Endpoint that the ANPR cameras POST to when they recognize a plate.

Configure on the camera (or via `PUT /ISAPI/Traffic/ANPR/alarmHttpPushProtocol`)
to push events to:

    POST {PUBLIC_LISTENER_URL}/isapi/anpr/{role}

where `role` is "entry" or "exit". Content-Type is multipart/form-data with
`anpr.xml` as one of the parts.

Direction resolution: the URL `{role}` is only a hint. The canonical role is
the one configured for the camera that actually sent the event, matched by
source IP. This way a camera mistakenly pointed at the wrong URL still
produces correctly-labelled entry/exit logs.

Auth: the JWT middleware whitelists this path because cameras can't carry a
JWT. We protect it instead with `_authorize_camera_push`, which accepts any
of: matching source IP, `X-ANPR-Secret` header, or HTTP Basic auth.
"""
from __future__ import annotations

import base64
import binascii
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import CameraConfig, get_settings
from src.database import get_db
from src.isapi.parser import ANPREvent, parse_anpr_xml
from src.services.anpr import handle_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/isapi", tags=["isapi-events"])


async def _read_xml_payload(request: Request) -> bytes | None:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        for key in ("anpr.xml", "anprXml", "anpr", "event.xml"):
            part = form.get(key)
            if part is not None:
                if hasattr(part, "read"):
                    return await part.read()
                return str(part).encode()
        for value in form.values():
            if hasattr(value, "read") and getattr(value, "content_type", "") in (
                "text/xml",
                "application/xml",
            ):
                return await value.read()
        return None
    return await request.body()


def _resolve_camera(
    cameras: list[CameraConfig],
    *,
    remote_ip: str | None,
    event: ANPREvent,
    url_role: str,
) -> tuple[CameraConfig | None, str]:
    """Return (camera, source) where source explains how we matched.

    We want to derive the role from the camera that actually sent the
    event, not from whatever URL it was configured to call.
    """
    if remote_ip:
        for c in cameras:
            if c.host == remote_ip:
                return c, f"remote_ip={remote_ip}"

    if event.device_ip:
        for c in cameras:
            if c.host == event.device_ip:
                return c, f"event.ipAddress={event.device_ip}"

    # Final fallback: trust the URL path so single-camera dev setups still work.
    for c in cameras:
        if c.role == url_role:
            return c, f"url_role={url_role}"

    return None, "no-match"


def _basic_auth_matches(
    request: Request, expected_user: str, expected_pass: str
) -> bool:
    """Return True if the request carries `Authorization: Basic <b64>` with
    credentials matching the configured user/pass.
    """
    if not (expected_user and expected_pass):
        return False
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(auth.split(" ", 1)[1].strip()).decode(
            "utf-8", errors="replace"
        )
    except (binascii.Error, ValueError):
        return False
    user, sep, pwd = decoded.partition(":")
    if not sep:
        return False
    return user == expected_user and pwd == expected_pass


def _authorize_camera_push(request: Request) -> None:
    """Block anyone who is not a configured camera. Accepts any of:

      * source IP matching a CAM*_HOST,
      * `X-ANPR-Secret` header matching `ANPR_INGEST_SECRET`,
      * HTTP Basic auth matching `ANPR_INGEST_USERNAME` / `_PASSWORD`.

    The JWT middleware whitelists this path because cameras can't carry a
    Bearer token, but that doesn't mean the endpoint should be open to the
    whole network — a forged XML with an allowlisted plate would otherwise
    auto-open the barrier.
    """
    settings = get_settings()
    camera_hosts = {c.host for c in settings.cameras()}
    remote_ip = request.client.host if request.client else None

    ip_matches = bool(remote_ip) and remote_ip in camera_hosts

    secret = settings.anpr_ingest_secret
    secret_matches = bool(secret) and (
        request.headers.get("x-anpr-secret", "").strip() == secret
    )

    basic_matches = _basic_auth_matches(
        request, settings.anpr_ingest_username, settings.anpr_ingest_password
    )

    if not (ip_matches or secret_matches or basic_matches):
        logger.warning(
            "Rejected ANPR push: remote_ip=%s not in camera hosts %s, "
            "X-ANPR-Secret=%s, Basic-auth=%s",
            remote_ip,
            camera_hosts,
            "ok" if secret_matches else "missing/wrong",
            "ok" if basic_matches else "missing/wrong",
        )
        # 401 with WWW-Authenticate makes the camera retry with Basic creds
        # if it has them configured (this is the normal HTTP auth handshake).
        raise HTTPException(
            status_code=401,
            detail="anpr push not authorized",
            headers={"WWW-Authenticate": 'Basic realm="anpr-ingest"'},
        )


@router.post("/anpr/{role}")
async def receive_anpr_event(
    role: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    if role not in {"entry", "exit"}:
        raise HTTPException(status_code=400, detail="invalid role")

    _authorize_camera_push(request)

    xml_bytes = await _read_xml_payload(request)
    if not xml_bytes:
        logger.warning("ANPR event received but no XML payload, role=%s", role)
        return {"ok": False, "reason": "no_xml"}

    event = parse_anpr_xml(xml_bytes)
    if event is None:
        logger.info("ANPR event ignored (no plate), role=%s", role)
        return {"ok": False, "reason": "no_plate"}

    remote_ip = request.client.host if request.client else None
    cameras = get_settings().cameras()
    cam, match_source = _resolve_camera(
        cameras, remote_ip=remote_ip, event=event, url_role=role
    )

    if cam is None:
        logger.error(
            "ANPR event has no matching camera (remote_ip=%s, device_ip=%s, url_role=%s)",
            remote_ip,
            event.device_ip,
            role,
        )
        raise HTTPException(status_code=500, detail="camera not configured")

    if cam.role != role:
        logger.warning(
            "ANPR role mismatch: URL said %s, but %s came from %s "
            "(plate=%s, match=%s) — using %s",
            role,
            cam.name,
            cam.host,
            event.plate_number,
            match_source,
            cam.role,
        )

    await handle_event(db, cam, event)
    return {
        "ok": True,
        "plate": event.plate_number,
        "role": cam.role,
        "camera": cam.name,
        "matched_by": match_source,
    }
