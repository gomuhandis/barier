"""Async ISAPI client for Hikvision ANPR cameras.

Endpoints used (per ISAPI Vehicle Access Control Management spec):

  Barrier (§11.6.3):
    PUT /ISAPI/Parking/channels/<channelID>/barrierGate
    GET /ISAPI/Parking/channels/<channelID>/barrierGate/barrierGateStatus

  Vehicle list comparison (§11.6.5 — vehicle allowlist / blocklist):
    PUT  /ISAPI/Traffic/channels/<channelID>/licensePlateAuditData/record?format=json
    PUT  /ISAPI/Traffic/channels/<channelID>/DelLicensePlateAuditData?format=json
    POST /ISAPI/Traffic/channels/<channelID>/searchLPListAudit?format=json
    GET  /ISAPI/Traffic/channels/<channelID>/licensePlateAuditData/record/capabilities?format=json

Auth: HTTP Digest (RFC 2617) — required by the ISAPI framework (§3).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from lxml import etree

from src.config import CameraConfig

logger = logging.getLogger(__name__)

CtrlMode = Literal["open", "close", "lock", "unlock"]

_BARRIER_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<BarrierGate xmlns="http://www.isapi.org/ver20/XMLSchema" version="2.0">'
    "<ctrlMode>{mode}</ctrlMode>"
    "</BarrierGate>"
)

_STATUS_MAP = {0: "no_signal", 1: "closed", 2: "open"}


@dataclass(slots=True)
class ISAPIResponse:
    ok: bool
    status_code: int
    message: str
    raw: str


class ISAPIClient:
    """One instance per camera. Uses digest auth over a persistent httpx client."""

    def __init__(self, cam: CameraConfig, timeout: float = 10.0) -> None:
        self.cam = cam
        self._client = httpx.AsyncClient(
            base_url=cam.base_url,
            auth=httpx.DigestAuth(cam.username, cam.password),
            timeout=timeout,
            verify=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- Barrier ----------
    async def barrier_control(self, mode: CtrlMode) -> ISAPIResponse:
        url = f"/ISAPI/Parking/channels/{self.cam.channel}/barrierGate"
        body = _BARRIER_XML.format(mode=mode)
        try:
            resp = await self._client.put(
                url,
                content=body,
                headers={"Content-Type": "application/xml"},
            )
        except httpx.HTTPError as exc:
            logger.warning("ISAPI %s barrier %s network error: %s", self.cam.name, mode, exc)
            return ISAPIResponse(False, 0, f"network: {exc}", "")
        return _parse_response_status(resp)

    async def barrier_status(self) -> tuple[ISAPIResponse, str | None]:
        url = (
            f"/ISAPI/Parking/channels/{self.cam.channel}"
            f"/barrierGate/barrierGateStatus"
        )
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as exc:
            return ISAPIResponse(False, 0, f"network: {exc}", ""), None
        parsed = _parse_response_status(resp)
        state = None
        if resp.status_code == 200:
            try:
                tree = etree.fromstring(resp.content)
                ns = {"x": "http://www.isapi.org/ver20/XMLSchema"}
                el = tree.find(".//x:barrierGateStatus", ns)
                if el is None:
                    el = tree.find(".//barrierGateStatus")
                if el is not None and el.text is not None:
                    state = _STATUS_MAP.get(int(el.text), el.text)
            except etree.XMLSyntaxError:
                pass
        return parsed, state

    # ---------- Plate allowlist / blocklist (§11.6.5) ----------
    async def add_plate_record(
        self,
        *,
        record_id: int,
        plate_number: str,
        is_allowed: bool,
        plate_color: str = "blue",
        plate_type: str = "92TypeCivil",
    ) -> ISAPIResponse:
        """Add or edit a single plate on the camera's allow/block list.

        ISAPI §11.6.5.3 — request body uses ``LicensePlateInfoList`` (array)
        with mandatory fields ``LicensePlate``, ``plateColor`` and ``plateType``.
        ``record_id`` is the stable per-plate ID (we pass the DB PK, capped to
        16 chars per the ``id`` capability) so re-adds edit the same row
        instead of creating duplicates.
        """
        return await self._upsert_plate_records(
            [
                {
                    "id": str(record_id)[:16],
                    "listType": "allowList" if is_allowed else "blockList",
                    "LicensePlate": plate_number,
                    "plateColor": plate_color,
                    "plateType": plate_type,
                    "operationType": "add",
                }
            ]
        )

    async def add_plate_records_bulk(
        self,
        records: list[dict[str, Any]],
    ) -> ISAPIResponse:
        """Bulk add/edit. Each record must contain id/listType/LicensePlate/plateColor/plateType."""
        return await self._upsert_plate_records(records)

    async def _upsert_plate_records(
        self, records: list[dict[str, Any]]
    ) -> ISAPIResponse:
        url = (
            f"/ISAPI/Traffic/channels/{self.cam.channel}"
            f"/licensePlateAuditData/record?format=json"
        )
        payload = {"LicensePlateInfoList": records}
        try:
            resp = await self._client.put(url, json=payload)
        except httpx.HTTPError as exc:
            logger.warning(
                "ISAPI %s add_plate(s) network error: %s", self.cam.name, exc
            )
            return ISAPIResponse(False, 0, f"network: {exc}", "")
        parsed = _parse_json_response(resp)
        if parsed.ok:
            return parsed

        # Re-add on an existing record fails — retry once as ``modify``.
        if not all(r.get("operationType") == "modify" for r in records):
            for r in records:
                r["operationType"] = "modify"
            try:
                resp2 = await self._client.put(url, json=payload)
                return _parse_json_response(resp2)
            except httpx.HTTPError as exc:
                logger.warning(
                    "ISAPI %s modify_plate(s) network error: %s",
                    self.cam.name,
                    exc,
                )
                return ISAPIResponse(False, 0, f"network: {exc}", "")
        return parsed

    async def delete_plate_record(self, plate_number: str) -> ISAPIResponse:
        """Delete a plate on the camera's list by plate-number (§11.6.5)."""
        return await self.delete_plate_records([plate_number])

    async def delete_plate_records(self, plate_numbers: list[str]) -> ISAPIResponse:
        url = (
            f"/ISAPI/Traffic/channels/{self.cam.channel}"
            f"/DelLicensePlateAuditData?format=json"
        )
        payload = {"licensePlate": plate_numbers}
        try:
            resp = await self._client.put(url, json=payload)
        except httpx.HTTPError as exc:
            logger.warning(
                "ISAPI %s del_plate network error: %s", self.cam.name, exc
            )
            return ISAPIResponse(False, 0, f"network: {exc}", "")
        return _parse_json_response(resp)

    async def search_plate_records(
        self,
        *,
        search_id: str = "shlakbaum-search",
        offset: int = 0,
        max_results: int = 100,
        plate: str | None = None,
    ) -> tuple[ISAPIResponse, list[dict[str, Any]]]:
        """Search the on-device allowlist/blocklist (§11.6.5).

        Returns the parsed response plus the extracted record array.
        """
        url = (
            f"/ISAPI/Traffic/channels/{self.cam.channel}"
            f"/searchLPListAudit?format=json"
        )
        payload: dict[str, Any] = {
            "searchID": search_id,
            "searchResultPosition": offset,
            "maxResults": max_results,
        }
        if plate:
            payload["LicensePlate"] = plate
        try:
            resp = await self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            logger.warning(
                "ISAPI %s search_plate network error: %s", self.cam.name, exc
            )
            return ISAPIResponse(False, 0, f"network: {exc}", ""), []
        parsed = _parse_json_response(resp)
        records: list[dict[str, Any]] = []
        if parsed.ok and resp.text:
            try:
                data = resp.json()
                # The exact wrapper key varies a bit by firmware; cover the
                # common ones.
                for key in (
                    "LicensePlateInfoSearch",
                    "LicensePlateAuditData",
                    "MatchList",
                ):
                    block = data.get(key)
                    if isinstance(block, dict):
                        for sub in (
                            "LicensePlateInfoList",
                            "MatchList",
                            "matchList",
                        ):
                            if isinstance(block.get(sub), list):
                                records = block[sub]
                                break
                    if records:
                        break
                if not records and isinstance(data.get("LicensePlateInfoList"), list):
                    records = data["LicensePlateInfoList"]
            except ValueError:
                pass
        return parsed, records


