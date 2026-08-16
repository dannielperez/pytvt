"""Unit tests for PlatformSDK constants, models, and normalization paths.

These tests do NOT require the live SDK binary or a running management server.
They exercise only the pure-Python parsing / normalization code paths in
``pytvt.platform_sdk.platform_constants``, ``platform_models``, and the
``PlatformSDKClient`` helpers that don't touch ctypes.
"""

from __future__ import annotations

import ctypes as ct
from unittest.mock import MagicMock

import pytest

from pytvt.platform_sdk import platform_constants as pc
from pytvt.platform_sdk.exceptions import (
    CapabilityNotAvailable,
    PlatformOperationError,
    ProtocolError,
    SessionExpired,
)
from pytvt.platform_sdk.platform_backend import (
    PlatformSDKClient,
    PlatformSdkManagementBackend,
    _guid_from_text,
    _PlatSessionState,
    _resource_to_model,
)
from pytvt.platform_sdk.platform_constants import (
    connect_state_is_online,
    connect_state_name,
    decode_area_rights,
    decode_system_rights,
    device_type_name,
    node_type_name,
    redact_sensitive,
    server_type_name,
)
from pytvt.platform_sdk.platform_models import (
    PlatformAlarmZone,
    PlatformResource,
    PlatformServer,
)

# ---------------------------------------------------------------------------
# Constant mappings
# ---------------------------------------------------------------------------


class TestConstantMappings:
    def test_connect_state_known_values(self) -> None:
        assert connect_state_name(pc.PLAT_LOGIN_SUCCESS) == "login_success"
        assert connect_state_name(pc.PLAT_CONNECT_FAIL) == "connect_fail"
        assert connect_state_name(pc.PLAT_NOLOGIN) == "nologin"

    def test_connect_state_unknown_falls_back(self) -> None:
        assert connect_state_name(999) == "unknown"
        assert connect_state_name(-1) == "unknown"

    def test_connect_state_is_online(self) -> None:
        assert connect_state_is_online(pc.PLAT_LOGIN_SUCCESS) is True
        assert connect_state_is_online(pc.PLAT_CONNECT_SUCCESS) is True
        assert connect_state_is_online(pc.PLAT_LOGIN_FAIL) is False
        assert connect_state_is_online(999) is False

    def test_node_type_names(self) -> None:
        assert node_type_name(pc.NODETYPE_AREA) == "area"
        assert node_type_name(pc.NODETYPE_DEVICE) == "device"
        assert node_type_name(pc.NODETYPE_CHANNEL) == "channel"
        assert node_type_name(42) == "unknown"

    def test_device_type_names(self) -> None:
        assert device_type_name(pc.DEVTYPE_NVR) == "nvr"
        assert device_type_name(pc.DEVTYPE_IPC) == "ipc"
        assert device_type_name(pc.DEVTYPE_ONVIF) == "onvif"
        assert device_type_name(123) == "unknown"

    def test_server_type_fallback_preserves_value(self) -> None:
        assert server_type_name(pc.SERVER_TYPE_MANAGEMENT) == "management"
        # Unknown values preserve the integer in the name for operator visibility.
        assert server_type_name(42) == "unknown_42"
        assert server_type_name(-1) == "unknown_-1"


class TestRightsDecoding:
    def test_decode_single_system_right(self) -> None:
        names = decode_system_rights(pc.PLAT_SYSTEM_RIGHT_LOG)
        assert names == ["log"]

    def test_decode_combined_system_rights(self) -> None:
        mask = pc.PLAT_SYSTEM_RIGHT_LOG | pc.PLAT_SYSTEM_RIGHT_TVWALL_CONFIG | pc.PLAT_SYSTEM_RIGHT_RESOURCE_CONFIG
        names = decode_system_rights(mask)
        assert set(names) == {"log", "tvwall_config", "resource_config"}

    def test_decode_system_rights_reports_unknown_bits(self) -> None:
        unknown_bit = 1 << 29
        names = decode_system_rights(pc.PLAT_SYSTEM_RIGHT_LOG | unknown_bit)
        assert "log" in names
        assert any(n.startswith("unknown_bit_0x") for n in names)

    def test_decode_area_rights(self) -> None:
        mask = pc.PLAT_AREA_RIGHT_LIVE | pc.PLAT_AREA_RIGHT_PLAYBACK
        names = decode_area_rights(mask)
        assert set(names) == {"live", "playback"}


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedactSensitive:
    def test_redacts_password_but_preserves_key(self) -> None:
        out = redact_sensitive({"username": "admin", "password": "secret"})
        assert out["username"] == "admin"
        assert out["password"] == "<redacted>"

    def test_empty_password_stays_empty(self) -> None:
        out = redact_sensitive({"password": ""})
        assert out["password"] == ""

    def test_case_insensitive(self) -> None:
        out = redact_sensitive({"Password": "x", "API_KEY": "y"})
        assert out["Password"] == "<redacted>"
        assert out["API_KEY"] == "<redacted>"

    def test_non_string_keys_untouched(self) -> None:
        out = redact_sensitive({1: "v", "token": "t"})
        assert out[1] == "v"
        assert out["token"] == "<redacted>"


