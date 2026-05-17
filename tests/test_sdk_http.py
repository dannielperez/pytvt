"""Tests for pytvt.device_sdk.sdk_http — HTTP API backend (mocked)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pytvt.models import DeviceEntry, ScannerConfig
from pytvt.device_sdk.sdk_http import sdk_scan


class TestSdkScan:
    @patch("pytvt.device_sdk.sdk_http.urllib.request.urlopen")
    def test_success(self, mock_urlopen, sample_device, default_config):
        response_data = json.dumps(
            {
                "success": True,
                "device_name": "NVR-SDK",
                "device_model": "TD-3332B4",
                "serial_number": "SN456",
                "firmware": "5.2.3",
                "total_channels": 2,
                "cameras": [
                    {"name": "C1", "address": "10.0.0.1", "port": 9008, "status": "Online"},
                ],
            }
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = sdk_scan(sample_device, default_config)
        assert result.success is True
        assert result.backend == "sdk"
        assert result.device_model == "TD-3332B4"
        assert len(result.cameras) == 1

    @patch("pytvt.device_sdk.sdk_http.urllib.request.urlopen")
    def test_connection_error(self, mock_urlopen, sample_device, default_config):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        result = sdk_scan(sample_device, default_config)
        assert result.success is False
        assert "connection error" in result.error.lower()

    @patch("pytvt.device_sdk.sdk_http.urllib.request.urlopen")
    def test_timeout(self, mock_urlopen, sample_device, default_config):
        mock_urlopen.side_effect = TimeoutError()
        result = sdk_scan(sample_device, default_config)
        assert result.success is False
        assert "timeout" in result.error.lower()

    @patch("pytvt.device_sdk.sdk_http.urllib.request.urlopen")
    def test_invalid_json(self, mock_urlopen, sample_device, default_config):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = sdk_scan(sample_device, default_config)
        assert result.success is False
        assert "json" in result.error.lower()
