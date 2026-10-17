"""Tests for pytvt.platform_sdk.web_backend status reads (TVT-6).

No network: a fake transport replays scripted envelopes. Covers
``get_server_statuses``/``get_device_statuses``/``get_acs_statuses`` and
``list_acs_systems`` mapping
sanitized status envelopes to DTOs, the confirmed server-status field set
(name/ip/port/type/stateType/module flags), tolerant online/offline and
guid/timestamp derivation on unrecognized shapes, and that an unauthenticated
call raises before any request is sent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qs

import pytest

from pytvt.platform_sdk import web_crypto
from pytvt.platform_sdk.exceptions import ManagementNotAuthenticatedError, ProtocolError
from pytvt.platform_sdk.web_backend import WebManagementBackend
from pytvt.platform_sdk.web_models import PlatformAcsStatus, PlatformAcsSystem, PlatformServerStatus
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
    calls: list[tuple[str, bytes | None]] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> WebHttpResponse:
        self.calls.append((url, body))
        del method, headers, timeout
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


# Device status responses follow the installed UI contract.
def org_response():
    return ok_response(status_list_xml('<item id="root" pId="zero"><name>Root</name></item>'))


def device_page(items, total):
    return ok_response(f'<response><status>success</status><content total="{total}">{items}</content></response>')


def test_device_status_pages_all_roots_and_redacts_credentials():
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    transport.script = [
        ok_response(status_list_xml('<item id="r1" pId="zero"/><item id="child" pId="r1"/><item id="r2" pId="zero"/>')),
        device_page(
            '<item id="a"><stateType>ONLINE</stateType><password>secret</password><userName>admin</userName><unknownSecret>hidden</unknownSecret><lastOnLineTime>2026-07-07 08:30:00</lastOnLineTime></item>',
            2,
        ),
        device_page('<item id="b"><stateType>OFFLINE</stateType></item>', 2),
        device_page('<item id="c"><stateType>unknown</stateType></item>', 1),
    ]
    statuses = backend.get_device_statuses()
    assert [s.device_id for s in statuses] == ["a", "b", "c"]
    assert [s.online for s in statuses] == [True, False, None]
    assert statuses[0].last_seen_at == datetime(2026, 7, 7, 8, 30, tzinfo=timezone.utc)
    assert not {"password", "userName", "unknownSecret"} & statuses[0].raw_data.keys()
    assert transport.script == []
    forms = [
        parse_qs(body.decode(), keep_blank_values=True)
        for url, body in transport.calls
        if url.endswith("getDeviceStatusList")
    ]
    assert [form["areaId"] for form in forms] == [["r1"], ["r1"], ["r2"]]
    assert [form["pageIndex"] for form in forms] == [["1"], ["2"], ["1"]]
    assert all(form["stateType"] == ["ALL"] for form in forms)


@pytest.mark.parametrize(
    "pages",
    [
        [device_page("<item/>", 1)],
        [device_page("", 1)],
        [device_page('<item id="a"/>', 2), device_page('<item id="a"/>', 2)],
        [device_page('<item id="a"/>', 2), device_page('<item id="b"/>', 3)],
        [device_page('<item id="a"/>', 0)],
        [ok_response(status_list_xml('<item id="a"/>'))],
        [ok_response(fail_xml())],
    ],
)
def test_device_status_incomplete_coverage_fails(pages):
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    transport.script = [org_response(), *pages]
    with pytest.raises(ProtocolError):
        backend.get_device_statuses()


@pytest.mark.parametrize("tree", ["", "<item/>", '<item id="a" pId="b"/><item id="b" pId="a"/>'])
def test_device_status_invalid_scope_fails(tree):
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(status_list_xml(tree))]
    with pytest.raises(ProtocolError):
        backend.get_device_statuses()


def test_device_status_empty_authorized_area():
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    transport.script = [org_response(), device_page("", 0)]
    assert backend.get_device_statuses() == []


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


def test_list_acs_systems_maps_fixture_to_typed_dto() -> None:
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    items_xml = (
        "<item><acsGuid>acs-1</acsGuid><name>Palmares Access</name>"
        "<ipAddress>10.20.30.40</ipAddress><servicePort>8010</servicePort></item>"
    )
    transport.script = [ok_response(status_list_xml(items_xml))]

    systems = backend.list_acs_systems()

    assert systems == [
        PlatformAcsSystem(
            guid="acs-1",
            name="Palmares Access",
            ip="10.20.30.40",
            port=8010,
            raw_data={
                "acsGuid": "acs-1",
                "name": "Palmares Access",
                "ipAddress": "10.20.30.40",
                "servicePort": "8010",
            },
        )
    ]


def test_list_acs_systems_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.list_acs_systems()


def test_device_status_request_budget_is_bounded():
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    transport.script = [org_response()] + [device_page(f'<item id="{i}"/>', 101) for i in range(100)]
    with pytest.raises(ProtocolError, match=r"limit|budget"):
        backend.get_device_statuses()


def test_device_status_elapsed_budget_is_bounded(monkeypatch):
    transport = FakeTransport()
    backend = logged_in_backend(transport)
    transport.script = [org_response()]
    ticks = iter([0, 61])
    monkeypatch.setattr("pytvt.platform_sdk.web_backend.time.monotonic", lambda: next(ticks))
    with pytest.raises(ProtocolError, match="budget"):
        backend.get_device_statuses()