def _parse_json_response(resp: httpx.Response) -> ISAPIResponse:
    raw = resp.text
    ok = resp.status_code == 200
    message = f"HTTP {resp.status_code}"
    try:
        data = resp.json() if raw else {}
        status_code = data.get("statusCode")
        status_str = data.get("statusString") or data.get("subStatusCode")
        if status_str:
            message = str(status_str)
        if status_code is not None and status_code not in (0, 1):
            ok = False
    except ValueError:
        pass
    return ISAPIResponse(ok=ok, status_code=resp.status_code, message=message, raw=raw)


def _parse_response_status(resp: httpx.Response) -> ISAPIResponse:
    raw = resp.text
    ok = resp.status_code == 200
    message = ""
    if raw:
        try:
            tree = etree.fromstring(resp.content)
            ns = {"x": "http://www.isapi.org/ver20/XMLSchema"}
            status = tree.find(".//x:statusString", ns)
            if status is None:
                status = tree.find(".//statusString")
            if status is not None and status.text:
                message = status.text
            code = tree.find(".//x:statusCode", ns)
            if code is None:
                code = tree.find(".//statusCode")
            if code is not None and code.text and code.text not in ("0", "1"):
                ok = False
        except etree.XMLSyntaxError:
            pass
    if not message:
        message = f"HTTP {resp.status_code}"
    return ISAPIResponse(ok=ok, status_code=resp.status_code, message=message, raw=raw)
