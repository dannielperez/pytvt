"""Tests for pytvt.platform_sdk.web_backend status reads (TVT-6).

No network: a fake transport replays scripted envelopes. Covers
``get_server_statuses``/``get_device_statuses``/``get_acs_statuses`` mapping
sanitized status envelopes to DTOs, the confirmed server-status field set
(name/ip/port/type/stateType/module flags), tolerant online/offline and
guid/timestamp derivation on unrecognized shapes, and that an unauthenticated
call raises before any request is sent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

import pytest

from pytvt.platform_sdk import web_crypto
from pytvt.platform_sdk.exceptions import ManagementNotAuthenticatedError, ProtocolError
from pytvt.platform_sdk.models import DeviceStatus
from pytvt.platform_sdk.web_backend import WebManagementBackend
from pytvt.platform_sdk.web_models import PlatformAcsStatus, PlatformServerStatus
from pytvt.platform_sdk.web_session import WebHttpResponse


def envelope_body(result_xml: str, ret_code: str = "1", ret_msg: str = "success") -> bytes:
    return json.dumps({"retCode": ret_code, "retMsg": ret_msg, "resultXml": result_xml}).encode("utf-8")


def ok_response(result_xml: str) -> WebHttpResponse:
    return WebHttpResponse(status=200, headers=(("Content-Type", "application/json"),), body=envelope_body(result_xml))


def status_list_xml(items_xml: str) -> str:
    return f"<response><status>success</status><content>{items_xml}</content></response>"


def fail_xml(error_code: str = "500") -> str:
    return f"<response><status>fail</status><errorCode>{error_code}</errorCode></response>"


@dataclass
class FakeTransport:
    """Replays a scripted list of responses."""

    script: list[WebHttpResponse] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> WebHttpResponse:
        del method, url, headers, body, timeout
        if not self.script:
            raise AssertionError("unexpected request beyond script")
        return self.script.pop(0)


def logged_in_backend(transport: FakeTransport) -> WebManagementBackend:
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
    return backend


# ── unauthenticated ──────────────────────────────────────────────────


def test_get_server_statuses_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.get_server_statuses()


def test_get_device_statuses_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.get_device_statuses()


def test_get_acs_statuses_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.get_acs_statuses()


# ── server statuses (confirmed field set) ─────────────────────────────


def test_get_server_statuses_maps_confirmed_fields_to_dtos() -> None:
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    items_xml = (
        "<item><name>NVMS-Main</name><ip>10.200.10.201</ip><port>6003</port><type>management</type>"
        "<stateType>online</stateType><lastOnLineTime>2026-07-07 09:00:00</lastOnLineTime>"
        "<lastOffLineTime></lastOffLineTime><ANS>1</ANS><APPSU>0</APPSU></item>"
    )
    transport.script = [ok_response(status_list_xml(items_xml))]

    statuses = backend.get_server_statuses()

    assert len(statuses) == 1
    entry = statuses[0]
    assert isinstance(entry, PlatformServerStatus)
    assert entry.guid == "NVMS-Main"  # no guid-shaped field confirmed; falls back to name
    assert entry.name == "NVMS-Main"
    assert entry.kind == "management"
    assert entry.online is True
    assert entry.raw_data["ip"] == "10.200.10.201"
    assert entry.raw_data["ANS"] == "1"


def test_get_server_statuses_offline_and_unknown_state() -> None:
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    items_xml = (
        "<item><name>Storage-1</name><type>storage</type><stateType>offline</stateType></item>"
        "<item><name>Storage-2</name><type>storage</type><stateType>degraded</stateType></item>"
    )
    transport.script = [ok_response(status_list_xml(items_xml))]

    statuses = backend.get_server_statuses()

    assert statuses[0].online is False
    assert statuses[1].online is None  # unrecognized code -> unknown, not guessed


def test_get_server_statuses_raises_protocol_error_on_failure_status() -> None:
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(fail_xml())]

    with pytest.raises(ProtocolError):
        backend.get_server_statuses()


# ── device statuses (unverified field set, tolerant mapping) ──────────


def test_get_device_statuses_maps_tolerant_fields_to_dtos() -> None:
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    items_xml = (
        "<item><deviceId>dev-1</deviceId><state>online</state>"
        "<lastOnLineTime>2026-07-07 08:30:00</lastOnLineTime></item>"
    )
    transport.script = [ok_response(status_list_xml(items_xml))]

    statuses = backend.get_device_statuses()

    assert len(statuses) == 1
    entry = statuses[0]
    assert isinstance(entry, DeviceStatus)
    assert entry.device_id == "dev-1"
    assert entry.online is True
    assert entry.last_seen_at == datetime(2026, 7, 7, 8, 30, 0)
    assert entry.raw_data["deviceId"] == "dev-1"


def test_get_device_statuses_tolerates_unrecognized_item_shape() -> None:
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    items_xml = "<item><unexpectedField>mystery</unexpectedField></item>"
    transport.script = [ok_response(status_list_xml(items_xml))]

    statuses = backend.get_device_statuses()

    assert len(statuses) == 1
    assert statuses[0].device_id == "0"
    assert statuses[0].online is None
    assert statuses[0].last_seen_at is None


def test_get_device_statuses_unparseable_timestamp_is_none() -> None:
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    items_xml = "<item><id>dev-2</id><lastSeenTime>not-a-timestamp</lastSeenTime></item>"
    transport.script = [ok_response(status_list_xml(items_xml))]

    statuses = backend.get_device_statuses()

    assert statuses[0].last_seen_at is None


# ── ACS statuses (unverified field set, tolerant mapping) ─────────────


def test_get_acs_statuses_maps_tolerant_fields_to_dtos() -> None:
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    items_xml = "<item><guid>acs-1</guid><name>Front Door</name><status>online</status></item>"
    transport.script = [ok_response(status_list_xml(items_xml))]

    statuses = backend.get_acs_statuses()

    assert len(statuses) == 1
    entry = statuses[0]
    assert isinstance(entry, PlatformAcsStatus)
    assert entry.guid == "acs-1"
    assert entry.name == "Front Door"
    assert entry.online is True


def test_get_acs_statuses_empty_list() -> None:
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(status_list_xml(""))]

    assert backend.get_acs_statuses() == []