# ---------------------------------------------------------------------------
# Resource normalization
# ---------------------------------------------------------------------------


class TestResourceNormalization:
    def _raw(self, **overrides: object) -> dict[str, object]:
        base = {
            "ulNodeID": 42,
            "ulParentID": 1,
            "guidNodeID": "deadbeef-0000-0000-0000000000000000",
            "guidParentID": "00000000-0000-0000-0000000000000000",
            "szName": "Lobby Camera",
            "nNodeType": pc.NODETYPE_CHANNEL,
            "nDevType": pc.DEVTYPE_IPC,
            "nOnline": 1,
            "nChlCount": 0,
            "nChlNO": 3,
            "szIp": "10.0.0.5",
            "bisSupportFaceMatch": True,
            "_opt_type": "create",
        }
        base.update(overrides)
        return base

    def test_channel_normalization(self) -> None:
        model = _resource_to_model(self._raw())
        assert isinstance(model, PlatformResource)
        assert model.node_id == 42
        assert model.name == "Lobby Camera"
        assert model.node_type_name == "channel"
        assert model.device_type_name == "ipc"
        assert model.online is True
        assert model.ip == "10.0.0.5"
        assert model.supports_face_match is True

    def test_area_has_no_online(self) -> None:
        model = _resource_to_model(self._raw(nNodeType=pc.NODETYPE_AREA, nOnline=0))
        # Areas don't report online — must be None to avoid misleading "offline".
        assert model.online is None

    def test_device_offline(self) -> None:
        model = _resource_to_model(self._raw(nNodeType=pc.NODETYPE_DEVICE, nOnline=0))
        assert model.online is False

    def test_unknown_types_fall_back(self) -> None:
        model = _resource_to_model(self._raw(nNodeType=99, nDevType=55))
        assert model.node_type_name == "unknown"
        assert model.device_type_name == "unknown"


class _FakeCaptureFunction:
    def __init__(self, image: bytes, *, succeeds: bool = True, returned: int | None = None) -> None:
        self.image = image
        self.succeeds = succeeds
        self.returned = len(image) if returned is None else returned
        self.argtypes = None
        self.restype = None
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> bool:
        self.calls.append(args)
        if self.succeeds:
            _login_id, _guid, buffer, buffer_size, returned_ptr = args
            ct.memmove(buffer, self.image, min(len(self.image), int(buffer_size)))
            ct.cast(returned_ptr, ct.POINTER(ct.c_int))[0] = self.returned
        return self.succeeds


class _FakeCaptureNs:
    def __init__(self, function: _FakeCaptureFunction, *, last_error: int | None = 0) -> None:
        self.function = function
        self.last_error = last_error
        self.bind_calls: list[tuple[str, dict[str, object]]] = []

    def bind_function(self, capability: str, **kwargs: object) -> _FakeCaptureFunction:
        self.bind_calls.append((capability, kwargs))
        self.function.argtypes = kwargs.get("argtypes")
        self.function.restype = kwargs.get("restype")
        return self.function

    def call_get_last_error(self) -> int | None:
        return self.last_error


