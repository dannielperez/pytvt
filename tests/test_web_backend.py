"""Tests for pytvt.platform_sdk.web_backend + web_models (TVT-3).

No network: a fake transport replays the same sanitized reqLogin/doLogin
envelopes used by test_web_session.py (TVT-2). Covers the ABC contract,
real login via the fake transport, unimplemented reads raising
CapabilityNotAvailable, diagnostics/context shape, and that the new DTOs
carry no secrets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from pytvt.platform_sdk import web_crypto
from pytvt.platform_sdk.base import BaseManagementBackend
from pytvt.platform_sdk.exceptions import CapabilityNotAvailable, ManagementAuthError
from pytvt.platform_sdk.web_backend import WebManagementBackend
from pytvt.platform_sdk.web_models import (
    PlatformAcsStatus,
    PlatformAlarmRecord,
    PlatformDeviceStatus,
    PlatformLogEntry,
    PlatformServerStatus,
)
from pytvt.platform_sdk.web_session import WebHttpResponse

USERNAME = "operator"
PASSWORD = "hunter2"
NONCE = "ABCDEFGHIJKLMNOPQRST"  # >= 16 chars, prefix is the AES key
REAL_AUTH_ID = "auth-9f3"


# ── fake transport (mirrors test_web_session.py) ────────────────────────


def envelope_body(result_xml: str, ret_code: str = "1", ret_msg: str = "success") -> bytes:
    return json.dumps({"retCode": ret_code, "retMsg": ret_msg, "resultXml": result_xml}).encode("utf-8")


def req_login_xml(nonce: str = NONCE, session_id: str = "{SESS-1}", token: str = "tok-1") -> str:
    return (
        "<response><status>success</status><content>"
        f"<nonce>{nonce}</nonce><sessionId>{session_id}</sessionId><token>{token}</token>"
        "</content></response>"
    )


def do_login_xml() -> str:
    encrypted = web_crypto.aes_ecb_pkcs7_encrypt(REAL_AUTH_ID, web_crypto._md5_hex(PASSWORD)[:16])
    return (
        "<response><status>success</status><content>"
        f"<type>admin</type><authId>{encrypted}</authId><userId>7</userId><showEmap>false</showEmap>"
        "</content></response>"
    )


def fail_xml(error_code: str) -> str:
    return f"<response><status>fail</status><errorCode>{error_code}</errorCode></response>"


def ok_response(result_xml: str, *, set_cookie: str | None = None) -> WebHttpResponse:
    headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),)
    if set_cookie is not None:
        headers = (*headers, ("Set-Cookie", set_cookie))
    return WebHttpResponse(status=200, headers=headers, body=envelope_body(result_xml))


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


def successful_login_transport() -> FakeTransport:
    return FakeTransport(
        script=[
            ok_response(req_login_xml()),
            ok_response(do_login_xml(), set_cookie="JSESSIONID=abc123; Path=/; HttpOnly"),
        ]
    )


# ── contract ──────────────────────────────────────────────────────────


def test_web_management_backend_implements_the_abc() -> None:
    backend = WebManagementBackend("nvms.example")
    assert isinstance(backend, BaseManagementBackend)


def test_load_sdk_always_true() -> None:
    assert WebManagementBackend("nvms.example").load_sdk() is True


def test_supports_flags() -> None:
    backend = WebManagementBackend("nvms.example")
    assert backend.supports_sdk() is False
    assert backend.supports_native_protocol() is False


def test_close_without_login_is_a_noop() -> None:
    backend = WebManagementBackend("nvms.example")
    backend.close()  # must not raise


# ── login ─────────────────────────────────────────────────────────────


def test_login_succeeds_via_fake_transport() -> None:
    transport = successful_login_transport()
    backend = WebManagementBackend("nvms.example", transport=transport)

    result = backend.login(USERNAME, PASSWORD)

    assert result is True
    assert backend._session is not None
    assert backend._session.authenticated


def test_login_failure_raises_auth_error() -> None:
    transport = FakeTransport(script=[ok_response(fail_xml("536870947"))])
    backend = WebManagementBackend("nvms.example", transport=transport)

    with pytest.raises(ManagementAuthError):
        backend.login(USERNAME, PASSWORD)


def test_close_after_login_forgets_the_session() -> None:
    transport = successful_login_transport()
    backend = WebManagementBackend("nvms.example", transport=transport)
    backend.login(USERNAME, PASSWORD)

    backend.close()

    assert backend._session is None


# ── unimplemented reads ───────────────────────────────────────────────


def test_unimplemented_reads_raise_capability_not_available() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(CapabilityNotAvailable):
        backend.get_server_info()
    with pytest.raises(CapabilityNotAvailable):
        backend.list_devices()
    with pytest.raises(CapabilityNotAvailable):
        backend.list_channels()
    with pytest.raises(CapabilityNotAvailable):
        backend.subscribe_alarms()


# ── context / diagnostics ─────────────────────────────────────────────


def test_get_context_reports_web_runtime_and_real_login_support() -> None:
    context = WebManagementBackend("nvms.example").get_context()

    assert context.platform.runtime_kind == "web"
    assert context.capabilities.supports_login is True
    assert context.capabilities.supports_management_server_login is True
    assert context.capabilities.supports_device_enumeration is False


def test_diagnostics_shape() -> None:
    diagnostics = WebManagementBackend("nvms.example").diagnostics()

    assert diagnostics["backend"] == "web"
    assert diagnostics["supports_login"] is True
    assert diagnostics["supports_device_enumeration"] is False
    assert "capability_evidence" in diagnostics


# ── DTOs (web_models) ─────────────────────────────────────────────────


def test_dto_as_dict_excludes_raw_data_and_carries_no_secrets() -> None:
    dtos = [
        PlatformAlarmRecord(alarm_id="1", raw_data={"secret": "nope"}),
        PlatformServerStatus(guid="g1", raw_data={"secret": "nope"}),
        PlatformDeviceStatus(device_id="d1", raw_data={"secret": "nope"}),
        PlatformAcsStatus(guid="a1", raw_data={"secret": "nope"}),
        PlatformLogEntry(log_id="l1", raw_data={"secret": "nope"}),
    ]
    for dto in dtos:
        payload = dto.as_dict()
        assert "raw_data" not in payload
        assert "secret" not in payload
        assert "password" not in payload
