"""TVT alarm-server frame parsing (push/listener wire format).

TVT NVRs push alarm/heartbeat frames to a listener (the "alarm server") in
several shapes depending on firmware: JSON (modern), an HTTP POST wrapping JSON,
a binary frame (``b"TVT\\0"`` magic + little-endian length + struct payload), or a
length-prefixed JSON frame. This module owns the **vendor wire format** — the
magic bytes, the struct layout, and the alarm-code table — so Django apps don't
hand-parse it (vendor protocol logic lives in pytvt, not in the consumer app).

It is transport- and framework-agnostic: :func:`parse_alarm_frame` takes the raw
bytes of one frame and returns a neutral :class:`ParsedAlarmFrame`. Source IP/port,
receive timestamp, rate-limiting, dedup and persistence are the *caller's* concern
(e.g. a downstream consumer's ``alarm_receiver`` keeps its security/ingest
wrapper and only delegates the byte-level parse here).
"""

from __future__ import annotations

import json
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from typing import Any

__all__ = [
    "TVT_BINARY_MAGIC",
    "TVT_ALARM_CODES",
    "MAX_FRAME_SIZE",
    "ParsedAlarmFrame",
    "parse_alarm_frame",
    "split_alarm_frame",
]

# Binary frame magic: ASCII "TVT" + NUL.
TVT_BINARY_MAGIC = b"\x54\x56\x54\x00"  # "TVT\0"
MAX_FRAME_SIZE = 65536

_UINT32_LEN = struct.calcsize("<I")  # TVT frames use little-endian uint32 fields
_FRAME_HEADER_LEN = 2 * _UINT32_LEN  # magic(4) + length(4)
_PRINTABLE_ASCII = range(32, 127)

# TVT alarm type code → (human-readable code, event category).
TVT_ALARM_CODES: dict[int, tuple[str, str]] = {
    0x01: ("motion", "alarm"),
    0x02: ("sensor", "alarm"),
    0x03: ("video_loss", "video_loss"),
    0x04: ("front_offline", "status_change"),
    # ── AI / smart-analytics events (NET_SDK_N9000_ALARM_TYPE shares this code
    # space with the alarm-server push frames; see device_sdk.AlarmType). These
    # let a listener classify NVR-side AI events — notably face — pushed by the
    # "Alarm Server" trigger action.
    0x05: ("object_removed_left", "ai"),
    0x06: ("video_abnormal", "ai"),
    0x0A: ("tripwire", "ai"),
    0x0B: ("perimeter_intrusion", "ai"),
    0x0C: ("face_detect", "face"),
    0x0D: ("crowd_density", "ai"),
    0x0E: ("people_intrusion", "ai"),
    0x0F: ("people_counting", "ai"),
    0x10: ("face_match", "face"),
    0x11: ("face_match_ipc", "face"),
    0x12: ("tripwire_perimeter_ipc", "ai"),
    0x13: ("target_tracking", "ai"),
    0x14: ("vehicle_plate", "lpr"),
    0x15: ("area_entry", "ai"),
    0x16: ("area_leave", "ai"),
    0x17: ("line_crossing_count", "ai"),
    0x18: ("traffic_statistics", "ai"),
    0x19: ("doorbell", "alarm"),
    0x1A: ("illegal_parking", "ai"),
    0x1B: ("loitering", "ai"),
    0x1C: ("audio_abnormal", "ai"),
    0x61: ("temperature", "ai"),
    0x62: ("fire_detection", "ai"),
    0x41: ("exception", "exception"),
    0x42: ("ip_conflict", "network"),
    0x43: ("disk_io_error", "disk_error"),
    0x44: ("disk_full", "disk_error"),
    0x48: ("net_disconnect", "network"),
    0x49: ("no_disk", "disk_error"),
    0x4B: ("hdd_pull_out", "disk_error"),
    0x65: ("front_online", "status_change"),
    0x66: ("video_loss_off", "status_change"),
    0x68: ("disk_io_error_off", "status_change"),
    0x69: ("disk_full_off", "status_change"),
    0x6A: ("no_disk_off", "status_change"),
}


