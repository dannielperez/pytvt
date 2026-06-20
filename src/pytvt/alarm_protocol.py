"""TVT alarm-server frame parsing (push/listener wire format).

TVT NVRs push alarm/heartbeat frames to a listener (the "alarm server") in
several shapes depending on firmware: JSON (modern), an HTTP POST wrapping JSON,
a binary frame (``b"TVT\\0"`` magic + little-endian length + struct payload), or a
length-prefixed JSON frame. This module owns the **vendor wire format** — the
magic bytes, the struct layout, and the alarm-code table — so Django apps don't
hand-parse it (CLAUDE.md §4: vendor protocol logic lives in pytvt).

It is transport- and framework-agnostic: :func:`parse_alarm_frame` takes the raw
bytes of one frame and returns a neutral :class:`ParsedAlarmFrame`. Source IP/port,
receive timestamp, rate-limiting, dedup and persistence are the *caller's* concern
(e.g. downstream application' ``alarm_receiver`` keeps its security/ingest wrapper and only
delegates the byte-level parse here).
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "TVT_BINARY_MAGIC",
    "TVT_ALARM_CODES",
    "MAX_FRAME_SIZE",
    "ParsedAlarmFrame",
    "parse_alarm_frame",
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

    ``parse_format`` is one of ``json`` | ``http`` | ``binary`` | ``unknown``.
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

    # HTTP POST wrapper (some firmware posts JSON over HTTP).
    http_body = _extract_http_body(data)
    if http_body is not None:
        return _parse_json_payload(http_body, event, parse_format="http")

    # Bare JSON.
    stripped = data.strip()
    if stripped and stripped[0:1] in (b"{", b"["):
        return _parse_json_payload(stripped, event, parse_format="json")

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
