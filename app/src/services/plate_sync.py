"""Sync plates from our DB to BOTH cameras' on-device allowlist/blocklist.

Used by the admin panel after create / update / delete / Excel import so the
cameras themselves (not only our backend) recognize the plate and can auto-
open the barrier for whitelisted cars per ISAPI §10.2.
"""
from __future__ import annotations

import logging
from typing import Iterable

from src.isapi.registry import registry
from src.models import Plate

logger = logging.getLogger(__name__)

# Defaults assumed when uploading. Camera firmware requires both fields per
# §11.6.5.3; admins typically don't care about the colour/type distinction.
DEFAULT_PLATE_COLOR = "blue"
DEFAULT_PLATE_TYPE = "92TypeCivil"


async def push_plate(plate: Plate) -> list[tuple[str, bool, str]]:
    """Push this plate to every camera. Returns [(cam_name, ok, message), ...]."""
    results: list[tuple[str, bool, str]] = []
    for client in registry.all():
        resp = await client.add_plate_record(
            record_id=plate.id,
            plate_number=plate.plate_number,
            is_allowed=plate.is_allowed,
            plate_color=DEFAULT_PLATE_COLOR,
            plate_type=DEFAULT_PLATE_TYPE,
        )
        logger.info(
            "plate-sync push %s → %s: ok=%s msg=%s",
            plate.plate_number,
            client.cam.name,
            resp.ok,
            resp.message,
        )
        results.append((client.cam.name, resp.ok, resp.message))
    return results


async def push_plates_bulk(plates: Iterable[Plate]) -> list[tuple[str, bool, str]]:
    """Push many plates to every camera in one batched ISAPI call per camera."""
    plate_list = list(plates)
    if not plate_list:
        return []
    payload = [
        {
            "id": str(p.id)[:16],
            "listType": "allowList" if p.is_allowed else "blockList",
            "LicensePlate": p.plate_number,
            "plateColor": DEFAULT_PLATE_COLOR,
            "plateType": DEFAULT_PLATE_TYPE,
            "operationType": "add",
        }
        for p in plate_list
    ]
    results: list[tuple[str, bool, str]] = []
    for client in registry.all():
        resp = await client.add_plate_records_bulk([dict(r) for r in payload])
        logger.info(
            "plate-sync bulk-push %d plates → %s: ok=%s msg=%s",
            len(plate_list),
            client.cam.name,
            resp.ok,
            resp.message,
        )
        results.append((client.cam.name, resp.ok, resp.message))
    return results


async def delete_plate(plate_number: str) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for client in registry.all():
        resp = await client.delete_plate_record(plate_number)
        logger.info(
            "plate-sync delete %s → %s: ok=%s msg=%s",
            plate_number,
            client.cam.name,
            resp.ok,
            resp.message,
        )
        results.append((client.cam.name, resp.ok, resp.message))
    return results
