"""Tests for pytvt.device_sdk.sdk_local — direct Python SDK backend."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pathlib import Path

from pytvt.models import ScannerConfig
from pytvt.device_sdk.client import DeviceInfo, NetSdkError
from pytvt.device_sdk.loader import NetSdkUnavailable
from pytvt.device_sdk.sdk_local import scan_nvr_payload, sdk_scan_local


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class TestScanNvrPayload:
    def test_success(self):
        device_info = DeviceInfo(
            serial_number="ABC123",
            product="TD-3332B4",
            device_name="NVR-01",
            device_type=1,
            mac="58:5B:69:AA:BB:CC",
            ip="10.0.0.1",
            port=6036,
            firmware="5.2.3.190",
            hardware_version="HW-1",
            kernel_version="KR-1",
            build_date="2026-04-24",
            video_inputs=4,
            audio_inputs=0,
            sensor_inputs=0,
            sensor_outputs=0,
        )
        cameras = [
            SimpleNamespace(
                channel=1,
                name="Lobby",
                ip="192.168.1.10",
                port=9008,
                online=True,
                manufacturer="TVT",
                model="TD-9544S4",
            )
        ]

        session = MagicMock()
        session.device_info.return_value = device_info
        session.ipc_info.return_value = cameras

        login_context = MagicMock()
        login_context.__enter__.return_value = session
        login_context.__exit__.return_value = None

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = None
        client.login.return_value = login_context

        with patch("pytvt.device_sdk.sdk_local.NetSdkClient", return_value=client) as mock_client:
            result = scan_nvr_payload(
                "10.0.0.1",
                port=6036,
                username="admin",
                password="secret",
                sdk_path="/opt/tvt-sdk",
                max_channels=32,
            )

        assert result["success"] is True
        assert result["device_name"] == "NVR-01"
        assert result["total_channels"] == 1
        assert result["cameras"][0]["status"] == "Online"
        assert set(result) == {
            "nvr_ip",
            "nvr_port",
            "success",
            "device_name",
            "device_model",
            "serial_number",
            "firmware",
            "total_channels",
            "cameras",
            "error",
        }
        mock_client.assert_called_once_with(sdk_path="/opt/tvt-sdk", connect_timeout=10_000, recv_timeout=10_000)
        client.login.assert_called_once_with("10.0.0.1", "admin", "secret", port=6036)
        session.ipc_info.assert_called_once_with(max_channels=32)

    def test_ipc_failure_still_reports_reachable_device(self):
        device_info = DeviceInfo(
            serial_number="ABC123",
            product="TD-3332B4",
            device_name="NVR-01",
            device_type=1,
            mac="58:5B:69:AA:BB:CC",
            ip="10.0.0.1",
            port=6036,
            firmware="5.2.3.190",
            hardware_version="HW-1",
            kernel_version="KR-1",
            build_date="2026-04-24",
            video_inputs=16,
            audio_inputs=0,
            sensor_inputs=0,
            sensor_outputs=0,
        )

        session = MagicMock()
        session.device_info.return_value = device_info
        session.ipc_info.side_effect = NetSdkError("GetDeviceIPCInfo")

        login_context = MagicMock()
        login_context.__enter__.return_value = session

        client = MagicMock()
        client.__enter__.return_value = client
        client.login.return_value = login_context

        with patch("pytvt.device_sdk.sdk_local.NetSdkClient", return_value=client):
            result = scan_nvr_payload("10.0.0.1", password="secret")

        assert result["success"] is True
        assert result["total_channels"] == 16
        assert "Could not retrieve IPC info" in (result["error"] or "")

    def test_sdk_unavailable_returns_error(self):
        with patch("pytvt.device_sdk.sdk_local.NetSdkClient", side_effect=NetSdkUnavailable("missing SDK")):
            result = scan_nvr_payload("10.0.0.1")

        assert result["success"] is False
        assert result["error"] == "missing SDK"

    def test_login_failure_returns_error(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = None
        client.login.side_effect = NetSdkError("Login failed")

        with patch("pytvt.device_sdk.sdk_local.NetSdkClient", return_value=client):
            result = scan_nvr_payload("10.0.0.1", username="admin", password="wrong")

        assert result["success"] is False
        assert result["error"] == "Login failed"

    def test_invalid_argument_error_returns_failure(self):
        with patch("pytvt.device_sdk.sdk_local.NetSdkClient", side_effect=ValueError("bad sdk path")):
            result = scan_nvr_payload("10.0.0.1", sdk_path="/bad/path")

        assert result["success"] is False
        assert result["error"] == "bad sdk path"

    def test_payload_matches_legacy_bridge_contract(self):
        fixture_payload = json.loads((FIXTURES_DIR / "legacy_scan_nvr_payload.json").read_text(encoding="utf-8"))

        device_info = DeviceInfo(
            serial_number="ABC123",
            product="TD-3332B4",
            device_name="NVR-01",
            device_type=1,
            mac="58:5B:69:AA:BB:CC",
            ip="10.0.0.1",
            port=6036,
            firmware="5.2.3.190",
            hardware_version="HW-1",
            kernel_version="KR-1",
            build_date="2026-04-24",
            video_inputs=4,
            audio_inputs=0,
            sensor_inputs=0,
            sensor_outputs=0,
        )
        cameras = [
            SimpleNamespace(
                channel=1,
                name="Lobby",
                ip="192.168.1.10",
                port=9008,
                online=True,
                manufacturer="TVT",
                model="TD-9544S4",
            )
        ]

        session = MagicMock()
        session.device_info.return_value = device_info
        session.ipc_info.return_value = cameras

        login_context = MagicMock()
        login_context.__enter__.return_value = session
        login_context.__exit__.return_value = None

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = None
        client.login.return_value = login_context

        with patch("pytvt.device_sdk.sdk_local.NetSdkClient", return_value=client):
            payload = scan_nvr_payload("10.0.0.1", password="secret")

        assert list(payload.keys()) == list(fixture_payload.keys())
        assert payload == fixture_payload
        for key, value in fixture_payload.items():
            assert isinstance(payload[key], type(value))


class TestSdkScanLocal:
    def test_sdk_scan_local_maps_payload_to_scan_result(self, sample_device):
        cfg = ScannerConfig(username="admin", password="test123", sdk_path="/opt/tvt-sdk")

        with patch(
            "pytvt.device_sdk.sdk_local.scan_nvr_payload",
            return_value={
                "success": True,
                "device_name": "NVR-01",
                "device_model": "TD-3332B4",
                "serial_number": "ABC123",
                "firmware": "5.2.3.190",
                "total_channels": 1,
                "cameras": [
                    {
                        "channel": 1,
                        "name": "Lobby",
                        "address": "192.168.1.10",
                        "port": 9008,
                        "status": "Online",
                        "protocol": "TVT",
                        "model": "TD-9544S4",
                    }
                ],
                "error": None,
            },
        ) as mock_payload:
            result = sdk_scan_local(sample_device, cfg)

        assert result.success is True
        assert result.backend == "sdk-local"
        assert result.cameras[0].name == "Lobby"
        assert result.device_info == {}
        mock_payload.assert_called_once_with(
            "10.0.0.1",
            port=6036,
            username="admin",
            password="test123",
            sdk_path="/opt/tvt-sdk",
            max_channels=64,
        )