class TestPlatformJpegCapture:
    JPEG = b"\xff\xd8platform-jpeg\xff\xd9"
    GUID = "5c6b7612-011b-0008-aafd-8d694053d89b"

    def _client(self, function: _FakeCaptureFunction, *, last_error: int | None = 0) -> PlatformSDKClient:
        client = PlatformSDKClient(_FakeCaptureNs(function, last_error=last_error), "1.2.3.4", 6003)
        client._login_id = 73
        client._authenticated = True
        return client

    def test_guid_parser_matches_sdk_field_layout(self) -> None:
        guid = _guid_from_text("{5C6B7612-011B-0008-AAFD-8D694053D89B}")
        assert guid.Data1 == 0x5C6B7612
        assert guid.Data2 == 0x011B
        assert guid.Data3 == 0x0008
        assert bytes(guid.Data4) == bytes.fromhex("aafd8d694053d89b")

    def test_capture_reuses_authenticated_login_and_returns_bounded_jpeg(self) -> None:
        function = _FakeCaptureFunction(self.JPEG)
        client = self._client(function)

        assert client.capture_jpeg(self.GUID, max_image_bytes=1024) == self.JPEG

        login_id, guid, _buffer, buffer_size, _returned = function.calls[0]
        assert login_id == 73
        assert guid.Data1 == 0x5C6B7612
        assert buffer_size == 1024
        assert client._ns.bind_calls[0][0] == "capture_jpeg_data"

    @pytest.mark.parametrize("value", [0, -1, True, 16 * 1024 * 1024 + 1])
    def test_capture_rejects_unsafe_buffer_sizes(self, value: object) -> None:
        with pytest.raises(ValueError, match="max_image_bytes"):
            self._client(_FakeCaptureFunction(self.JPEG)).capture_jpeg(
                self.GUID,
                max_image_bytes=value,  # type: ignore[arg-type]
            )

    def test_capture_rejects_invalid_guid_before_sdk_call(self) -> None:
        function = _FakeCaptureFunction(self.JPEG)
        with pytest.raises(ValueError, match="channel_guid"):
            self._client(function).capture_jpeg("not-a-guid")
        assert function.calls == []

    def test_capture_surfaces_sdk_failure_without_credentials(self) -> None:
        with pytest.raises(PlatformOperationError, match=r"node_offline \[12\]") as excinfo:
            self._client(_FakeCaptureFunction(b"", succeeds=False), last_error=12).capture_jpeg(self.GUID)
        assert excinfo.value.code == pc.PLAT_ERROR_NODE_NET_OFFLINE
        assert excinfo.value.invalidates_session is False

    @pytest.mark.parametrize(
        "code",
        [
            pc.PLAT_ERROR_NODE_NET_DISCONNECT,
            pc.PLAT_ERROR_NODE_NET_OFFLINE,
            pc.PLAT_ERROR_INVALID_PARAM,
            pc.PLAT_ERROR_NO_SUPPORT,
            pc.PLAT_ERROR_DEVICE_BUSY,
            pc.PLAT_ERROR_BUFFER_TOO_SMALL,
        ],
    )
    def test_reviewed_operation_failures_preserve_platform_login(self, code: int) -> None:
        assert PlatformOperationError("capture failed", code).invalidates_session is False

    @pytest.mark.parametrize(
        "code",
        [
            None,
            999,
            pc.PLAT_ERROR_NOINIT,
            pc.PLAT_ERROR_NO_LOGIN,
            pc.PLAT_ERROR_PASSWORD,
            pc.PLAT_ERROR_FAIL_CONNECT,
            pc.PLAT_ERROR_TIMEOUT,
        ],
    )
    def test_session_or_ambiguous_failures_invalidate_platform_login(self, code: int | None) -> None:
        assert PlatformOperationError("capture failed", code).invalidates_session is True

    @pytest.mark.parametrize(
        ("image", "returned"),
        [
            (b"not-jpeg", None),
            (JPEG, 0),
            (JPEG, 1025),
        ],
    )
    def test_capture_rejects_invalid_sdk_output(self, image: bytes, returned: int | None) -> None:
        with pytest.raises(ProtocolError):
            self._client(_FakeCaptureFunction(image, returned=returned)).capture_jpeg(
                self.GUID,
                max_image_bytes=1024,
            )


# ---------------------------------------------------------------------------
# PlatformSDKClient list/tree/find helpers (mocked session)
# ---------------------------------------------------------------------------


