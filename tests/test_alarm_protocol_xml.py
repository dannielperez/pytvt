"""XML alarm-server pushes (TVTAPI v1.9 SendAlarmStatus / heartbeat).

Fixtures are the vendor's DOCUMENTED examples (see fixtures/alarm_server/README.md),
not live captures. Behaviour the document does not specify is asserted as
"preserved, not interpreted" — e.g. the ``id`` attribute is kept verbatim and is
NOT treated as a recorder channel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pytvt import parse_alarm_frame
from pytvt.alarm_protocol import MAX_FRAME_SIZE, split_alarm_frame

FIXTURES = Path(__file__).parent / "fixtures" / "alarm_server"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _post(body: bytes, path: bytes = b"/SendAlarmStatus") -> bytes:
    return (
        b"POST "
        + path
        + b" HTTP/1.1\r\nHost: receiver\r\nContent-Type: application/xml\r\n"
        + b"Content-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    )


# ---------------------------------------------------------------------------
# Documented alarm push
# ---------------------------------------------------------------------------


def test_documented_send_alarm_status_over_http_is_parsed():
    frame = parse_alarm_frame(_post(_fixture("ipc_v1_9_send_alarm_status_motion.xml")))

    assert frame.parse_format == "http_xml"
    assert frame.event_code == "motion"
    assert frame.event_type == "alarm"
    # The document does not say the id is a channel; it is preserved, not mapped.
    assert frame.channel is None
    assert frame.parsed["active_alarms"] == [
        {"element": "motionAlarm", "id": "1", "code": "motion"},
    ]
    assert frame.parsed["data_time"] == "2017-06-20 10:30:21"
    assert frame.parsed["device_info"]["sn"] == "N563F0159MNK"
    assert frame.parsed["device_info"]["mac_address"] == "78-24-AF-44-89-01"
    assert frame.parsed["xml_root"] == "config"
    assert frame.parsed["xml_namespace"] == "http://www.ipc.com/ver10"
    # The serial number is the strongest stable identity the payload carries.
    assert frame.device_id == "N563F0159MNK"


def test_bare_xml_body_is_parsed_too():
    frame = parse_alarm_frame(_fixture("ipc_v1_9_send_alarm_status_motion.xml"))
    assert frame.parse_format == "xml"
    assert frame.event_code == "motion"


def test_documented_heartbeat_is_a_heartbeat():
    frame = parse_alarm_frame(_post(_fixture("ipc_v1_9_heartbeat.xml")))

    assert frame.parse_format == "http_xml"
    assert frame.event_type == "heartbeat"
    assert frame.event_code == "heartbeat"
    assert frame.device_id == "N563F0159MNK"


@pytest.mark.parametrize(
    ("element", "code"),
    [
        ("motionAlarm", "motion"),
        ("perimeterAlarm", "perimeter_intrusion"),
        ("tripwireAlarm", "tripwire"),
        ("ipdAlarm", "people_intrusion"),
        ("oscAlarm", "object_removed_left"),
        ("cddAlarm", "crowd_density"),
        ("cpcAlarm", "people_counting"),
        ("vfdAlarm", "face_detect"),
        ("avdAlarm", "video_abnormal"),
    ],
)
def test_documented_status_elements_map_to_alarm_codes(element, code):
    body = (
        f'<config xmlns="http://www.ipc.com/ver10"><alarmStatusInfo>'
        f'<{element} type="boolean">true</{element}></alarmStatusInfo></config>'
    ).encode()
    frame = parse_alarm_frame(body)
    assert frame.event_code == code
    assert frame.parsed["active_alarms"][0]["element"] == element


def test_full_status_reports_every_active_alarm_in_document_order():
    frame = parse_alarm_frame(_fixture("ipc_v1_9_alarm_status_full.xml"))

    assert [a["code"] for a in frame.parsed["active_alarms"]] == [
        "motion",
        "sensor",
        "tripwire",
    ]
    assert frame.parsed["active_alarms"][1] == {
        "element": "sensorAlarmIn",
        "id": "3",
        "code": "sensor",
    }
    # The frame's own fields describe the first active alarm.
    assert frame.event_code == "motion"
    assert frame.parsed["inactive_count"] == 11  # 1 motion + 3 sensor items + 7 booleans


def test_split_returns_one_frame_per_active_alarm():
    frames = split_alarm_frame(parse_alarm_frame(_fixture("ipc_v1_9_alarm_status_full.xml")))

    assert [f.event_code for f in frames] == ["motion", "sensor", "tripwire"]
    assert all(f.parse_format == "xml" for f in frames)
    assert frames[2].parsed["active_alarms"] == [
        {"element": "tripwireAlarm", "id": "", "code": "tripwire"},
    ]
    assert frames[2].device_id == "N563F0159MNK"


def test_split_leaves_non_status_frames_alone():
    heartbeat = parse_alarm_frame(_fixture("ipc_v1_9_heartbeat.xml"))
    assert split_alarm_frame(heartbeat) == [heartbeat]


def test_all_false_status_is_reported_not_guessed_as_recovery():
    """The document does not say whether a cleared alarm is pushed as false."""
    body = (
        b'<config xmlns="http://www.ipc.com/ver10"><alarmStatusInfo>'
        b'<tripwireAlarm type="boolean">false</tripwireAlarm></alarmStatusInfo></config>'
    )
    frame = parse_alarm_frame(body)
    assert frame.event_code == "alarm_status_inactive"
    assert frame.event_type == "status_change"
    assert frame.parsed["active_alarms"] == []


def test_unknown_status_element_is_kept_and_labelled():
    body = (
        b'<config xmlns="http://www.ipc.com/ver10"><alarmStatusInfo>'
        b"<newShinyAlarm>true</newShinyAlarm></alarmStatusInfo></config>"
    )
    frame = parse_alarm_frame(body)
    assert frame.event_code == "xml_newShinyAlarm"
    assert frame.event_type == "alarm"
    assert frame.parsed["active_alarms"][0]["code"] == "xml_newShinyAlarm"


def test_element_order_and_whitespace_do_not_matter():
    body = (
        b"<config><deviceInfo><sn>X1</sn></deviceInfo>"
        b"<alarmStatusInfo><tripwireAlarm> TRUE </tripwireAlarm></alarmStatusInfo>"
        b"<dataTime>2020-01-01 00:00:00</dataTime></config>"
    )
    frame = parse_alarm_frame(body)
    assert frame.event_code == "tripwire"
    assert frame.device_id == "X1"


# ---------------------------------------------------------------------------
# Not the documented shape: preserved for capture, never guessed
# ---------------------------------------------------------------------------


def test_unrecognized_xml_dialect_is_unknown_with_diagnostics():
    """e.g. an NVR (NVMS-9000) payload, whose push format is not yet captured."""
    body = b'<?xml version="1.0"?><request version="1.0" clientType="WEB"><x/></request>'
    frame = parse_alarm_frame(_post(body))
    assert frame.parse_format == "unknown"
    assert frame.event_code == ""
    assert frame.parsed["xml_root"] == "request"
    assert frame.parsed["unknown_reason"] == "unrecognized_xml_document"
    assert bytes.fromhex(frame.parsed["hex"]) == body


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"<config><alarmStatusInfo><motionAlarm>true", "malformed_xml"),
        (b"<config><alarmStatusInfo></config>", "malformed_xml"),
        (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><config>&a;</config>',
            "xml_doctype_refused",
        ),
        (b'<?xml version="1.0" encoding="x-unknown-enc"?><config/>', "malformed_xml"),
    ],
)
def test_hostile_or_broken_xml_never_raises(body, reason):
    frame = parse_alarm_frame(body)
    assert frame.parse_format == "unknown"
    assert frame.parsed["unknown_reason"] == reason


def test_oversized_xml_is_not_parsed():
    body = b"<config>" + b"<pad/>" * (MAX_FRAME_SIZE // 6 + 1) + b"</config>"
    frame = parse_alarm_frame(body)
    assert frame.parse_format == "unknown"
    assert frame.parsed["unknown_reason"] == "xml_too_large"


def test_status_with_missing_value_is_not_active():
    body = b"<config><alarmStatusInfo><tripwireAlarm/></alarmStatusInfo></config>"
    frame = parse_alarm_frame(body)
    assert frame.event_code == "alarm_status_inactive"


def test_json_path_is_unchanged():
    frame = parse_alarm_frame(b'{"type":"alarm","alarm_type":10,"channel":2}')
    assert frame.parse_format == "json"
    assert frame.event_code == "tripwire"
    assert split_alarm_frame(frame) == [frame]


# ---------------------------------------------------------------------------
# NVR push — CAPTURED (live, 2026-10-12): heartbeat only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    ["nvr_captured_heartbeat_http.bin", "nvr_captured_heartbeat_decl_http.bin"],
)
def test_captured_nvr_heartbeat_is_a_heartbeat(fixture):
    """Real NVR heartbeat: <DeviceInfo> root, no <config>, DeviceName/SN casing."""
    frame = parse_alarm_frame(_fixture(fixture))

    assert frame.parse_format == "http_xml"
    assert frame.event_type == "heartbeat"
    assert frame.event_code == "heartbeat"
    assert frame.device_id == "TESTSERIAL0"
    assert frame.parsed["xml_root"] == "DeviceInfo"
    assert frame.parsed["device_info"] == {
        "device_name": "Example NVR",
        "device_no": "1",
        "sn": "TESTSERIAL0",
        "ip_address": "192.0.2.10",
        "mac_address": "00:00:5E:00:53:01",
    }
    assert frame.parsed["active_alarms"] == []


def test_device_info_with_unexpected_children_is_still_only_a_heartbeat():
    """A <DeviceInfo> root never yields an alarm — alarm bodies are uncaptured."""
    frame = parse_alarm_frame(
        b"<DeviceInfo><SN>X</SN><motionAlarm>true</motionAlarm></DeviceInfo>",
    )
    assert frame.event_type == "heartbeat"
    assert frame.parsed["active_alarms"] == []
