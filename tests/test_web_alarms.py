"""Tests for pytvt.platform_sdk.web_backend alarm reads (TVT-5).

No network: a fake transport replays scripted envelopes. Covers
``list_alarm_events``/``list_active_alarms`` mapping sanitized alarm
envelopes to ``PlatformAlarmRecord`` DTOs, pagination form contents for the
event list, an unpaginated request for the active-alarm node list, an empty
list, tolerant field mapping on an unrecognized item shape, and that an
unauthenticated call raises before any request is sent.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field

import pytest

from pytvt.platform_sdk import web_crypto
from pytvt.platform_sdk.exceptions import ManagementNotAuthenticatedError, ProtocolError
from pytvt.platform_sdk.web_backend import WebManagementBackend
from pytvt.platform_sdk.web_models import PlatformAlarmRecord
from pytvt.platform_sdk.web_session import WebHttpResponse


def envelope_body(result_xml: str, ret_code: str = "1", ret_msg: str = "success") -> bytes:
    return json.dumps({"retCode": ret_code, "retMsg": ret_msg, "resultXml": result_xml}).encode("utf-8")


def ok_response(result_xml: str) -> WebHttpResponse:
    return WebHttpResponse(status=200, headers=(("Content-Type", "application/json"),), body=envelope_body(result_xml))


def alarm_list_xml(items_xml: str) -> str:
    return f"<response><status>success</status><content>{items_xml}</content></response>"


def fail_xml(error_code: str = "500") -> str:
    return f"<response><status>fail</status><errorCode>{error_code}</errorCode></response>"


@dataclass
class RecordingTransport:
    """Replays a scripted list of responses and records every request made."""

    script: list[WebHttpResponse] = field(default_factory=list)
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> WebHttpResponse:
        del method, headers, timeout
        form = dict(urllib.parse.parse_qsl(body.decode("utf-8"))) if body else {}
        self.requests.append((url, form))
        if not self.script:
            raise AssertionError("unexpected request beyond script")
        return self.script.pop(0)


def logged_in_backend(transport: RecordingTransport) -> WebManagementBackend:
    backend = WebManagementBackend("nvms.example", transport=transport)
    nonce = "ABCDEFGHIJKLMNOPQRST"
    req_xml = (
        "<response><status>success</status><content>"
        f"<nonce>{nonce}</nonce><sessionId>{{SESS-1}}</sessionId><token>tok-1</token>"
        "</content></response>"
    )
    encrypted_auth_id = web_crypto.aes_ecb_pkcs7_encrypt("auth-9f3", web_crypto._md5_hex("hunter2")[:16])
    do_xml = (
        "<response><status>success</status><content>"
        f"<type>admin</type><authId>{encrypted_auth_id}</authId><userId>7</userId>"
        "</content></response>"
    )
    transport.script[:0] = [ok_response(req_xml), ok_response(do_xml)]
    backend.login("operator", "hunter2")
    transport.requests.clear()
    return backend


# ── unauthenticated ──────────────────────────────────────────────────


def test_list_alarm_events_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.list_alarm_events()


def test_list_active_alarms_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.list_active_alarms()


# ── alarm events (Alarm/getAlarmInfoList) ─────────────────────────────


def test_list_alarm_events_maps_sanitized_envelope_to_dtos() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    items_xml = (
        "<item><alarmId>1</alarmId><type>motion</type><deviceId>dev-1</deviceId>"
        "<channelId>ch-1</channelId><time>2026-07-07 12:00:00</time></item>"
        "<item><alarmId>2</alarmId><type>videoLoss</type><deviceId>dev-2</deviceId>"
        "<channelId>ch-2</channelId><time>2026-07-07 12:05:00</time></item>"
    )
    transport.script = [ok_response(alarm_list_xml(items_xml))]

    alarms = backend.list_alarm_events()

    assert [entry.alarm_id for entry in alarms] == ["1", "2"]
    assert isinstance(alarms[0], PlatformAlarmRecord)
    assert alarms[0].alarm_type == "motion"
    assert alarms[0].device_id == "dev-1"
    assert alarms[0].channel_id == "ch-1"
    assert alarms[0].occurred_at == "2026-07-07 12:00:00"
    assert alarms[1].alarm_type == "videoLoss"
    assert alarms[0].raw_data["alarmId"] == "1"


def test_list_alarm_events_sends_pagination() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(alarm_list_xml(""))]

    backend.list_alarm_events(page_index=3, page_size=25)

    (url, form) = transport.requests[0]
    assert url.endswith("/service/Alarm/getAlarmInfoList")
    assert form == {"pageIndex": "3", "pageSize": "25"}


def test_list_alarm_events_defaults() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(alarm_list_xml(""))]

    backend.list_alarm_events()

    (_url, form) = transport.requests[0]
    assert form == {"pageIndex": "1", "pageSize": "100"}


def test_list_alarm_events_empty_list() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(alarm_list_xml(""))]

    assert backend.list_alarm_events() == []


def test_list_alarm_events_raises_protocol_error_on_failure_status() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(fail_xml("536870955"))]

    with pytest.raises(ProtocolError):
        backend.list_alarm_events()


def test_list_alarm_events_tolerates_unrecognized_item_shape() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    items_xml = "<item><unexpectedField>mystery</unexpectedField></item>"
    transport.script = [ok_response(alarm_list_xml(items_xml))]

    alarms = backend.list_alarm_events()

    assert len(alarms) == 1
    assert alarms[0].alarm_id == "0"
    assert alarms[0].alarm_type == ""
    assert alarms[0].raw_data == {"unexpectedField": "mystery"}


# ── active alarms (Alarm/getNodeList) ─────────────────────────────────


def test_list_active_alarms_maps_sanitized_envelope_to_dtos() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    items_xml = "<item><id>zone-1</id><type>zone</type></item>"
    transport.script = [ok_response(alarm_list_xml(items_xml))]

    alarms = backend.list_active_alarms()

    assert len(alarms) == 1
    assert alarms[0].alarm_id == "zone-1"
    assert alarms[0].alarm_type == "zone"


def test_list_active_alarms_sends_no_form_body() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(alarm_list_xml(""))]

    backend.list_active_alarms()

    (url, form) = transport.requests[0]
    assert url.endswith("/service/Alarm/getNodeList")
    assert form == {}


def test_list_active_alarms_empty_list() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(alarm_list_xml(""))]

    assert backend.list_active_alarms() == []