class TestPlatformSDKClientHelpers:
    def _make_client_with_state(self, state: _PlatSessionState) -> PlatformSDKClient:
        client = PlatformSDKClient(ns_lib=MagicMock(), host="1.2.3.4", port=6003)
        client._state = state
        client._authenticated = True
        return client

    def _add_node(
        self,
        state: _PlatSessionState,
        *,
        node_id: int,
        parent_id: int,
        name: str,
        node_type: int,
        guid: str = "deadbeef-0000-0000-0000000000000000",
    ) -> None:
        state.all_nodes.append(
            {
                "_opt_type": "create",
                "ulNodeID": node_id,
                "ulParentID": parent_id,
                "guidNodeID": guid,
                "guidParentID": "00000000-0000-0000-0000000000000000",
                "szName": name,
                "nNodeType": node_type,
                "nDevType": pc.DEVTYPE_NVR,
                "nOnline": 1,
                "nChlCount": 0,
                "nChlNO": -1,
                "szIp": "10.0.0.1",
                "bisSupportFaceMatch": False,
                "usSensorInNum": 0,
                "usAlarmOutNum": 0,
            }
        )

    def test_list_areas_devices_channels_filter(self) -> None:
        state = _PlatSessionState()
        self._add_node(state, node_id=1, parent_id=0, name="Root", node_type=pc.NODETYPE_AREA)
        self._add_node(state, node_id=2, parent_id=1, name="NVR1", node_type=pc.NODETYPE_DEVICE)
        self._add_node(state, node_id=3, parent_id=2, name="Ch1", node_type=pc.NODETYPE_CHANNEL)
        client = self._make_client_with_state(state)

        assert [r.name for r in client.list_areas()] == ["Root"]
        assert [r.name for r in client.list_devices_normalized()] == ["NVR1"]
        assert [r.name for r in client.list_channels_normalized()] == ["Ch1"]

    def test_list_resources_tree_parent_child(self) -> None:
        state = _PlatSessionState()
        self._add_node(state, node_id=1, parent_id=0, name="Root", node_type=pc.NODETYPE_AREA)
        self._add_node(state, node_id=2, parent_id=1, name="NVR1", node_type=pc.NODETYPE_DEVICE)
        self._add_node(state, node_id=3, parent_id=2, name="Ch1", node_type=pc.NODETYPE_CHANNEL)
        tree = self._make_client_with_state(state).list_resources_tree()

        # Root is the only top-level node; it has one device child; device has one channel.
        assert len(tree) == 1
        root = tree[0]
        assert root["name"] == "Root"
        assert len(root["children"]) == 1
        assert root["children"][0]["name"] == "NVR1"
        assert len(root["children"][0]["children"]) == 1
        assert root["children"][0]["children"][0]["name"] == "Ch1"

    def test_find_resource_by_name_and_guid(self) -> None:
        state = _PlatSessionState()
        self._add_node(
            state,
            node_id=11,
            parent_id=0,
            name="Lobby Camera",
            node_type=pc.NODETYPE_CHANNEL,
            guid="cafef00d-1234-5678-0011223344556677",
        )
        client = self._make_client_with_state(state)

        hits = client.find_resource_by_name("lobby")
        assert len(hits) == 1 and hits[0].node_id == 11

        found = client.find_resource_by_guid("cafef00d-1234-5678-0011223344556677")
        assert found is not None and found.node_id == 11

        assert client.find_resource_by_guid("nope") is None

    def test_list_servers_deduplicates_across_buckets(self) -> None:
        state = _PlatSessionState()
        state.ai_servers["aaaa-aaaa"] = {"guid": "aaaa-aaaa", "name": "AI-1"}
        state.storage_servers["bbbb-bbbb"] = {"guid": "bbbb-bbbb", "name": "STO-1"}
        state.alarm_hosts["cccc-cccc"] = {
            "guid": "cccc-cccc",
            "name": "AH-1",
            "ip_or_sn": "10.0.0.9",
            "port": 4001,
        }
        # A transfer server appears only via server_connect_events
        state.server_connect_events.append(
            {"server_guid": "dddd-dddd", "server_type": 5, "connect_state": pc.PLAT_LOGIN_SUCCESS}
        )
        # A null-GUID = management server heartbeat
        state.server_connect_events.append(
            {
                "server_guid": "00000000-0000-0000-0000000000000000",
                "server_type": 0,
                "connect_state": pc.PLAT_LOGIN_SUCCESS,
            }
        )
        client = self._make_client_with_state(state)

        servers = client.list_servers()
        by_kind = {s.kind: s for s in servers}
        assert {"ai", "storage", "alarm_host", "unknown", "management"} <= set(by_kind)
        assert by_kind["alarm_host"].ip == "10.0.0.9"
        assert by_kind["alarm_host"].port == 4001
        assert by_kind["management"].name == "Management Server"

        # list_transfer_servers re-labels the unknown server as "transfer".
        transfers = client.list_transfer_servers()
        assert [t.guid for t in transfers] == ["dddd-dddd"]
        assert transfers[0].kind == "transfer"
        assert transfers[0].connect_state_name == "login_success"
        assert transfers[0].online is True

        assert [s.guid for s in client.list_storage_servers()] == ["bbbb-bbbb"]
        assert [s.guid for s in client.list_intelligent_analysis_servers()] == ["aaaa-aaaa"]
        assert [s.guid for s in client.list_alarm_servers()] == ["cccc-cccc"]
        # These are placeholders (empty list by design)
        assert client.list_access_servers() == []
        assert client.list_tv_wall_servers() == []

    def test_list_alarm_zones(self) -> None:
        state = _PlatSessionState()
        state.alarm_zones["zone-1"] = {
            "guid": "zone-1",
            "host_guid": "host-1",
            "name": "Front Door",
            "zone_type": 2,
        }
        client = self._make_client_with_state(state)
        zones = client.list_alarm_zones()
        assert len(zones) == 1
        assert isinstance(zones[0], PlatformAlarmZone)
        assert zones[0].name == "Front Door"
        assert zones[0].host_guid == "host-1"


