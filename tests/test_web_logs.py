"""Tests for pytvt.platform_sdk.web_backend log reads (TVT-9).

No network: a fake transport replays scripted envelopes. Covers
``list_operation_logs``/``list_status_logs`` mapping sanitized log envelopes
to ``PlatformLogEntry`` DTOs, pagination form fields, dictionary decode of
the log-type code, tolerant field mapping on an unrecognized item shape, and
that an unauthenticated call raises before any request is sent.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field

import pytest

from pytvt.platform_sdk import web_crypto
from pytvt.platform_sdk.exceptions import ManagementNotAuthenticatedError, ProtocolError
from pytvt.platform_sdk.web_backend import WebManagementBackend
from pytvt.platform_sdk.web_session import WebHttpResponse


def envelope_body(result_xml: str, ret_code: str = "1", ret_msg: str = "success") -> bytes:
    return json.dumps({"retCode": ret_code, "retMsg": ret_msg, "resultXml": result_xml}).encode("utf-8")


def ok_response(result_xml: str) -> WebHttpResponse:
    return WebHttpResponse(status=200, headers=(("Content-Type", "application/json"),), body=envelope_body(result_xml))


def log_list_xml(items_xml: str) -> str:
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


def test_list_operation_logs_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.list_operation_logs()


def test_list_status_logs_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.list_status_logs()


# ── operation logs ───────────────────────────────────────────────────


def test_list_operation_logs_maps_sanitized_envelope_to_dtos() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    items_xml = (
        "<item><id>1</id><type>100</type><content>admin logged in</content>"
        "<time>2026-07-07 10:00:00</time><user>admin</user></item>"
        "<item><id>2</id><type>200</type><content>config changed</content>"
        "<time>2026-07-07 10:05:00</time><user>operator</user></item>"
    )
    dictionary_xml = (
        "<item><type>100</type><name>Login</name></item><item><type>200</type><name>ConfigChange</name></item>"
    )
    transport.script = [
        ok_response(log_list_xml(items_xml)),
        ok_response(log_list_xml(dictionary_xml)),
    ]

    logs = backend.list_operation_logs()

    assert [entry.log_id for entry in logs] == ["1", "2"]
    assert logs[0].log_type == "Login"
    assert logs[0].message == "admin logged in"
    assert logs[0].occurred_at == "2026-07-07 10:00:00"
    assert logs[0].operator == "admin"
    assert logs[1].log_type == "ConfigChange"
    assert logs[0].raw_data["id"] == "1"


def test_list_operation_logs_sends_pagination_and_date_range() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(log_list_xml("")), ok_response(log_list_xml(""))]

    backend.list_operation_logs(start_time="2026-07-01", end_time="2026-07-07", page_index=2, page_size=50)

    (url, form), _dictionary_call = transport.requests
    assert url.endswith("/service/SystemMaintain/getLog")
    assert form == {
        "pageIndex": "2",
        "pageSize": "50",
        "startTime": "2026-07-01",
        "endTime": "2026-07-07",
    }


def test_list_operation_logs_defaults_omit_date_range() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(log_list_xml("")), ok_response(log_list_xml(""))]

    backend.list_operation_logs()

    (_url, form), _dictionary_call = transport.requests
    assert form == {"pageIndex": "1", "pageSize": "100"}


def test_list_operation_logs_raises_protocol_error_on_failure_status() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(fail_xml("536870955"))]

    with pytest.raises(ProtocolError):
        backend.list_operation_logs()


def test_list_operation_logs_tolerates_unrecognized_item_shape() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    items_xml = "<item><unexpectedField>mystery</unexpectedField></item>"
    transport.script = [ok_response(log_list_xml(items_xml)), ok_response(log_list_xml(""))]

    logs = backend.list_operation_logs()

    assert len(logs) == 1
    assert logs[0].log_id == "0"
    assert logs[0].log_type == ""
    assert logs[0].message == ""
    assert logs[0].raw_data == {"unexpectedField": "mystery"}


def test_list_operation_logs_dictionary_lookup_failure_is_non_fatal() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    items_xml = "<item><id>1</id><type>999</type><content>unknown event</content></item>"
    transport.script = [ok_response(log_list_xml(items_xml)), ok_response(fail_xml())]

    logs = backend.list_operation_logs()

    assert logs[0].log_type == "999"  # no dictionary entry for 999 -> falls back to the raw code


# ── status logs ──────────────────────────────────────────────────────


def test_list_status_logs_maps_sanitized_envelope_to_dtos() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    items_xml = "<item><id>9</id><logType>state</logType><occurTime>2026-07-07 11:00:00</occurTime></item>"
    transport.script = [ok_response(log_list_xml(items_xml)), ok_response(log_list_xml(""))]

    logs = backend.list_status_logs()

    assert len(logs) == 1
    assert logs[0].log_id == "9"
    assert logs[0].log_type == "state"
    assert logs[0].occurred_at == "2026-07-07 11:00:00"


def test_list_status_logs_sends_pagination() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(log_list_xml("")), ok_response(log_list_xml(""))]

    backend.list_status_logs(page_index=3, page_size=25)

    (url, form), _dictionary_call = transport.requests
    assert url.endswith("/service/SystemStatus/getStateLog")
    assert form == {"pageIndex": "3", "pageSize": "25"}
