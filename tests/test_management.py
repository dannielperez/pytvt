"""Interface-level tests for pytvt.management Phase 3.

These tests validate evidence-driven SDK inspection and the first real narrow
SDK login path. No test requires the real vendor SDK or a live device.
"""

from __future__ import annotations

import ctypes
from unittest.mock import MagicMock, patch

import pytest

from pytvt.management import (
    CapabilityNotAvailable,
    ManagementClient,
    ManagementNotAuthenticatedError,
)
from pytvt.management.exceptions import (
    ProtocolError,
    SessionExpired,
    TransportError,
    UnsupportedOnPlatformError,
)
from pytvt.management.native import NativeManagementBackend
from pytvt.management.sdk import (
    EvidenceRecord,
    SDKClient,
    ResolvedSymbol,
    SdkManagementBackend,
    SdkDiagnostics,
    _SDKLoadError,
    _load_library,
    _resolve_symbol_registry,
    export_evidence_records,
    export_evidence_schema,
    inspect_sdk_library,
)
from pytvt.netsdk import types as netsdk_types


def _resolved(
    purpose: str,
    status: str,
    symbol_name: str | None,
    *,
    signature_ready: bool = False,
    signature_source: str | None = None,
    semantics: str = "unknown",
) -> ResolvedSymbol:
    return ResolvedSymbol(
        purpose=purpose,
        status=status,
        symbol_name=symbol_name,
        signature_ready=signature_ready,
        signature_source=signature_source,
        notes="test fixture",
        confidence="medium",
        semantics=semantics,
        live_validation_status="not_live_validated",
        evidence_records=(),
    )


def _login_ready_diagnostics(path: str = "/fake/libdvrnetsdk.so") -> SdkDiagnostics:
    symbols = {
        "init": _resolved("init", "confirmed", "NET_SDK_Init", signature_ready=True, signature_source="test"),
        "login": _resolved("login", "confirmed", "NET_SDK_Login", signature_ready=True, signature_source="test"),
        "logout": _resolved("logout", "confirmed", "NET_SDK_Logout", signature_ready=True, signature_source="test"),
        "server_info": _resolved("server_info", "confirmed", "NET_SDK_GetDeviceInfo", signature_ready=True, signature_source="test"),
        "device_enumeration": _resolved("device_enumeration", "missing", None, semantics="configured_inventory"),
        "status_query": _resolved("status_query", "candidate", "NET_SDK_GetAlarmStatus", semantics="channel_status"),
        "alarm_subscription": _resolved("alarm_subscription", "confirmed", "NET_SDK_SetupAlarmChan", signature_ready=True, signature_source="test", semantics="alarm_channel_registration"),
        "alarm_unsubscribe": _resolved("alarm_unsubscribe", "confirmed", "NET_SDK_CloseAlarmChan", signature_ready=True, signature_source="test", semantics="alarm_channel_registration"),
    }
    return SdkDiagnostics(
        sdk_path=path,
        load_success=True,
        load_error=None,
        symbol_scan_success=True,
        symbol_scan_error=None,
        discovered_symbol_count=7,
        symbols=symbols,
        login_path_ready=True,
        login_readiness_reason="init/login/logout symbols confirmed with validated ctypes signatures",
        evidence_record_count=0,
    )


class TestSDKLibraryLoading:
    def test_missing_path_raises(self):
        with pytest.raises(_SDKLoadError, match="does not exist"):
            _load_library("/nonexistent/path/libtvt.so")

    def test_not_a_file_raises(self, tmp_path):
        with pytest.raises(_SDKLoadError, match="is not a file"):
            _load_library(str(tmp_path))

    def test_unparseable_binary_raises(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"\x00not a valid ELF")
        with pytest.raises(_SDKLoadError, match="ctypes could not load"):
            _load_library(str(fake))