# ---------------------------------------------------------------------------
# Live online-state merge (update_state supersedes create-time nOnline)
# ---------------------------------------------------------------------------


class TestLiveOnlineMerge:
    """Regression for the create-time `.online` bug (FIELD_LEARNINGS.md).

    The PlatformSDK emits each node once via NODEOPTTYPE_CREATE (carrying a
    create-time `nOnline`, typically 0 at enumeration), then streams live
    status as separate `update_state` notifications (`nConnState` keyed by
    `ulNodeID`).  `list_resources_normalized()` must merge the latest
    `update_state` so `.online` reflects live state, not the stale create flag.
    """

    def _client(self, state: _PlatSessionState) -> PlatformSDKClient:
        client = PlatformSDKClient(ns_lib=MagicMock(), host="1.2.3.4", port=6003)
        client._state = state
        client._authenticated = True
        return client

    def _add_create(
        self,
        state: _PlatSessionState,
        *,
        node_id: int,
        name: str,
        online: int,
        node_type: int = pc.NODETYPE_DEVICE,
    ) -> None:
        state.all_nodes.append(
            {
                "_opt_type": "create",
                "ulNodeID": node_id,
                "ulParentID": 1,
                "guidNodeID": "deadbeef-0000-0000-0000000000000000",
                "guidParentID": "00000000-0000-0000-0000000000000000",
                "szName": name,
                "nNodeType": node_type,
                "nDevType": pc.DEVTYPE_NVR,
                "nOnline": online,
                "nChlCount": 0,
                "nChlNO": -1,
                "szIp": "10.0.0.1",
                "bisSupportFaceMatch": False,
                "usSensorInNum": 0,
                "usAlarmOutNum": 0,
            }
        )

    def _add_update_state(self, state: _PlatSessionState, *, node_id: int, conn_state: int) -> None:
        state.all_nodes.append(
            {
                "_opt_type": "update_state",
                "ulNodeID": node_id,
                "nConnState": conn_state,
                "szName": "",
                "szIp": "",
            }
        )

    def test_update_state_brings_node_online(self) -> None:
        # Create-time reads offline; a later update_state reports it online.
        state = _PlatSessionState()
        self._add_create(state, node_id=2, name="NVR1", online=0)
        self._add_update_state(state, node_id=2, conn_state=1)
        [model] = self._client(state).list_resources_normalized()
        assert model.online is True
        assert model.raw_data["_live_conn_state"] == 1

    def test_update_state_takes_node_offline(self) -> None:
        # Create-time reads online; a later update_state reports it offline.
        state = _PlatSessionState()
        self._add_create(state, node_id=2, name="NVR1", online=1)
        self._add_update_state(state, node_id=2, conn_state=0)
        [model] = self._client(state).list_resources_normalized()
        assert model.online is False

    def test_latest_update_state_wins(self) -> None:
        # Several updates arrive in order; the last one is authoritative.
        state = _PlatSessionState()
        self._add_create(state, node_id=2, name="NVR1", online=0)
        self._add_update_state(state, node_id=2, conn_state=1)
        self._add_update_state(state, node_id=2, conn_state=0)
        self._add_update_state(state, node_id=2, conn_state=1)
        [model] = self._client(state).list_resources_normalized()
        assert model.online is True

    def test_node_without_update_state_keeps_create_value(self) -> None:
        # No update_state for this node → create-time flag is preserved verbatim.
        state = _PlatSessionState()
        self._add_create(state, node_id=2, name="NVR1", online=1)
        [model] = self._client(state).list_resources_normalized()
        assert model.online is True
        assert "_live_conn_state" not in model.raw_data

    def test_golden_fleet_create_all_offline_then_live(self) -> None:
        # GOLDEN: enumeration reads all 3 NVRs offline; live updates show 2 up,
        # 1 down.  The pre-fix normalizer reported 0 online (create-time only).
        state = _PlatSessionState()
        self._add_create(state, node_id=10, name="NVR-A", online=0)
        self._add_create(state, node_id=11, name="NVR-B", online=0)
        self._add_create(state, node_id=12, name="NVR-C", online=0)
        self._add_update_state(state, node_id=10, conn_state=1)
        self._add_update_state(state, node_id=11, conn_state=1)
        self._add_update_state(state, node_id=12, conn_state=0)

        models = {m.node_id: m for m in self._client(state).list_resources_normalized()}
        online_count = sum(1 for m in models.values() if m.online)
        assert online_count == 2
        assert models[10].online is True
        assert models[11].online is True
        assert models[12].online is False

    def test_update_state_does_not_force_area_online(self) -> None:
        # Areas never report online; a stray update_state must not flip it to a bool.
        state = _PlatSessionState()
        self._add_create(state, node_id=1, name="Root", online=0, node_type=pc.NODETYPE_AREA)
        self._add_update_state(state, node_id=1, conn_state=1)
        [model] = self._client(state).list_resources_normalized()
        assert model.online is None


