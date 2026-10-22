"""Mocked configuration projection and active-alarm reads; configuration is not history."""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field

import pytest

from pytvt.platform_sdk import web_crypto
from pytvt.platform_sdk.exceptions import CapabilityNotAvailable, ManagementNotAuthenticatedError, ProtocolError
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
        form = dict(urllib.parse.parse_qsl(body.decode("utf-8"), keep_blank_values=True)) if body else {}
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


def test_configuration_is_not_alarm_history():
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    with pytest.raises(CapabilityNotAvailable, match="configuration"):
        backend.list_alarm_events()
    assert transport.requests == []


def test_configuration_preserves_destinations_and_marks_lossy():
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [
        ok_response(
            '<response><status>success</status><content total="1">'
            '<item id="camera-1"><name>Front door</name><trigger>'
            "<buzzerSwitch>true</buzzerSwitch><tvWall><switch>true</switch>"
            '<item><chl id="wall-1">Main wall</chl></item></tvWall>'
            "<SOP><secret>not-exposed</secret></SOP></trigger></item></content></response>"
        )
    ]
    result = backend.list_alarm_configuration(alarm_type="perimeter")
    assert result["total"] == 1
    assert result["write_supported"] is False
    assert result["duplicate_audio_count"] is None
    row = result["rows"][0]
    assert row["channel_id"] == "camera-1"
    assert row["unexposed_fields"] == ["SOP"]
    assert "wall-1" in str(row["actions"])
    assert "not-exposed" not in str(result)
    assert transport.requests[0][1] == {"type": "perimeter", "pageIndex": "1", "pageSize": "25", "areaId": ""}


@pytest.mark.parametrize(
    "content",
    [
        '<content total="1"><item id="x"><name>not a configuration</name></item></content>',
        '<content><item id="x"><trigger/></item></content>',
        '<content total="1"><item><trigger/></item></content>',
        '<content total="1"></content>',
        '<content total="2"><item id="x"><trigger/></item><item id="x"><trigger/></item></content>',
    ],
)
def test_configuration_rejects_incomplete_response(content):
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response("<response><status>success</status>" + content + "</response>")]
    with pytest.raises(ProtocolError):
        backend.list_alarm_configuration(alarm_type="motion")


@pytest.mark.parametrize(
    "kwargs",
    [{"alarm_type": "invalid"}, {"alarm_type": "motion", "page_size": 100}, {"alarm_type": "motion", "page_index": 0}],
)
def test_configuration_bounds_before_network(kwargs):
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    with pytest.raises(ValueError):
        backend.list_alarm_configuration(**kwargs)
    assert not transport.requests


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