class TestSymbolRegistry:
    def test_resolves_confirmed_symbols(self):
        registry = _resolve_symbol_registry({"NET_SDK_Init", "NET_SDK_Login", "NET_SDK_Logout"})
        assert registry["init"].status == "confirmed"
        assert registry["login"].status == "confirmed"
        assert registry["logout"].status == "confirmed"

    def test_resolves_candidate_symbols(self):
        registry = _resolve_symbol_registry({"NET_SDK_LoginEx", "NET_SDK_GetDeviceInfo", "NET_SDK_DiscoverDevice"})
        assert registry["login"].status == "candidate"
        assert registry["login"].symbol_name == "NET_SDK_LoginEx"
        assert registry["server_info"].status == "confirmed"
        assert registry["device_enumeration"].status == "candidate"
        assert registry["device_enumeration"].symbol_name == "NET_SDK_DiscoverDevice"

    def test_resolves_loginex_as_confirmed_when_login_mode_is_login_ex(self):
        registry = _resolve_symbol_registry(
            {"NET_SDK_Init", "NET_SDK_LoginEx", "NET_SDK_Logout"},
            login_mode="login_ex",
        )
        assert registry["login"].status == "confirmed"
        assert registry["login"].symbol_name == "NET_SDK_LoginEx"
        assert registry["login"].signature_ready is True
        assert registry["login"].signature_source == "pytvt.netsdk.bindings.NET_SDK_LoginEx"

    def test_resolves_list_devices_strongest_candidate_as_confirmed(self):
        registry = _resolve_symbol_registry({"NET_SDK_GetDeviceIPCInfo"})
        assert registry["device_enumeration"].status == "confirmed"
        assert registry["device_enumeration"].symbol_name == "NET_SDK_GetDeviceIPCInfo"
        assert registry["device_enumeration"].signature_ready is True
        assert registry["device_enumeration"].semantics == "configured_inventory"

    def test_resolves_status_query_strongest_candidate_as_confirmed(self):
        registry = _resolve_symbol_registry({"NET_SDK_GetDeviceCHStatus", "NET_SDK_GetAlarmStatus"})
        assert registry["status_query"].status == "confirmed"
        assert registry["status_query"].symbol_name == "NET_SDK_GetDeviceCHStatus"
        assert registry["status_query"].signature_ready is True
        assert registry["status_query"].semantics == "channel_status"

    def test_resolves_alarm_subscription_family_as_confirmed(self):
        registry = _resolve_symbol_registry({"NET_SDK_SetupAlarmChan", "NET_SDK_CloseAlarmChan"})
        assert registry["alarm_subscription"].status == "confirmed"
        assert registry["alarm_subscription"].symbol_name == "NET_SDK_SetupAlarmChan"
        assert registry["alarm_subscription"].semantics == "alarm_channel_registration"
        assert registry["alarm_unsubscribe"].status == "confirmed"
        assert registry["alarm_unsubscribe"].symbol_name == "NET_SDK_CloseAlarmChan"

    def test_resolves_missing_symbols(self):
        registry = _resolve_symbol_registry(set())
        assert registry["login"].status == "missing"
        assert registry["status_query"].status == "missing"

    def test_resolved_symbol_includes_confidence_and_evidence_records(self):
        registry = _resolve_symbol_registry({"NET_SDK_Login"})
        assert registry["login"].confidence in {"medium", "high"}
        assert isinstance(registry["login"].evidence_records, tuple)
        assert registry["login"].evidence_records
        payload = registry["login"].as_dict()
        assert payload["live_validation_status"] == "not_live_validated"
        assert "evidence_sources" in payload


