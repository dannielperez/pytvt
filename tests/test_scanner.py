"""Tests for pytvt.scanner — device loading, filtering, backend dispatch."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pytvt.models import CameraInfo, DeviceEntry, ScannerConfig, ScanResult
from pytvt.scanner import (
    _is_tvt,
    filter_tvt_devices,
    load_devices,
    scan_single_nvr,
)

# ── load_devices ─────────────────────────────────────────────────────


class TestLoadDevices:
    def test_loads_json_array(self, tmp_path):
        f = tmp_path / "devices.json"
        f.write_text(
            json.dumps(
                [
                    {"ip": "10.0.0.1", "site": "A"},
                    {"ip": "10.0.0.2"},
                ]
            )
        )
        devices = load_devices(str(f))
        assert len(devices) == 2
        assert devices[0].ip == "10.0.0.1"
        assert devices[0].site == "A"
        assert devices[1].ip == "10.0.0.2"

    def test_raises_on_non_array(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text(json.dumps({"ip": "10.0.0.1"}))
        with pytest.raises(ValueError, match="must contain a JSON array"):
            load_devices(str(f))

    def test_empty_array(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("[]")
        assert load_devices(str(f)) == []

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_devices("/nonexistent/devices.json")


# ── filter_tvt_devices / _is_tvt ────────────────────────────────────


class TestFilterTvt:
    def test_by_manufacturer(self):
        devices = [
            DeviceEntry(ip="1.1.1.1", manufacturer="TVT"),
            DeviceEntry(ip="2.2.2.2", manufacturer="Hikvision"),
            DeviceEntry(ip="3.3.3.3", manufacturer="UNIQUE"),
        ]
        result = filter_tvt_devices(devices)
        assert len(result) == 2
        assert result[0].ip == "1.1.1.1"
        assert result[1].ip == "3.3.3.3"

    def test_by_mac_prefix(self):
        d = DeviceEntry(ip="1.1.1.1", mac="58:5B:69:AA:BB:CC", manufacturer="")
        assert _is_tvt(d) is True

    def test_not_tvt(self):
        d = DeviceEntry(ip="1.1.1.1", mac="AA:BB:CC:DD:EE:FF", manufacturer="Other")
        assert _is_tvt(d) is False

    def test_case_insensitive_manufacturer(self):
        d = DeviceEntry(ip="1.1.1.1", manufacturer="tvt")
        assert _is_tvt(d) is True

    def test_empty_list(self):
        assert filter_tvt_devices([]) == []


# ── scan_single_nvr ──────────────────────────────────────────────────


class TestScanSingleNvr:
    def _make_result(self, success: bool = True) -> ScanResult:
        r = ScanResult(nvr_ip="10.0.0.1", backend="protocol", success=success)
        if success:
            r.cameras = [CameraInfo(name="C1", address="192.168.1.1")]
        else:
            r.error = "timeout"
        return r

    @patch("pytvt.scanner._raw_protocol_scan")
    def test_protocol_backend(self, mock_scan, sample_device, default_config):
        mock_scan.return_value = {
            "success": True,
            "device_name": "NVR1",
            "device_model": "TD-3332B4",
            "serial_number": "SN123",
            "firmware": "5.2.3",
            "total_channels": 4,
            "cameras": [{"name": "C1", "address": "10.0.0.1", "port": 9008}],
            "device_info": {},
        }
        result = scan_single_nvr(sample_device, default_config, "protocol")
        assert result.success is True
        assert result.backend == "protocol"
        assert result.device_model == "TD-3332B4"
        assert len(result.cameras) == 1
        mock_scan.assert_called_once()

    @patch("pytvt.sdk_http.sdk_scan")
    def test_sdk_backend(self, mock_sdk, sample_device, default_config):
        mock_sdk.return_value = ScanResult(success=True, backend="sdk")
        # Re-register the mock into the registry so dispatch finds it
        from pytvt.constants import BackendFamily, IntegrationMode
        from pytvt.registry import _registry

        _registry[(BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE)] = mock_sdk
        try:
            result = scan_single_nvr(sample_device, default_config, "sdk")
            assert result.backend == "sdk"
            mock_sdk.assert_called_once_with(sample_device, default_config)
        finally:
            from pytvt.sdk_http import sdk_scan

            _registry[(BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE)] = sdk_scan

    @patch("pytvt.sdk_local.sdk_scan_local")
    def test_sdk_local_backend(self, mock_local, sample_device, default_config):
        mock_local.return_value = ScanResult(success=True, backend="sdk-local")
        from pytvt.constants import BackendFamily, IntegrationMode
        from pytvt.registry import _registry

        _registry[(BackendFamily.SDK, IntegrationMode.DIRECT_SDK)] = mock_local
        try:
            result = scan_single_nvr(sample_device, default_config, "sdk-local")
            assert result.backend == "sdk-local"
            mock_local.assert_called_once_with(sample_device, default_config)
        finally:
            from pytvt.sdk_local import sdk_scan_local

            _registry[(BackendFamily.SDK, IntegrationMode.DIRECT_SDK)] = sdk_scan_local

    @patch("pytvt.scanner._raw_protocol_scan")
    def test_both_backend_protocol_succeeds(self, mock_proto, sample_device, default_config):
        mock_proto.return_value = {
            "success": True,
            "device_name": "",
            "device_model": "",
            "serial_number": "",
            "firmware": "",
            "total_channels": 0,
            "cameras": [],
            "device_info": {},
        }
        result = scan_single_nvr(sample_device, default_config, "both")
        assert result.backend == "protocol"

    @patch("pytvt.sdk_http.sdk_scan")
    @patch("pytvt.scanner._raw_protocol_scan")
    def test_both_backend_falls_back_to_sdk(self, mock_proto, mock_sdk, sample_device, default_config):
        from pytvt.constants import BackendFamily, IntegrationMode
        from pytvt.registry import _registry

        _registry[(BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE)] = mock_sdk
        mock_proto.return_value = {
            "success": False,
            "error": "timeout",
            "device_name": "",
            "device_model": "",
            "serial_number": "",
            "firmware": "",
            "total_channels": 0,
            "cameras": [],
            "device_info": {},
        }
        mock_sdk.return_value = ScanResult(success=True, backend="sdk")
        try:
            result = scan_single_nvr(sample_device, default_config, "both")
            assert result.success is True
            assert result.backend == "sdk"
        finally:
            from pytvt.sdk_http import sdk_scan

            _registry[(BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE)] = sdk_scan

    @patch("pytvt.sdk_http.sdk_scan")
    @patch("pytvt.scanner._raw_protocol_scan")
    def test_both_backend_both_fail(self, mock_proto, mock_sdk, sample_device, default_config):
        from pytvt.constants import BackendFamily, IntegrationMode
        from pytvt.registry import _registry

        _registry[(BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE)] = mock_sdk
        mock_proto.return_value = {
            "success": False,
            "error": "proto fail",
            "device_name": "",
            "device_model": "",
            "serial_number": "",
            "firmware": "",
            "total_channels": 0,
            "cameras": [],
            "device_info": {},
        }
        mock_sdk.return_value = ScanResult(success=False, error="sdk fail", backend="sdk")
        try:
            result = scan_single_nvr(sample_device, default_config, "both")
            assert result.success is False
            assert "proto fail" in result.error
            assert "sdk fail" in result.error
        finally:
            from pytvt.sdk_http import sdk_scan

            _registry[(BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE)] = sdk_scan

    def test_unknown_backend(self, sample_device, default_config):
        with pytest.raises(ValueError, match="Unknown backend"):
            scan_single_nvr(sample_device, default_config, "invalid")