# ---------------------------------------------------------------------------
# Write scaffolding and unreachable APIs
# ---------------------------------------------------------------------------


class TestWriteScaffoldsAndUnreachable:
    def _client(self) -> PlatformSDKClient:
        client = PlatformSDKClient(ns_lib=MagicMock(), host="1.2.3.4", port=6003)
        client._state = _PlatSessionState()
        client._authenticated = True
        return client

    def test_create_user_dry_run_returns_plan(self) -> None:
        out = self._client().create_user(name="foo")
        assert out == {
            "dry_run": True,
            "operation": "create_user",
            "payload": {"name": "foo"},
        }

    def test_create_user_live_raises(self) -> None:
        with pytest.raises(CapabilityNotAvailable):
            self._client().create_user(dry_run=False, name="foo")

    @pytest.mark.parametrize(
        "method",
        [
            "list_users",
            "list_permission_groups",
            "list_logs",
            "list_alarm_logs",
            "list_operation_logs",
            "list_exception_logs",
            "list_tv_walls",
            "list_alarm_events",
            "list_active_alarms",
        ],
    )
    def test_unreachable_apis_raise_capability_not_available(self, method: str) -> None:
        client = self._client()
        with pytest.raises(CapabilityNotAvailable):
            getattr(client, method)()


class TestUnauthenticatedGuards:
    def test_list_resources_requires_session(self) -> None:
        client = PlatformSDKClient(ns_lib=MagicMock(), host="1.2.3.4", port=6003)
        with pytest.raises(SessionExpired):
            client.list_resources()
        with pytest.raises(SessionExpired):
            client.list_servers()
        with pytest.raises(SessionExpired):
            client.list_alarm_zones()


class _FakeNsLib:
    """Namespaced-library double whose bound functions return real values.

    A ``MagicMock`` would make ``int(get_last_error())`` raise and silently
    render every reason "unavailable" — which is the exact bug under test.
    """

    def __init__(self, *, login_id: int = -1, last_error: int | None = 8) -> None:
        self._login_id = login_id
        self._last_error = last_error

    def call_init(self) -> bool:
        return True

    def call_get_last_error(self) -> int | None:
        return self._last_error

    def bind_function(self, capability: str, **_kwargs: object):
        if capability == "login":
            return lambda *_a: self._login_id
        if capability == "set_message_callback":
            return lambda *_a: True
        return lambda *_a: True