@dataclass
class ParsedAlarmFrame:
    """Wire-level result of parsing one TVT alarm frame.

    Carries only what the *frame* tells us; transport metadata (source address,
    receive time, raw bytes) belongs to the caller, not the protocol layer.

    ``parse_format`` is one of ``json`` | ``http`` | ``binary`` | ``xml`` |
    ``http_xml`` | ``unknown``.
    ``event_type`` is a coarse category (``heartbeat`` | ``alarm`` | ``exception``
    | ``status_change`` | ``network`` | ``disk_error`` | ``video_loss`` | ``other``).
    ``parsed`` holds the format-specific detail (decoded JSON, or binary
    diagnostics); for ``unknown`` it holds a hex/ascii dump for post-capture
    analysis.
    """

    event_code: str = ""
    event_type: str = "other"
    channel: int | None = None
    device_id: str = ""
    parse_format: str = "unknown"
    parsed: dict[str, Any] = field(default_factory=dict)


def parse_alarm_frame(data: bytes) -> ParsedAlarmFrame:
    """Best-effort parse of a single TVT alarm-server frame.

    Tries, in order: an HTTP POST body, raw JSON, a binary ``TVT\\0`` frame, a
    length-prefixed JSON frame; falls back to an ``unknown`` hex/ascii dump.
    Never raises on malformed input — an unparseable frame yields a
    ``parse_format="unknown"`` result so the caller can capture and triage it.
    """
    event = ParsedAlarmFrame()

    # HTTP POST wrapper (some firmware posts JSON over HTTP; the documented
    # IPC "SendAlarmStatus" push posts XML).
    http_body = _extract_http_body(data)
    if http_body is not None:
        if _looks_like_xml(http_body):
            return _parse_xml_payload(http_body, event, parse_format="http_xml")
        return _parse_json_payload(http_body, event, parse_format="http")

    # Bare JSON.
    stripped = data.strip()
    if stripped and stripped[0:1] in (b"{", b"["):
        return _parse_json_payload(stripped, event, parse_format="json")

    # Bare XML.
    if _looks_like_xml(data):
        return _parse_xml_payload(data, event, parse_format="xml")

    # Binary TVT frame (magic + length + struct payload).
    if data[:4] == TVT_BINARY_MAGIC and len(data) >= _FRAME_HEADER_LEN:
        return _parse_binary_frame(data, event)

    # Length-prefixed JSON (4-byte LE uint32 length + JSON body).
    if len(data) >= _FRAME_HEADER_LEN:
        try:
            declared = struct.unpack_from("<I", data, 0)[0]
            if _UINT32_LEN < declared <= len(data) and declared < MAX_FRAME_SIZE:
                remainder = data[4 : 4 + declared].strip()
                if remainder and remainder[0:1] in (b"{", b"["):
                    return _parse_json_payload(remainder, event, parse_format="json")
        except struct.error:
            pass

    # Unknown — keep full diagnostics for post-capture analysis.
    event.parse_format = "unknown"
    event.event_type = "other"
    event.parsed = {
        "hex": data.hex(),
        "ascii": data.decode("ascii", errors="replace"),
        "length": len(data),
        "head_hex": data[:32].hex(),
        "printable": all(b in _PRINTABLE_ASCII or b in (9, 10, 13) for b in data),
    }
    return event


# ---------------------------------------------------------------------------
# XML: TVTAPI v1.9 (IP media device) alarm-server push
# ---------------------------------------------------------------------------
#
# DOCUMENTED source: "TVTAPI v1.9 - Draft, For IP Media Device", §5.3.1-5.3.4.
# The device POSTs to ``/SendAlarmStatus`` a ``<config xmlns=".../ver10">`` body
# carrying ``<alarmStatusInfo>`` (the "whole alarmStatusInfo element" of
# GetAlarmStatus), ``<dataTime>`` and ``<deviceInfo>``; a heartbeat carries only
# ``<deviceInfo>``. NOT documented, therefore NOT interpreted here:
#   * whether the ``id`` attribute is a recorder channel (it is preserved as-is);
#   * the timezone of ``dataTime`` (preserved as the raw string);
#   * whether a cleared alarm is pushed as ``false`` (reported, not called a
#     recovery);
#   * the NVR (NVMS-9000) push body, which uses a different XML dialect and has
#     not been captured — any other document is returned as ``unknown``.

