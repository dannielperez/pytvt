"""Tests for pytvt.platform_sdk.web_backend authority reads (TVT-8).

No network: a fake transport replays scripted envelopes. Covers
``list_users``/``list_roles``/``list_permission_groups`` mapping sanitized
authority envelopes to DTOs, endpoint selection, password omission, permission
mask decode, raw payload redaction, and unauthenticated guards.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field

import pytest

from pytvt.platform_sdk import web_crypto
from pytvt.platform_sdk.exceptions import ManagementNotAuthenticatedError, ProtocolError
from pytvt.platform_sdk.platform_models import PlatformPermissionGroup, PlatformUser
from pytvt.platform_sdk.web_backend import WebManagementBackend
from pytvt.platform_sdk.web_session import WebHttpResponse


def envelope_body(result_xml: str, ret_code: str = "1", ret_msg: str = "success") -> bytes:
    return json.dumps({"retCode": ret_code, "retMsg": ret_msg, "resultXml": result_xml}).encode("utf-8")


def ok_response(result_xml: str) -> WebHttpResponse:
    return WebHttpResponse(status=200, headers=(("Content-Type", "application/json"),), body=envelope_body(result_xml))


def authority_list_xml(items_xml: str) -> str:
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


# -- unauthenticated --------------------------------------------------


def test_list_users_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.list_users()


def test_list_roles_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.list_roles()


def test_list_permission_groups_requires_login() -> None:
    backend = WebManagementBackend("nvms.example")
    with pytest.raises(ManagementNotAuthenticatedError):
        backend.list_permission_groups()


# -- users ------------------------------------------------------------


def test_list_users_maps_sanitized_envelope_to_dtos_without_password() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    items_xml = (
        "<item><userId>7</userId><userName>admin</userName><enabled>true</enabled>"
        "<authGroup>Administrator</authGroup><macBindStatus>1</macBindStatus>"
        "<macAddress>00:11:22:33:44:55</macAddress><email>admin@example.test</email>"
        "<description>Main operator</description><password>secret-value</password></item>"
    )
    transport.script = [ok_response(authority_list_xml(items_xml))]

    users = backend.list_users()

    assert len(users) == 1
    user = users[0]
    assert isinstance(user, PlatformUser)
    assert user.username == "admin"
    assert user.enabled is True
    assert user.permission_group == "Administrator"
    assert user.mac_bind_status is True
    assert user.mac_address == "00:11:22:33:44:55"
    assert user.email == "admin@example.test"
    assert user.description == "Main operator"
    assert user.user_id == 7
    assert user.has_password is True
    assert "password" not in user.as_dict()
    assert not hasattr(user, "password")

    (url, form) = transport.requests[0]
    assert url.endswith("/service/User/getUserList")
    assert form == {}


def test_list_users_tolerates_unrecognized_shape() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(authority_list_xml("<item><unexpectedField>mystery</unexpectedField></item>"))]

    users = backend.list_users()

    assert len(users) == 1
    assert users[0].username == ""
    assert users[0].enabled is False
    assert users[0].user_id == 0
    assert users[0].has_password is False


def test_list_users_raises_protocol_error_on_failure_status() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(fail_xml("536870955"))]

    with pytest.raises(ProtocolError):
        backend.list_users()


# -- roles / permission groups ---------------------------------------


def test_list_roles_maps_permission_masks_to_group_dtos() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    items_xml = (
        "<item><roleId>3</roleId><roleName>Operators</roleName>"
        "<systemAndOperateRright>17</systemAndOperateRright><areaRight>3</areaRight></item>"
    )
    transport.script = [ok_response(authority_list_xml(items_xml))]

    roles = backend.list_roles()

    assert len(roles) == 1
    role = roles[0]
    assert isinstance(role, PlatformPermissionGroup)
    assert role.group_id == 3
    assert role.name == "Operators"
    assert role.system_permission_mask == 17
    assert role.system_permissions == ["resource_config", "user_rights_config"]
    assert role.area_permission_mask == 3
    assert role.area_permissions == ["live", "ptz"]

    (url, form) = transport.requests[0]
    assert url.endswith("/service/User/getRoleList")
    assert form == {}


def test_list_permission_groups_redacts_sensitive_raw_fields() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    items_xml = (
        "<item><groupId>4</groupId><groupName>Audit</groupName>"
        "<systemPermission>512</systemPermission><areaPermission>128</areaPermission>"
        "<token>secret-token</token></item>"
    )
    transport.script = [ok_response(authority_list_xml(items_xml))]

    groups = backend.list_permission_groups()

    assert len(groups) == 1
    group = groups[0]
    assert group.group_id == 4
    assert group.name == "Audit"
    assert group.system_permissions == ["log"]
    assert group.area_permissions == ["view_log"]
    assert group.raw_data["token"] == "<redacted>"

    (url, form) = transport.requests[0]
    assert url.endswith("/service/User/getAuthGroupList")
    assert form == {}


def test_list_permission_groups_uses_index_fallback_for_missing_id() -> None:
    transport = RecordingTransport()
    backend = logged_in_backend(transport)
    transport.script = [ok_response(authority_list_xml("<item><groupName>Fallback</groupName></item>"))]

    groups = backend.list_permission_groups()

    assert groups[0].group_id == 0
    assert groups[0].name == "Fallback"
