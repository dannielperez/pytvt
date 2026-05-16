"""Tests for native SDK LAN IP modification adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pytvt.netsdk.client import NetSdkCapabilityError
from pytvt.netsdk.ip_modify import (
    MACOS_REQUEST_MODIFY_DEVICE_IP,
    MACOS_REQUEST_MODIFY_DEVICE_IP_OBSERVER,
    build_netclient_modify_ip_xml,
    modify_device_ip_by_mac,
    normalize_mac,
    scan_device_match,
    sdk_ip_modify_diagnostics,
    verify_device_ip_after_modify,
)
from pytvt.netsdk.loader import NetSdkUnavailable


class Func:
    def __init__(self, return_value=True):
        self.return_value = return_value
        self.calls = []
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.return_value


class SeqFunc(Func):
    def __init__(self, values):
        super().__init__(None)
        self.values = list(values)

    def __call__(self, *args):
        self.calls.append(args)
        if self.values:
            return self.values.pop(0)
        return None


class FakeMacLib:
    def __init__(self, ok=True, error=0):
        setattr(self, MACOS_REQUEST_MODIFY_DEVICE_IP, Func(ok))
        self.NET_CLIENT_GetLastError = Func(error)


class FakeMacObserverOnlyLib:
    def __init__(self, ok=True, error=0):
        setattr(self, MACOS_REQUEST_MODIFY_DEVICE_IP_OBSERVER, Func(ok))
        self.NET_CLIENT_GetLastError = Func(error)


class FakeMacBothLib:
    def __init__(self, first_ok=False, second_ok=True, first_error=9001, second_error=0):
        setattr(self, MACOS_REQUEST_MODIFY_DEVICE_IP, Func(first_ok))
        setattr(self, MACOS_REQUEST_MODIFY_DEVICE_IP_OBSERVER, Func(second_ok))
        self.NET_CLIENT_GetLastError = SeqFunc([first_error, second_error])


def test_normalize_mac_accepts_common_formats():
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"


def test_symbol_missing_raises_capability_error():
    with patch("pytvt.netsdk.ip_modify.load_sdk", return_value=object()):
        with pytest.raises(NetSdkCapabilityError, match="does not export"):
            modify_device_ip_by_mac(
                "AA:BB:CC:DD:EE:FF",
                "192.168.1.10",
                "192.168.1.20",
                "255.255.255.0",
                "192.168.1.1",
            )


def test_sdk_unavailable_is_clear():
    with patch("pytvt.netsdk.ip_modify.load_sdk", side_effect=NetSdkUnavailable("missing sdk")):
        with pytest.raises(NetSdkUnavailable, match="missing sdk"):
            modify_device_ip_by_mac(
                "AA:BB:CC:DD:EE:FF",
                None,
                "192.168.1.20",
                "255.255.255.0",
                "192.168.1.1",
            )


def test_macos_successful_modify_call_with_mocked_sdk():
    lib = FakeMacLib(ok=True)
    with patch("pytvt.netsdk.ip_modify.load_sdk", return_value=lib):
        result = modify_device_ip_by_mac(
            "AA:BB:CC:DD:EE:FF",
            "192.168.1.10",
            "192.168.1.20",
            "255.255.255.0",
            "192.168.1.1",
            username="admin",
            password="secret",
        )

    func = getattr(lib, MACOS_REQUEST_MODIFY_DEVICE_IP)
    assert result.success is True
    assert result.sdk_function_used == "NET_CLIENT_RequestModifyDeviceIp"
    assert func.calls


def test_macos_observer_overload_supported():
    lib = FakeMacObserverOnlyLib(ok=True)
    with patch("pytvt.netsdk.ip_modify.load_sdk", return_value=lib):
        result = modify_device_ip_by_mac(
            "AA:BB:CC:DD:EE:FF",
            "192.168.1.10",
            "192.168.1.20",
            "255.255.255.0",
            "192.168.1.1",
        )

    func = getattr(lib, MACOS_REQUEST_MODIFY_DEVICE_IP_OBSERVER)
    assert result.success is True
    assert result.sdk_function_used == "NET_CLIENT_RequestModifyDeviceIp"
    assert func.calls


def test_macos_first_overload_failure_falls_back_to_second():
    lib = FakeMacBothLib(first_ok=False, second_ok=True)
    with patch("pytvt.netsdk.ip_modify.load_sdk", return_value=lib):
        result = modify_device_ip_by_mac(
            "AA:BB:CC:DD:EE:FF",
            "192.168.1.10",
            "192.168.1.20",
            "255.255.255.0",
            "192.168.1.1",
        )

    first = getattr(lib, MACOS_REQUEST_MODIFY_DEVICE_IP)
    second = getattr(lib, MACOS_REQUEST_MODIFY_DEVICE_IP_OBSERVER)
    assert result.success is True
    assert result.sdk_function_used == "NET_CLIENT_RequestModifyDeviceIp"
    assert first.calls
    assert second.calls


def test_macos_sdk_failure_includes_error_code():
    lib = FakeMacLib(ok=False, error=1234)
    with patch("pytvt.netsdk.ip_modify.load_sdk", return_value=lib):
        result = modify_device_ip_by_mac(
            "AA:BB:CC:DD:EE:FF",
            "192.168.1.10",
            "192.168.1.20",
            "255.255.255.0",
            "192.168.1.1",
        )

    assert result.success is False
    assert result.error_code == 1234
    assert "failed" in (result.error_message or "")


def test_no_secret_in_result_repr_or_error():
    lib = FakeMacLib(ok=True)
    with patch("pytvt.netsdk.ip_modify.load_sdk", return_value=lib):
        result = modify_device_ip_by_mac(
            "AA:BB:CC:DD:EE:FF",
            None,
            "192.168.1.20",
            "255.255.255.0",
            "192.168.1.1",
            password="NeverRenderMe",
        )

    assert "NeverRenderMe" not in repr(result)


def test_request_xml_contains_password_only_in_native_payload():
    xml = build_netclient_modify_ip_xml(
        mac="AA:BB:CC:DD:EE:FF",
        old_ip=None,
        new_ip="192.168.1.20",
        subnet_mask="255.255.255.0",
        gateway="192.168.1.1",
        username="admin",
        password="secret",
    )
    assert b"<password>secret</password>" in xml


def test_architecture_mismatch_message_from_diagnostics():
    with patch("pytvt.netsdk.ip_modify.resolve_sdk_library_path", return_value="/opt/tvt/libNetClientSDK.dylib"):
        with patch("pytvt.netsdk.ip_modify.sdk_binary_arches", return_value={"x86_64"}):
            with patch(
                "pytvt.netsdk.ip_modify.load_sdk",
                side_effect=NetSdkUnavailable("Run under Rosetta/x86_64 Python"),
            ):
                report = sdk_ip_modify_diagnostics("/opt/tvt")

    assert report["sdk_architecture"] == ["x86_64"]
    assert "Rosetta" in str(report["load_error"])


def test_diagnostics_include_selected_function_when_symbol_available():
    lib = FakeMacLib(ok=True)
    with patch("pytvt.netsdk.ip_modify.resolve_sdk_library_path", return_value="/opt/tvt/libNetClientSDK.dylib"):
        with patch("pytvt.netsdk.ip_modify.sdk_binary_arches", return_value={"x86_64"}):
            with patch("pytvt.netsdk.ip_modify.load_sdk", return_value=lib):
                report = sdk_ip_modify_diagnostics("/opt/tvt")

    assert report["selected_sdk_function"] == "NET_CLIENT_RequestModifyDeviceIp"


def test_scan_only_helper_never_calls_modify():
    with patch("pytvt.netsdk.ip_modify.modify_device_ip_by_mac") as mock_modify:
        with patch(
            "pytvt.netsdk.ip_modify._scan_match_by_mac_or_ip",
            return_value={"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.10"},
        ):
            with patch(
                "pytvt.netsdk.ip_modify.sdk_ip_modify_diagnostics",
                return_value={"symbols": {"NET_SDK_DiscoverDevice": True}},
            ):
                payload = scan_device_match(mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.10")

    assert payload["matched"] is True
    mock_modify.assert_not_called()


def test_verify_result_contains_no_secret_text():
    with patch(
        "pytvt.netsdk.ip_modify.sdk_ip_modify_diagnostics",
        return_value={"symbols": {"NET_SDK_DiscoverDevice": False}},
    ):
        payload = verify_device_ip_after_modify(mac="AA:BB:CC:DD:EE:FF", new_ip="192.168.1.20")

    assert "secret" not in repr(payload).lower()