# Element name -> alarm code. Element names and their meanings are from the
# document's action-name table (ipd = Intruding People Detection, osc = Object
# Status Change, perimeter = Perimeter Environment Assurance, vfd = Video Face
# Detection, avd = Abnormal Video Diagnosis, cdd = Crowd Density Detection,
# cpc = Cross-line People Counting); the codes are the existing
# ``TVT_ALARM_CODES`` names for the same analytics.
XML_STATUS_ELEMENT_CODES: dict[str, str] = {
    "motionAlarm": "motion",
    "perimeterAlarm": "perimeter_intrusion",
    "tripwireAlarm": "tripwire",
    "ipdAlarm": "people_intrusion",
    "oscAlarm": "object_removed_left",
    "cddAlarm": "crowd_density",
    "cpcAlarm": "people_counting",
    "vfdAlarm": "face_detect",
    "avdAlarm": "video_abnormal",
    "sensorAlarmIn": "sensor",
}
_DEVICE_INFO_FIELDS = {
    "deviceName": "device_name",
    "deviceNo.": "device_no",
    "sn": "sn",
    "ipAddress": "ip_address",
    "macAddress": "mac_address",
}
_CATEGORY_BY_CODE = {name: category for name, category in TVT_ALARM_CODES.values()}


def _looks_like_xml(data: bytes) -> bool:
    return data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")


def _local(tag: str) -> str:
    """Element name without its ``{namespace}`` prefix."""
    return tag.rsplit("}", 1)[-1]


def _xml_unknown(raw: bytes, event: ParsedAlarmFrame, reason: str, root: str = "") -> ParsedAlarmFrame:
    event.parse_format = "unknown"
    event.event_type = "other"
    event.event_code = ""
    event.parsed = {
        "unknown_reason": reason,
        "xml_root": root,
        "hex": raw[:MAX_FRAME_SIZE].hex(),
        "length": len(raw),
    }
    return event


def _parse_xml_payload(raw: bytes, event: ParsedAlarmFrame, *, parse_format: str) -> ParsedAlarmFrame:
    """Parse a documented alarm-server XML push; anything else is ``unknown``.

    Never raises. Namespace-agnostic and independent of element order.
    """
    if len(raw) > MAX_FRAME_SIZE:
        return _xml_unknown(raw, event, "xml_too_large")
    # Entity/DTD declarations have no place in a device push and are the vector
    # for entity-expansion attacks: refuse rather than rely on parser limits.
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        return _xml_unknown(raw, event, "xml_doctype_refused")
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, LookupError, ValueError):
        return _xml_unknown(raw, event, "malformed_xml")

    root_name = _local(root.tag)
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    children = {_local(child.tag): child for child in root}
    status = children.get("alarmStatusInfo")
    device_info_el = children.get("deviceInfo")
    if root_name != "config" or (status is None and device_info_el is None):
        return _xml_unknown(raw, event, "unrecognized_xml_document", root_name)

    device_info: dict[str, str] = {}
    if device_info_el is not None:
        for child in device_info_el:
            key = _DEVICE_INFO_FIELDS.get(_local(child.tag))
            if key:
                device_info[key] = (child.text or "").strip()
    data_time_el = children.get("dataTime")
    parsed: dict[str, Any] = {
        "xml_root": root_name,
        "xml_namespace": namespace,
        "device_info": device_info,
        "data_time": (data_time_el.text or "").strip() if data_time_el is not None else "",
        "active_alarms": [],
        "inactive_count": 0,
    }
    event.parse_format = parse_format
    event.device_id = device_info.get("sn") or device_info.get("mac_address", "")
    event.parsed = parsed

    if status is None:
        event.event_type = "heartbeat"
        event.event_code = "heartbeat"
        return event

    active: list[dict[str, str]] = parsed["active_alarms"]
    for element in status:
        name = _local(element.tag)
        code = XML_STATUS_ELEMENT_CODES.get(name, f"xml_{name}")
        items = [item for item in element if _local(item.tag) == "item"]
        for node in items or [element]:
            if (node.text or "").strip().lower() == "true":
                active.append({"element": name, "id": node.get("id", ""), "code": code})
            else:
                parsed["inactive_count"] += 1

    if not active:
        event.event_type = "status_change"
        event.event_code = "alarm_status_inactive"
        return event
    event.event_code = active[0]["code"]
    event.event_type = _CATEGORY_BY_CODE.get(event.event_code, "alarm")
    return event