class TestInspectSdkLibrary:
    def test_missing_file_reports_failed_diagnostics(self):
        diagnostics = inspect_sdk_library("/nope/libdvrnetsdk.so")
        assert diagnostics.load_success is False
        assert diagnostics.login_path_ready is False
        assert diagnostics.load_error is not None

    def test_loaded_but_no_required_symbols_reports_not_ready(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
            with patch(
                "pytvt.management.sdk.list_exported_symbols",
                return_value=({"OTHER_SYMBOL"}, {"source": "mock", "error": None}),
            ):
                diagnostics = inspect_sdk_library(str(fake))
        assert diagnostics.load_success is True
        assert diagnostics.symbol_scan_success is True
        assert diagnostics.login_path_ready is False
        assert "Missing required SDK symbols" in diagnostics.login_readiness_reason

    def test_candidate_symbols_only_reports_not_ready(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
            with patch(
                "pytvt.management.sdk.list_exported_symbols",
                return_value=(
                    {"NET_SDK_LoginEx", "NET_SDK_Logout", "NET_SDK_Init"},
                    {"source": "mock", "error": None},
                ),
            ):
                diagnostics = inspect_sdk_library(str(fake))
        assert diagnostics.login_path_ready is False
        assert diagnostics.symbols["login"].status == "candidate"

    def test_confirmed_login_symbols_reports_ready(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
            with patch(
                "pytvt.management.sdk.list_exported_symbols",
                return_value=(
                    {"NET_SDK_Init", "NET_SDK_Login", "NET_SDK_Logout"},
                    {"source": "mock", "error": None},
                ),
            ):
                diagnostics = inspect_sdk_library(str(fake))
        assert diagnostics.login_path_ready is True
        assert diagnostics.discovered_symbol_count == 3

    def test_confirmed_loginex_symbols_report_ready_for_loginex_mode(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
            with patch(
                "pytvt.management.sdk.list_exported_symbols",
                return_value=(
                    {"NET_SDK_Init", "NET_SDK_LoginEx", "NET_SDK_Logout"},
                    {"source": "mock", "error": None},
                ),
            ):
                diagnostics = inspect_sdk_library(str(fake), login_mode="login_ex")
        assert diagnostics.login_path_ready is True
        assert diagnostics.login_mode == "login_ex"
        payload = diagnostics.as_dict()
        assert payload["login_backend"]["mode"] == "login_ex"
        assert payload["login_backend"]["connect_type"] == "tcp"
        assert payload["login_backend"]["connect_type_code"] == 0
        assert payload["login_backend"]["symbol_name"] == "NET_SDK_LoginEx"

    def test_diagnostics_include_evidence_count(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
            with patch(
                "pytvt.management.sdk.list_exported_symbols",
                return_value=(
                    {"NET_SDK_Init", "NET_SDK_Login", "NET_SDK_Logout"},
                    {"source": "mock", "error": None},
                ),
            ):
                diagnostics = inspect_sdk_library(str(fake))
        assert diagnostics.evidence_record_count > 0

    def test_architecture_mismatch_is_reported_as_explicit_blocker(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        with patch("pytvt.management.sdk._detect_sdk_machine", return_value="x86_64"):
            with patch("pytvt.management.sdk.platform.machine", return_value="aarch64"):
                with patch("pytvt.management.sdk._load_library", side_effect=_SDKLoadError("ctypes could not load SDK: bad arch")):
                    diagnostics = inspect_sdk_library(str(fake))

        payload = diagnostics.as_dict()
        blockers = payload["sdk_not_ready_blockers"]
        assert any(item["code"] == "sdk_arch_mismatch" for item in blockers)
        assert any(item["code"] == "sdk_load_failure" for item in blockers)

    def test_unresolved_signatures_are_reported_as_explicit_blocker(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")

        with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
            with patch(
                "pytvt.management.sdk.list_exported_symbols",
                return_value=(
                    {"NET_SDK_Init", "NET_SDK_LoginEx", "NET_SDK_Logout"},
                    {"source": "mock", "error": None},
                ),
            ):
                diagnostics = inspect_sdk_library(str(fake))

        payload = diagnostics.as_dict()
        blockers = payload["sdk_not_ready_blockers"]
        assert any(item["code"] == "sdk_unresolved_signatures" for item in blockers)
        assert any(item["code"] == "sdk_unconfirmed_symbols" for item in blockers)


class TestSdkBackendCapabilityProbe:
    def test_supports_sdk_false_without_path(self):
        backend = SdkManagementBackend("192.168.1.1", sdk_path=None)
        assert backend.supports_sdk() is False

    def test_supports_sdk_false_when_library_load_fails(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        diagnostics = _login_ready_diagnostics(str(fake))
        diagnostics = SdkDiagnostics(
            sdk_path=diagnostics.sdk_path,
            load_success=False,
            load_error="boom",
            symbol_scan_success=diagnostics.symbol_scan_success,
            symbol_scan_error=diagnostics.symbol_scan_error,
            discovered_symbol_count=diagnostics.discovered_symbol_count,
            symbols=diagnostics.symbols,
            login_path_ready=False,
            login_readiness_reason=diagnostics.login_readiness_reason,
            evidence_record_count=diagnostics.evidence_record_count,
        )
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            backend = SdkManagementBackend("192.168.1.1", sdk_path=str(fake))
        assert backend.supports_sdk() is False

    def test_supports_sdk_false_when_symbols_are_only_candidates(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        symbols = _login_ready_diagnostics(str(fake)).symbols.copy()
        symbols["login"] = _resolved("login", "candidate", "NET_SDK_LoginEx")
        diagnostics = SdkDiagnostics(
            sdk_path=str(fake),
            load_success=True,
            load_error=None,
            symbol_scan_success=True,
            symbol_scan_error=None,
            discovered_symbol_count=3,
            symbols=symbols,
            login_path_ready=False,
            login_readiness_reason="Required SDK symbols are not confirmed: login",
            evidence_record_count=0,
        )
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
                backend = SdkManagementBackend("192.168.1.1", sdk_path=str(fake))
        assert backend.supports_sdk() is False

    def test_supports_sdk_true_only_when_login_path_ready(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        diagnostics = _login_ready_diagnostics(str(fake))
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
                backend = SdkManagementBackend("192.168.1.1", sdk_path=str(fake))
        assert backend.supports_sdk() is True

    def test_diagnostics_output_shape(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        diagnostics = _login_ready_diagnostics(str(fake))
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
                backend = SdkManagementBackend("192.168.1.1", sdk_path=str(fake))
        payload = backend.get_sdk_diagnostics()
        assert payload["sdk_path"] == str(fake)
        assert isinstance(payload["symbols"], dict)
        assert payload["login_path_ready"] is True
        assert "login" in payload["symbols"]
        assert "evidence_record_count" in payload
        assert "list_devices_backend" in payload
        assert payload["list_devices_backend"]["semantics"] in {
            "configured_inventory",
            "unknown",
        }
        assert "get_device_statuses_backend" in payload
        assert payload["get_device_statuses_backend"]["semantics"] in {
            "channel_status",
            "unknown",
        }
        assert "subscribe_alarms_backend" in payload
        assert payload["subscribe_alarms_backend"]["semantics"] in {
            "alarm_channel_registration",
            "unknown",
        }
        assert payload["subscribe_alarms_backend"]["payload_semantics"] == "opaque"


class TestSDKClientLoginPath:
    def _make_lib(self):
        lib = MagicMock(spec=ctypes.CDLL)
        lib.NET_SDK_Init = MagicMock(return_value=True)
        lib.NET_SDK_Login = MagicMock(return_value=42)
        lib.NET_SDK_LoginEx = MagicMock(return_value=42)
        lib.NET_SDK_Logout = MagicMock(return_value=True)
        lib.NET_SDK_GetDeviceInfo = MagicMock(return_value=True)
        lib.NET_SDK_GetLastError = MagicMock(return_value=0)
        return lib

    def _login_registry(self):
        return _login_ready_diagnostics().symbols

    def test_login_succeeds_when_symbols_and_signatures_are_confirmed(self):
        lib = self._make_lib()
        client = SDKClient(lib, "192.168.1.1", 6003, symbol_registry=self._login_registry())
        client.login("admin", "secret")
        assert client._authenticated is True
        assert client._session_handle == 42
        lib.NET_SDK_Init.assert_called_once_with()
        lib.NET_SDK_Login.assert_called_once()

    def test_login_refuses_when_login_symbol_is_candidate(self):
        lib = self._make_lib()
        registry = self._login_registry().copy()
        registry["login"] = _resolved("login", "candidate", "NET_SDK_LoginEx")
        client = SDKClient(lib, "192.168.1.1", 6003, symbol_registry=registry)
        with pytest.raises(CapabilityNotAvailable, match="only a candidate"):
            client.login("admin", "secret")

    def test_login_refuses_when_signature_is_unresolved(self):
        lib = self._make_lib()
        registry = self._login_registry().copy()
        registry["login"] = _resolved("login", "confirmed", "NET_SDK_Login", signature_ready=False)
        client = SDKClient(lib, "192.168.1.1", 6003, symbol_registry=registry)
        with pytest.raises(CapabilityNotAvailable, match="signature is unresolved"):
            client.login("admin", "secret")

    def test_login_raises_auth_error_on_invalid_handle(self):
        lib = self._make_lib()
        lib.NET_SDK_Login.return_value = -1
        client = SDKClient(lib, "192.168.1.1", 6003, symbol_registry=self._login_registry())
        with pytest.raises(Exception, match="invalid handle"):
            client.login("admin", "secret")

    def test_login_raises_auth_error_with_sdk_error_code_when_available(self):
        lib = self._make_lib()
        lib.NET_SDK_Login.return_value = -1
        lib.NET_SDK_GetLastError.return_value = 4660
        client = SDKClient(lib, "192.168.1.1", 6003, symbol_registry=self._login_registry())
        with pytest.raises(Exception, match="error_code=4660"):
            client.login("admin", "secret")

    def test_login_ex_mode_calls_loginex_without_fallback(self):
        lib = self._make_lib()
        registry = self._login_registry().copy()
        registry["login"] = _resolved(
            "login",
            "confirmed",
            "NET_SDK_LoginEx",
            signature_ready=True,
            signature_source="pytvt.netsdk.bindings.NET_SDK_LoginEx",
        )
        client = SDKClient(
            lib,
            "192.168.1.1",
            6003,
            symbol_registry=registry,
            login_mode="login_ex",
        )
        client.login("admin", "secret")
        lib.NET_SDK_LoginEx.assert_called_once()
        lib.NET_SDK_Login.assert_not_called()

    def test_login_ex_mode_uses_nat_connect_type_code(self):
        lib = self._make_lib()
        registry = self._login_registry().copy()
        registry["login"] = _resolved(
            "login",
            "confirmed",
            "NET_SDK_LoginEx",
            signature_ready=True,
            signature_source="pytvt.netsdk.bindings.NET_SDK_LoginEx",
        )
        client = SDKClient(
            lib,
            "192.168.1.1",
            6003,
            symbol_registry=registry,
            login_mode="login_ex",
            login_connect_type="nat",
        )
        client.login("admin", "secret")
        args, _kwargs = lib.NET_SDK_LoginEx.call_args
        assert args[5] == 1
        assert args[6] == b""

    def test_login_ex_mode_passes_device_id_bytes(self):
        lib = self._make_lib()
        registry = self._login_registry().copy()
        registry["login"] = _resolved(
            "login",
            "confirmed",
            "NET_SDK_LoginEx",
            signature_ready=True,
            signature_source="pytvt.netsdk.bindings.NET_SDK_LoginEx",
        )
        client = SDKClient(
            lib,
            "192.168.1.1",
            6003,
            symbol_registry=registry,
            login_mode="login_ex",
            login_connect_type="nat",
        )
        client.login("admin", "secret", device_id="NVMS")
        args, _kwargs = lib.NET_SDK_LoginEx.call_args
        assert args[5] == 1
        assert args[6] == b"NVMS"

    def test_login_ex_mode_surfaces_error_code_and_symbol_name(self):
        lib = self._make_lib()
        lib.NET_SDK_LoginEx.return_value = -1
        lib.NET_SDK_GetLastError.return_value = 7
        registry = self._login_registry().copy()
        registry["login"] = _resolved(
            "login",
            "confirmed",
            "NET_SDK_LoginEx",
            signature_ready=True,
            signature_source="pytvt.netsdk.bindings.NET_SDK_LoginEx",
        )
        client = SDKClient(
            lib,
            "192.168.1.1",
            6003,
            symbol_registry=registry,
            login_mode="login_ex",
        )
        with pytest.raises(Exception, match=r"NET_SDK_LoginEx returned an invalid handle \(error_code=7\)"):
            client.login("admin", "secret")

    def test_close_calls_real_logout_and_resets_state(self):
        lib = self._make_lib()
        client = SDKClient(lib, "192.168.1.1", 6003, symbol_registry=self._login_registry())
        client.login("admin", "secret")
        client.close()
        assert client._authenticated is False
        assert client._session_handle is None
        lib.NET_SDK_Logout.assert_called_once_with(42)


class TestClientBackendSelection:
    def test_selects_sdk_only_when_login_path_ready(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        diagnostics = _login_ready_diagnostics(str(fake))
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
                client = ManagementClient("192.168.1.1", sdk_path=str(fake), prefer_sdk=True)
                backend = client._select_backend()
        assert isinstance(backend, SdkManagementBackend)

    def test_selects_native_when_sdk_not_ready(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        diagnostics = SdkDiagnostics(
            sdk_path=str(fake),
            load_success=True,
            load_error=None,
            symbol_scan_success=True,
            symbol_scan_error=None,
            discovered_symbol_count=1,
            symbols=_resolve_symbol_registry({"NET_SDK_LoginEx"}),
            login_path_ready=False,
            login_readiness_reason="Required SDK symbols are not confirmed: login",
            evidence_record_count=0,
        )
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
                client = ManagementClient("192.168.1.1", sdk_path=str(fake), prefer_sdk=True)
                backend = client._select_backend()
        assert isinstance(backend, NativeManagementBackend)

    def test_supports_sdk_matches_login_path_readiness(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        diagnostics = _login_ready_diagnostics(str(fake))
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
                client = ManagementClient("192.168.1.1", sdk_path=str(fake), prefer_sdk=True)
                assert client.supports_sdk() is True

    def test_backend_name_is_none_before_login(self):
        client = ManagementClient("192.168.1.1")
        assert client.backend_name is None

    def test_get_sdk_diagnostics_without_login_returns_probe_diagnostics(self):
        client = ManagementClient("192.168.1.1")
        payload = client.get_sdk_diagnostics()
        assert "load_success" in payload
        assert "symbols" in payload

    def test_get_sdk_diagnostics_exposes_selected_login_mode(self, tmp_path):
        fake = tmp_path / "libtvt.so"
        fake.write_bytes(b"mock")
        diagnostics = _login_ready_diagnostics(str(fake))
        diagnostics = SdkDiagnostics(
            sdk_path=diagnostics.sdk_path,
            load_success=diagnostics.load_success,
            load_error=diagnostics.load_error,
            symbol_scan_success=diagnostics.symbol_scan_success,
            symbol_scan_error=diagnostics.symbol_scan_error,
            discovered_symbol_count=diagnostics.discovered_symbol_count,
            symbols=diagnostics.symbols,
            login_path_ready=diagnostics.login_path_ready,
            login_readiness_reason=diagnostics.login_readiness_reason,
            evidence_record_count=diagnostics.evidence_record_count,
            login_mode="login_ex",
            login_connect_type="nat20",
        )
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock()):
                client = ManagementClient(
                    "192.168.1.1",
                    sdk_path=str(fake),
                    prefer_sdk=True,
                    sdk_login_mode="login_ex",
                    sdk_connect_type="nat20",
                )
                payload = client.get_sdk_diagnostics()
        assert payload["login_mode"] == "login_ex"
        assert payload["login_connect_type"] == "nat20"
        assert payload["login_backend"]["mode"] == "login_ex"
        assert payload["login_backend"]["connect_type"] == "nat20"
        assert payload["login_backend"]["connect_type_code"] == 2

    def test_backend_name_and_sdk_diagnostics_use_active_sdk_backend(self):
        diagnostics = _login_ready_diagnostics("/fake/libdvrnetsdk.so")
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock(spec=ctypes.CDLL)):
                client = ManagementClient("192.168.1.1", sdk_path="/fake/libdvrnetsdk.so", prefer_sdk=True)
                client._backend = SdkManagementBackend("192.168.1.1", sdk_path="/fake/libdvrnetsdk.so")
        assert client.backend_name == "sdk"
        assert client.get_sdk_diagnostics()["load_success"] is True


class TestSDKClientServerInfo:
    def _make_client(self) -> tuple[SDKClient, MagicMock]:
        lib = MagicMock(spec=ctypes.CDLL)
        lib.NET_SDK_Init = MagicMock(return_value=True)
        lib.NET_SDK_Login = MagicMock(return_value=42)
        lib.NET_SDK_Logout = MagicMock(return_value=True)
        lib.NET_SDK_GetDeviceInfo = MagicMock(return_value=True)
        lib.NET_SDK_GetLastError = MagicMock(return_value=0)
        client = SDKClient(lib, "192.168.1.1", 6003, symbol_registry=_login_ready_diagnostics().symbols)
        return client, lib

    def test_get_server_info_requires_active_session(self):
        client, _lib = self._make_client()
        with pytest.raises(SessionExpired):
            client.get_server_info()

    def test_get_server_info_returns_mapped_payload_and_raw_data(self):
        client, lib = self._make_client()

        def _fill_info(_handle, info_ptr):
            info = ctypes.cast(info_ptr, ctypes.POINTER(netsdk_types.NET_SDK_DEVICEINFO)).contents
            info.deviceProduct = b"TVT-TD-A510"
            info.firmwareVersion = b"v1.2.3"
            info.szSN = b"SN-ABC-123"
            return True

        lib.NET_SDK_GetDeviceInfo.side_effect = _fill_info
        client.login("admin", "secret")

        server_info = client.get_server_info()
        assert server_info.model == "TVT-TD-A510"
        assert server_info.firmware == "v1.2.3"
        assert server_info.serial_number == "SN-ABC-123"
        assert server_info.raw_data
        assert server_info.raw_data["deviceProduct"] == "TVT-TD-A510"
        assert server_info.raw_data["firmwareVersion"] == "v1.2.3"
        assert server_info.raw_data["szSN"] == "SN-ABC-123"

    def test_get_server_info_raises_transport_error_with_error_code(self):
        client, lib = self._make_client()
        client.login("admin", "secret")
        lib.NET_SDK_GetDeviceInfo.return_value = False
        lib.NET_SDK_GetLastError.return_value = 4660

        with pytest.raises(TransportError, match="error_code=4660"):
            client.get_server_info()

    def test_backend_get_server_info_without_login_raises_session_expired(self):
        diagnostics = _login_ready_diagnostics()
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock(spec=ctypes.CDLL)):
                backend = SdkManagementBackend("192.168.1.1", sdk_path="/fake/libdvrnetsdk.so")

        expected_exc = SessionExpired if backend.get_context().platform.os_family == "linux" else UnsupportedOnPlatformError
        with pytest.raises(expected_exc):
            backend.get_server_info()


class TestSDKClientListDevices:
    def _registry_with_list_devices(self) -> dict[str, ResolvedSymbol]:
        registry = _login_ready_diagnostics().symbols.copy()
        registry["device_enumeration"] = _resolved(
            "device_enumeration",
            "confirmed",
            "NET_SDK_GetDeviceIPCInfo",
            signature_ready=True,
            signature_source="test",
            semantics="configured_inventory",
        )
        return registry

    def _make_client(self) -> tuple[SDKClient, MagicMock]:
        lib = MagicMock(spec=ctypes.CDLL)
        lib.NET_SDK_Init = MagicMock(return_value=True)
        lib.NET_SDK_Login = MagicMock(return_value=42)
        lib.NET_SDK_Logout = MagicMock(return_value=True)
        lib.NET_SDK_GetDeviceIPCInfo = MagicMock(return_value=True)
        lib.NET_SDK_GetLastError = MagicMock(return_value=0)
        client = SDKClient(lib, "192.168.1.1", 6003, symbol_registry=self._registry_with_list_devices())
        return client, lib

    def test_list_devices_requires_active_session(self):
        client, _lib = self._make_client()
        with pytest.raises(SessionExpired):
            client.list_devices()

    def test_list_devices_raises_transport_error_with_error_code(self):
        client, lib = self._make_client()
        client.login("admin", "secret")
        lib.NET_SDK_GetDeviceIPCInfo.return_value = False
        lib.NET_SDK_GetLastError.return_value = 43981

        with pytest.raises(TransportError, match="error_code=43981"):
            client.list_devices()

    def test_list_devices_returns_conservative_device_mapping(self):
        client, lib = self._make_client()

        def _fill_ipc(_handle, info_array, _max_count, count_ptr):
            info_array[0].deviceID = 101
            info_array[0].status = 1
            info_array[0].szServer = b"192.0.2.10"
            info_array[0].szChlname = b"Front Gate"
            info_array[0].productModel = b"TVT-IPC-Model"
            info_array[0].nPort = 554
            info_array[0].nHttpPort = 80
            info_array[1].deviceID = 101
            info_array[1].status = 1
            info_array[1].szServer = b"192.0.2.10"
            info_array[1].szChlname = b"Front Gate Aux"
            info_array[1].productModel = b"TVT-IPC-Model"
            ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_long)).contents.value = 2
            return True

        lib.NET_SDK_GetDeviceIPCInfo.side_effect = _fill_ipc
        client.login("admin", "secret")

        devices = client.list_devices()
        assert len(devices) == 1
        assert devices[0].device_id == "101"
        assert devices[0].ip_address == "192.0.2.10"
        assert devices[0].status == "online"

    def test_list_devices_preserves_unknown_fields_in_raw_data(self):
        client, lib = self._make_client()

        def _fill_ipc(_handle, info_array, _max_count, count_ptr):
            info_array[0].deviceID = 202
            info_array[0].status = 0
            info_array[0].szServer = b"10.200.10.202"
            info_array[0].manufacturerName = b"VendorName"
            info_array[0].szEtherName = b"eth0"
            ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_long)).contents.value = 1
            return True

        lib.NET_SDK_GetDeviceIPCInfo.side_effect = _fill_ipc
        client.login("admin", "secret")

        devices = client.list_devices()
        payload = devices[0].raw_data
        assert payload["manufacturerName"] == "VendorName"
        assert payload["szEtherName"] == "eth0"
        assert payload["inventory_source_symbol"] == "NET_SDK_GetDeviceIPCInfo"
        assert payload["inventory_semantics"] == "configured_ipc_inventory"

    def test_list_devices_raises_protocol_error_for_semantic_mismatch(self):
        client, lib = self._make_client()

        def _fill_ipc(_handle, info_array, _max_count, count_ptr):
            info_array[0].channel = 4
            info_array[0].status = 1
            ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_long)).contents.value = 1
            return True

        lib.NET_SDK_GetDeviceIPCInfo.side_effect = _fill_ipc
        client.login("admin", "secret")

        with pytest.raises(ProtocolError, match="semantically incompatible"):
            client.list_devices()

    def test_backend_list_devices_without_login_raises_session_expired(self):
        diagnostics = _login_ready_diagnostics()
        diagnostics = SdkDiagnostics(
            sdk_path=diagnostics.sdk_path,
            load_success=diagnostics.load_success,
            load_error=diagnostics.load_error,
            symbol_scan_success=diagnostics.symbol_scan_success,
            symbol_scan_error=diagnostics.symbol_scan_error,
            discovered_symbol_count=diagnostics.discovered_symbol_count,
            symbols=self._registry_with_list_devices(),
            login_path_ready=diagnostics.login_path_ready,
            login_readiness_reason=diagnostics.login_readiness_reason,
            evidence_record_count=diagnostics.evidence_record_count,
        )
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock(spec=ctypes.CDLL)):
                backend = SdkManagementBackend("192.168.1.1", sdk_path="/fake/libdvrnetsdk.so")

        expected_exc = SessionExpired if backend.get_context().platform.os_family == "linux" else UnsupportedOnPlatformError
        with pytest.raises(expected_exc):
            backend.list_devices()


class TestSDKClientDeviceStatuses:
    def _registry_with_status_query(self) -> dict[str, ResolvedSymbol]:
        registry = _login_ready_diagnostics().symbols.copy()
        registry["status_query"] = _resolved(
            "status_query",
            "confirmed",
            "NET_SDK_GetDeviceCHStatus",
            signature_ready=True,
            signature_source="test",
            semantics="channel_status",
        )
        return registry

    def _make_client(self) -> tuple[SDKClient, MagicMock]:
        lib = MagicMock(spec=ctypes.CDLL)
        lib.NET_SDK_Init = MagicMock(return_value=True)
        lib.NET_SDK_Login = MagicMock(return_value=42)
        lib.NET_SDK_Logout = MagicMock(return_value=True)
        lib.NET_SDK_GetDeviceCHStatus = MagicMock(return_value=True)
        lib.NET_SDK_GetLastError = MagicMock(return_value=0)
        client = SDKClient(lib, "192.168.1.1", 6003, symbol_registry=self._registry_with_status_query())
        return client, lib

    def test_get_device_statuses_requires_active_session(self):
        client, _lib = self._make_client()
        with pytest.raises(SessionExpired):
            client.get_device_statuses()

    def test_get_device_statuses_raises_transport_error_with_error_code(self):
        client, lib = self._make_client()
        client.login("admin", "secret")
        lib.NET_SDK_GetDeviceCHStatus.return_value = False
        lib.NET_SDK_GetLastError.return_value = 48879

        with pytest.raises(TransportError, match="error_code=48879"):
            client.get_device_statuses()

    def test_get_device_statuses_returns_conservative_mapping(self):
        client, lib = self._make_client()

        def _fill_status(_handle, status_array, _max_count, count_ptr):
            status_array[0].channel = 1
            status_array[0].status = 1
            status_array[0].name = b"Cam 1"
            status_array[1].channel = 2
            status_array[1].status = 0
            status_array[1].name = b"Cam 2"
            ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_long)).contents.value = 2
            return True

        lib.NET_SDK_GetDeviceCHStatus.side_effect = _fill_status
        client.login("admin", "secret")
        statuses = client.get_device_statuses()

        assert len(statuses) == 2
        assert statuses[0].device_id == "channel:1"
        assert statuses[0].online is True
        assert statuses[1].device_id == "channel:2"
        assert statuses[1].online is False

    def test_get_device_statuses_preserves_unknown_fields_in_raw_data(self):
        client, lib = self._make_client()

        def _fill_status(_handle, status_array, _max_count, count_ptr):
            status_array[0].channel = 4
            status_array[0].status = 1
            status_array[0].chlType = 19
            status_array[0].name = b"Loading Dock"
            ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_long)).contents.value = 1
            return True

        lib.NET_SDK_GetDeviceCHStatus.side_effect = _fill_status
        client.login("admin", "secret")
        statuses = client.get_device_statuses()

        payload = statuses[0].raw_data
        assert payload["chlType"] == 19
        assert payload["status_source_symbol"] == "NET_SDK_GetDeviceCHStatus"
        assert payload["status_semantics"] == "channel_connectivity"

    def test_get_device_statuses_raises_protocol_error_for_semantic_mismatch(self):
        client, lib = self._make_client()

        def _fill_status(_handle, status_array, _max_count, count_ptr):
            status_array[0].channel = 0
            status_array[0].name = b""
            ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_long)).contents.value = 1
            return True

        lib.NET_SDK_GetDeviceCHStatus.side_effect = _fill_status
        client.login("admin", "secret")

        with pytest.raises(ProtocolError, match="semantically incompatible"):
            client.get_device_statuses()

    def test_backend_get_device_statuses_without_login_raises_session_expired(self):
        diagnostics = _login_ready_diagnostics()
        diagnostics = SdkDiagnostics(
            sdk_path=diagnostics.sdk_path,
            load_success=diagnostics.load_success,
            load_error=diagnostics.load_error,
            symbol_scan_success=diagnostics.symbol_scan_success,
            symbol_scan_error=diagnostics.symbol_scan_error,
            discovered_symbol_count=diagnostics.discovered_symbol_count,
            symbols=self._registry_with_status_query(),
            login_path_ready=diagnostics.login_path_ready,
            login_readiness_reason=diagnostics.login_readiness_reason,
            evidence_record_count=diagnostics.evidence_record_count,
        )
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock(spec=ctypes.CDLL)):
                backend = SdkManagementBackend("192.168.1.1", sdk_path="/fake/libdvrnetsdk.so")

        expected_exc = SessionExpired if backend.get_context().platform.os_family == "linux" else UnsupportedOnPlatformError
        with pytest.raises(expected_exc):
            backend.get_device_statuses()


