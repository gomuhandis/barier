"""Parse ANPR event pushed by the camera.

Camera posts multipart/form-data with a field `anpr.xml` whose body is the
`<EventNotificationAlert>` — per ISAPI §9.1.4.  The relevant fields we pull:

    <licensePlate>...</licensePlate>
    <plateColor>...</plateColor>
    <confidenceLevel>...</confidenceLevel>
    <dateTime>...</dateTime>
    <ipAddress>...</ipAddress>
    <channelName>...</channelName>
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from lxml import etree

from src.utils.tz import now_local


@dataclass(slots=True)
class ANPREvent:
    plate_number: str
    plate_color: str | None
    confidence: int | None
    event_time: datetime
    device_ip: str | None
    raw_xml: str


def parse_anpr_xml(xml_bytes: bytes) -> ANPREvent | None:
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None

    def text(*paths: str) -> str | None:
        for path in paths:
            el = root.find(path)
            if el is not None and el.text:
                val = el.text.strip()
                if val:
                    return val
        return None

    plate = text(".//{*}licensePlate", ".//licensePlate")
    if not plate or plate.lower() == "noplate":
        return None

    color = text(".//{*}plateColor", ".//plateColor")
    conf_raw = text(".//{*}confidenceLevel", ".//confidenceLevel")
    confidence: int | None = None
    if conf_raw and conf_raw.isdigit():
        confidence = int(conf_raw)

    ip = text(".//{*}ipAddress", ".//ipAddress")

    dt_raw = text(".//{*}dateTime", ".//dateTime")
    event_time = _parse_datetime(dt_raw) if dt_raw else now_local()

    return ANPREvent(
        plate_number=plate.upper(),
        plate_color=color,
        confidence=confidence,
        event_time=event_time,
        device_ip=ip,
        raw_xml=xml_bytes.decode("utf-8", errors="replace"),
    )


def _parse_datetime(raw: str) -> datetime:
    from src.utils.tz import LOCAL_TZ

    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            # Hikvision cameras default to local time when their TZ is set
            # to GMT+5 — assume that for naive timestamps.
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt
    return now_local()
