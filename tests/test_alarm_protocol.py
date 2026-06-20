"""Tests for the TVT alarm-server frame parser (pytvt.alarm_protocol)."""

from __future__ import annotations

import json
import struct

from pytvt import ParsedAlarmFrame, parse_alarm_frame
from pytvt.alarm_protocol import TVT_BINARY_MAGIC


def _binary_frame(payload: bytes) -> bytes:
    return TVT_BINARY_MAGIC + struct.pack("<I", len(payload)) + payload


def test_json_alarm_maps_code_and_fields():
    raw = json.dumps({"type": "alarm", "alarm_type": 0x01, "channel": 3, "device_id": "dev-9"}).encode()
    f = parse_alarm_frame(raw)
    assert f.parse_format == "json"
    assert f.event_code == "motion" and f.event_type == "alarm"
    assert f.channel == 3 and f.device_id == "dev-9"


def test_json_heartbeat():
    f = parse_alarm_frame(b'{"type":"heartbeat"}')
    assert f.parse_format == "json"
    assert f.event_type == "heartbeat" and f.event_code == "heartbeat"


def test_json_unknown_alarm_code_falls_back_to_code_label():
    f = parse_alarm_frame(b'{"type":"alarm","alarm_type":999}')
    assert f.event_code == "code_999" and f.event_type == "alarm"


def test_http_post_wrapper_is_unwrapped():
    body = b'{"type":"alarm","alarm_type":2}'
    raw = b"POST /alarm HTTP/1.1\r\nHost: x\r\nContent-Length: 1\r\n\r\n" + body
    f = parse_alarm_frame(raw)
    assert f.parse_format == "http"
    assert f.event_code == "sensor" and f.event_type == "alarm"


def test_binary_frame_extracts_code_and_channel():
    payload = struct.pack("<I", 0x03) + struct.pack("<I", 7)  # video_loss, channel 7
    f = parse_alarm_frame(_binary_frame(payload))
    assert f.parse_format == "binary"
    assert f.event_code == "video_loss" and f.event_type == "video_loss"
    assert f.channel == 7
    assert f.parsed["alarm_code"] == 0x03 and f.parsed["channel_raw"] == 7


def test_binary_unknown_code_uses_hex_label():
    payload = struct.pack("<I", 0xAB) + struct.pack("<I", 1)
    f = parse_alarm_frame(_binary_frame(payload))
    assert f.event_code == "binary_0xab" and f.event_type == "alarm"


def test_length_prefixed_json():
    body = b'{"type":"event","alarmType":68}'  # 0x44 = disk_full
    raw = struct.pack("<I", len(body)) + body
    f = parse_alarm_frame(raw)
    assert f.parse_format == "json"
    assert f.event_code == "disk_full" and f.event_type == "disk_error"


def test_unknown_frame_is_diagnostic_not_raised():
    raw = b"\xde\xad\xbe\xef garbage frame"
    f = parse_alarm_frame(raw)
    assert f.parse_format == "unknown"
    assert f.event_type == "other"
    assert f.parsed["length"] == len(raw)
    assert "hex" in f.parsed and "ascii" in f.parsed


def test_malformed_json_is_unknown_not_raised():
    f = parse_alarm_frame(b'{"type": "alarm", bad json')
    # leading "{" routes to JSON; invalid JSON degrades to unknown, never raises
    assert f.parse_format == "unknown"


def test_returns_dataclass_with_wire_only_fields():
    f = parse_alarm_frame(b'{"type":"heartbeat"}')
    assert isinstance(f, ParsedAlarmFrame)
    # wire fields only — no transport metadata leaked into the protocol layer
    names = set(f.__dataclass_fields__)
    assert names == {"event_code", "event_type", "channel", "device_id", "parse_format", "parsed"}
    assert "source_ip" not in names and "timestamp" not in names