class TestSDKClientSubscribeAlarms:
    def _registry_with_alarm_subscription(self) -> dict[str, ResolvedSymbol]:
        registry = _login_ready_diagnostics().symbols.copy()
        registry["alarm_subscription"] = _resolved(
            "alarm_subscription",
            "confirmed",
            "NET_SDK_SetupAlarmChan",
            signature_ready=True,
            signature_source="test",
            semantics="alarm_channel_registration",
        )
        registry["alarm_unsubscribe"] = _resolved(
            "alarm_unsubscribe",
            "confirmed",
            "NET_SDK_CloseAlarmChan",
            signature_ready=True,
            signature_source="test",
            semantics="alarm_channel_registration",
        )
        return registry

    def _make_client(self) -> tuple[SDKClient, MagicMock]:
        lib = MagicMock(spec=ctypes.CDLL)
        lib.NET_SDK_Init = MagicMock(return_value=True)
        lib.NET_SDK_Login = MagicMock(return_value=42)
        lib.NET_SDK_Logout = MagicMock(return_value=True)
        lib.NET_SDK_SetupAlarmChan = MagicMock(return_value=17)
        lib.NET_SDK_CloseAlarmChan = MagicMock(return_value=True)
        lib.NET_SDK_GetLastError = MagicMock(return_value=0)
        client = SDKClient(lib, "192.168.1.1", 6003, symbol_registry=self._registry_with_alarm_subscription())
        return client, lib

    def test_subscribe_alarms_requires_active_session(self):
        client, _lib = self._make_client()
        with pytest.raises(SessionExpired):
            client.subscribe_alarms()

    def test_subscribe_alarms_raises_transport_error_with_error_code(self):
        client, lib = self._make_client()
        client.login("admin", "secret")
        lib.NET_SDK_SetupAlarmChan.return_value = -1
        lib.NET_SDK_GetLastError.return_value = 51966

        with pytest.raises(TransportError, match="error_code=51966"):
            client.subscribe_alarms()

    def test_subscribe_alarms_blocks_when_teardown_semantics_unavailable(self):
        client, lib = self._make_client()
        client.login("admin", "secret")
        del lib.NET_SDK_CloseAlarmChan

        with pytest.raises(CapabilityNotAvailable, match="teardown function"):
            client.subscribe_alarms()

    def test_subscribe_alarms_returns_registration_handle_and_close_is_supported(self):
        client, lib = self._make_client()
        client.login("admin", "secret")

        sub = client.subscribe_alarms()
        assert sub.handle == "alarm-channel:17"
        assert getattr(sub, "is_active") is True

        sub.close()
        assert getattr(sub, "is_active") is False
        lib.NET_SDK_CloseAlarmChan.assert_called_once_with(17)

    def test_subscribe_alarms_preserves_subscription_metadata_in_raw_data(self):
        client, _lib = self._make_client()
        client.login("admin", "secret")

        sub = client.subscribe_alarms()
        assert sub.raw_data["subscription_semantics"] == "alarm_channel_registration"
        assert sub.raw_data["payload_semantics"] == "opaque"
        assert sub.raw_data["callback_semantics_confirmed"] is False

    def test_subscribe_alarms_allows_only_single_active_subscription(self):
        client, _lib = self._make_client()
        client.login("admin", "secret")
        _first = client.subscribe_alarms()

        with pytest.raises(CapabilityNotAvailable, match="Multiple alarm subscriptions"):
            client.subscribe_alarms()

    def test_backend_subscribe_alarms_without_login_raises_session_expired(self):
        diagnostics = _login_ready_diagnostics()
        diagnostics = SdkDiagnostics(
            sdk_path=diagnostics.sdk_path,
            load_success=diagnostics.load_success,
            load_error=diagnostics.load_error,
            symbol_scan_success=diagnostics.symbol_scan_success,
            symbol_scan_error=diagnostics.symbol_scan_error,
            discovered_symbol_count=diagnostics.discovered_symbol_count,
            symbols=self._registry_with_alarm_subscription(),
            login_path_ready=diagnostics.login_path_ready,
            login_readiness_reason=diagnostics.login_readiness_reason,
            evidence_record_count=diagnostics.evidence_record_count,
        )
        with patch("pytvt.management.sdk.inspect_sdk_library", return_value=diagnostics):
            with patch("pytvt.management.sdk._load_library", return_value=MagicMock(spec=ctypes.CDLL)):
                backend = SdkManagementBackend("192.168.1.1", sdk_path="/fake/libdvrnetsdk.so")

        expected_exc = SessionExpired if backend.get_context().platform.os_family == "linux" else UnsupportedOnPlatformError
        with pytest.raises(expected_exc):
            backend.subscribe_alarms()