def split_alarm_frame(frame: ParsedAlarmFrame) -> list[ParsedAlarmFrame]:
    """One frame per active alarm in an XML status push; other frames as-is.

    A documented SendAlarmStatus body can report several active alarms at once,
    while ``ParsedAlarmFrame`` describes one. Consumers that ingest per alarm
    call this; ``parse_alarm_frame`` keeps its single-frame contract.
    """
    active = frame.parsed.get("active_alarms") if frame.parse_format in ("xml", "http_xml") else None
    if not active or len(active) < 2:
        return [frame]
    frames = []
    for alarm in active:
        parsed = dict(frame.parsed)
        parsed["active_alarms"] = [alarm]
        frames.append(
            replace(
                frame,
                event_code=alarm["code"],
                event_type=_CATEGORY_BY_CODE.get(alarm["code"], "alarm"),
                parsed=parsed,
            )
        )
    return frames


def _extract_http_body(data: bytes) -> bytes | None:
    """Return the body of an HTTP POST frame, or ``None`` if it isn't one."""
    if not data.upper().startswith(b"POST "):
        return None
    separator = b"\r\n\r\n"
    idx = data.find(separator)
    if idx < 0:
        return None
    return data[idx + len(separator) :]


def _parse_json_payload(
    raw: bytes,
    event: ParsedAlarmFrame,
    *,
    parse_format: str = "json",
) -> ParsedAlarmFrame:
    """Parse a JSON alarm/heartbeat payload into ``event``."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        event.parse_format = "unknown"
        event.parsed = {"hex": raw.hex()}
        return event

    event.parse_format = parse_format
    event.parsed = payload if isinstance(payload, dict) else {"payload": payload}

    # A non-dict JSON (e.g. a list) has no typed fields to extract.
    if not isinstance(payload, dict):
        event.event_code = "unknown"
        event.event_type = "other"
        return event

    msg_type = str(payload.get("type", payload.get("Type", ""))).lower()
    if msg_type in ("heartbeat", "keepalive", "keep_alive"):
        event.event_type = "heartbeat"
        event.event_code = "heartbeat"
    elif msg_type in ("alarm", "event"):
        alarm_code = payload.get(
            "alarm_type",
            payload.get("alarmType", payload.get("AlarmType", 0)),
        )
        try:
            code_key = int(alarm_code)
        except (TypeError, ValueError):
            code_key = -1
        code_info = TVT_ALARM_CODES.get(code_key, (f"code_{alarm_code}", "alarm"))
        event.event_code = code_info[0]
        event.event_type = code_info[1]
    else:
        event.event_code = msg_type or "unknown"
        event.event_type = "other"

    event.channel = payload.get(
        "channel",
        payload.get("Channel", payload.get("chl", None)),
    )
    event.device_id = str(
        payload.get("device_id", payload.get("deviceId", payload.get("DeviceID", ""))),
    )
    return event


def _parse_binary_frame(data: bytes, event: ParsedAlarmFrame) -> ParsedAlarmFrame:
    """Parse a binary TVT alarm frame (magic + LE length + struct payload)."""
    event.parse_format = "binary"
    payload_len = struct.unpack_from("<I", data, 4)[0]
    payload = data[8 : 8 + payload_len] if 8 + payload_len <= len(data) else data[8:]

    parsed: dict[str, Any] = {
        "magic": data[:4].hex(),
        "declared_length": payload_len,
        "actual_length": len(payload),
        "hex": payload.hex(),
    }

    # Alarm type code: first LE uint32 of the payload.
    if len(payload) >= _UINT32_LEN:
        alarm_code = struct.unpack_from("<I", payload, 0)[0]
        code_info = TVT_ALARM_CODES.get(alarm_code, (f"binary_0x{alarm_code:02x}", "alarm"))
        event.event_code = code_info[0]
        event.event_type = code_info[1]
        parsed["alarm_code"] = alarm_code
        parsed["alarm_name"] = code_info[0]

    # Channel: next LE uint32 (offset 4) when present.
    if len(payload) >= 2 * _UINT32_LEN:
        parsed["channel_raw"] = struct.unpack_from("<I", payload, 4)[0]
        event.channel = parsed["channel_raw"]

    event.parsed = parsed
    return event