class TestPlatErrorCodes:
    def test_known_codes_get_semantic_names(self) -> None:
        assert pc.plat_error_name(pc.PLAT_ERROR_USER_LOCKED) == "user_locked"
        assert pc.plat_error_name(pc.PLAT_ERROR_PASSWORD) == "password_mismatch"
        assert pc.plat_error_name(pc.PLAT_ERROR_NO_USER) == "no_such_user"

    def test_unknown_code_falls_back_without_raising(self) -> None:
        assert pc.plat_error_name(4242) == "unknown"

    def test_credential_errors_are_separated_from_transport_errors(self) -> None:
        assert pc.plat_error_is_credential(pc.PLAT_ERROR_USER_LOCKED)
        assert pc.plat_error_is_credential(pc.PLAT_ERROR_PASSWORD)
        assert not pc.plat_error_is_credential(pc.PLAT_ERROR_FAIL_CONNECT)
        assert not pc.plat_error_is_credential(pc.PLAT_SUCCESS)


class TestLoginFailureReporting:
    """A bare -1 must never be the whole story (PlatErrorDef.h codes)."""

    def _login(self, **kwargs) -> str:
        client = PlatformSDKClient(ns_lib=_FakeNsLib(**kwargs), host="1.2.3.4", port=6003)
        with pytest.raises(Exception) as excinfo:
            client.login("admin", "secret")
        return str(excinfo.value)

    def _login_error(self, **kwargs):
        client = PlatformSDKClient(ns_lib=_FakeNsLib(**kwargs), host="1.2.3.4", port=6003)
        with pytest.raises(Exception) as excinfo:
            client.login("admin", "secret")
        return excinfo.value

    def test_locked_account_is_named_in_the_error(self) -> None:
        message = self._login(last_error=pc.PLAT_ERROR_USER_LOCKED)
        assert "user_locked" in message
        assert "[8]" in message
        assert "account-side" in message

    def test_wrong_password_is_distinguishable_from_a_locked_account(self) -> None:
        message = self._login(last_error=pc.PLAT_ERROR_PASSWORD)
        assert "password_mismatch" in message
        assert "user_locked" not in message

    def test_transport_failure_is_not_labelled_account_side(self) -> None:
        message = self._login(last_error=pc.PLAT_ERROR_FAIL_CONNECT)
        assert "connect_failed" in message
        assert "account-side" not in message

    def test_missing_error_symbol_degrades_without_masking_the_failure(self) -> None:
        message = self._login(last_error=None)
        assert "reason unavailable" in message
        # the original diagnostic must survive
        assert "invalid login ID -1" in message
        assert "1.2.3.4:6003" in message

    def test_login_error_exposes_credential_rejection_without_message_parsing(self) -> None:
        credential_error = self._login_error(last_error=pc.PLAT_ERROR_PASSWORD)
        transport_error = self._login_error(last_error=pc.PLAT_ERROR_FAIL_CONNECT)
        unknown_error = self._login_error(last_error=None)

        assert credential_error.credential_rejected is True
        assert transport_error.credential_rejected is False
        assert unknown_error.credential_rejected is None


class TestLastErrorSymbolWiring:
    """The PLAT namespace must actually resolve Plat_GetLastErrorEx.

    A duplicate ``get_last_error`` key in the symbol dict would be silently
    overwritten and the reason would degrade to "unavailable" on every real
    login failure -- invisible to any test that fakes the library.
    """

    def test_plat_namespace_maps_the_last_error_symbol(self) -> None:
        from pytvt.platform_sdk.sdk_namespace import (
            CAPABILITY_SYMBOLS,
            SdkNamespace,
            capabilities_for_namespace,
        )

        assert CAPABILITY_SYMBOLS["get_last_error"][SdkNamespace.PLAT] == "Plat_GetLastErrorEx"
        # and it survives into the per-namespace view the loader actually uses
        assert capabilities_for_namespace(SdkNamespace.PLAT)["get_last_error"] == ("Plat_GetLastErrorEx")

    def test_net_sdk_mapping_is_not_clobbered(self) -> None:
        from pytvt.platform_sdk.sdk_namespace import CAPABILITY_SYMBOLS, SdkNamespace

        assert CAPABILITY_SYMBOLS["get_last_error"][SdkNamespace.NET_SDK] == "NET_SDK_GetLastError"

    def test_platform_capture_maps_the_exported_symbol(self) -> None:
        from pytvt.platform_sdk.sdk_namespace import CAPABILITY_SYMBOLS, SdkNamespace

        assert CAPABILITY_SYMBOLS["capture_jpeg_data"][SdkNamespace.PLAT] == "Plat_CaptureJpgPictureDataEx"