class TestNativeBackendStub:
    def test_login_raises(self):
        backend = NativeManagementBackend("192.168.1.1")
        with pytest.raises(CapabilityNotAvailable):
            backend.login("admin", "pass")


class TestClientRequiresLogin:
    def test_get_server_info_before_login(self):
        client = ManagementClient("192.168.1.1")
        with pytest.raises(ManagementNotAuthenticatedError):
            client.get_server_info()


class TestContextManager:
    def test_context_manager_closes_on_exit(self):
        client = ManagementClient("192.168.1.1")
        mock_backend = MagicMock()
        client._backend = mock_backend
        with client:
            pass
        mock_backend.close.assert_called_once()


class TestEvidenceExport:
    def test_export_evidence_records_returns_plain_dicts(self):
        records = export_evidence_records()
        assert records
        assert isinstance(records[0], dict)
        assert "source_type" in records[0]

    def test_export_evidence_schema_shape(self):
        schema = export_evidence_schema()
        assert "fields" in schema
        assert "example" in schema
        assert schema["example"]["suspected_capability"] == "login"

    def test_evidence_record_as_dict(self):
        record = EvidenceRecord(
            symbol_name="NET_SDK_Login",
            suspected_capability="login",
            source_type="linux_sdk_probe",
            confidence="high",
            notes="test",
        )
        payload = record.as_dict()
        assert payload["symbol_name"] == "NET_SDK_Login"
