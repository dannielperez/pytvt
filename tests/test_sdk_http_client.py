"""Tests for SdkHttpClient."""

from __future__ import annotations

import json
from http.client import HTTPResponse
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from pytvt.sdk_http_client import (
    CommandResult,
    DeviceInfoResult,
    DeviceTimeResult,
    RtspUrlResult,
    SdkHttpClient,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> SdkHttpClient:
    return SdkHttpClient("http://test:3000", timeout=5)


def _mock_response(data: dict | bytes, status: int = 200, content_type: str = "application/json") -> MagicMock:
    """Create a mock urllib response."""
    if isinstance(data, dict):
        body = json.dumps(data).encode()
    else:
        body = data

    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.headers = {"Content-Type": content_type}
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


CREDS = ("10.0.0.1", "admin", "pass123")


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_healthy(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({"status": "ok"})
            assert client.health() is True

    def test_unhealthy(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = ConnectionRefusedError
            assert client.health() is False


# ---------------------------------------------------------------------------
# device_info
# ---------------------------------------------------------------------------

class TestDeviceInfo:
    def test_success(self, client: SdkHttpClient) -> None:
        resp_data = {
            "success": True,
            "device_name": "NVR-01",
            "device_model": "ED9516",
            "serial_number": "SN123",
            "firmware": "v4.0",
            "hardware_version": "HW1",
            "kernel_version": "K1",
            "mcu_version": "MCU1",
            "video_inputs": 16,
            "audio_inputs": 1,
            "sensor_inputs": 4,
            "sensor_outputs": 2,
            "device_type": 3,
            "error": None,
        }
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(resp_data)
            result = client.device_info(*CREDS)

        assert isinstance(result, DeviceInfoResult)
        assert result.success is True
        assert result.device_name == "NVR-01"
        assert result.serial_number == "SN123"
        assert result.video_inputs == 16
        assert result.error is None

    def test_connection_error(self, client: SdkHttpClient) -> None:
        import urllib.error
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.URLError("refused")
            result = client.device_info(*CREDS)

        assert result.success is False
        assert "Connection error" in (result.error or "")

    def test_timeout(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = TimeoutError
            result = client.device_info(*CREDS)

        assert result.success is False
        assert "Timeout" in (result.error or "")


# ---------------------------------------------------------------------------
# device_time
# ---------------------------------------------------------------------------

class TestDeviceTime:
    def test_get_time(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({
                "success": True,
                "action": "get",
                "device_time": "2025-01-15T10:30:00",
                "error": None,
            })
            result = client.device_time(*CREDS)

        assert result.success is True
        assert result.action == "get"
        assert result.device_time == "2025-01-15T10:30:00"

    def test_set_time(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({
                "success": True,
                "action": "set",
                "timestamp": 1700000000,
                "error": None,
            })
            result = client.device_time(*CREDS, set_timestamp=1700000000)

        assert result.success is True
        assert result.action == "set"
        assert result.timestamp == 1700000000

    def test_set_sends_timestamp_in_payload(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({"success": True, "action": "set", "error": None})
            client.device_time(*CREDS, set_timestamp=1700000000)

            call_args = mock_open.call_args
            req = call_args[0][0]
            body = json.loads(req.data.decode())
            assert body["set_timestamp"] == 1700000000


# ---------------------------------------------------------------------------
# reboot
# ---------------------------------------------------------------------------

class TestReboot:
    def test_success(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({"success": True, "error": None})
            result = client.reboot(*CREDS)

        assert isinstance(result, CommandResult)
        assert result.success is True

    def test_failure(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({"success": False, "error": "Device busy"})
            result = client.reboot(*CREDS)

        assert result.success is False
        assert result.error == "Device busy"


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_success_returns_bytes(self, client: SdkHttpClient) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # fake JPEG
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(jpeg, content_type="image/jpeg")
            result = client.snapshot(*CREDS, channel=0)

        assert result is not None
        assert result[:4] == b"\xff\xd8\xff\xe0"
        assert len(result) == 104

    def test_failure_returns_none(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response(
                {"success": False, "error": "fail"}, content_type="application/json",
            )
            result = client.snapshot(*CREDS, channel=0)

        assert result is None

    def test_connection_error_returns_none(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = ConnectionRefusedError
            result = client.snapshot(*CREDS)

        assert result is None


# ---------------------------------------------------------------------------
# rtsp_url
# ---------------------------------------------------------------------------

class TestRtspUrl:
    def test_main_stream(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({
                "success": True,
                "rtsp_url": "rtsp://10.0.0.1:554/chID=0&streamType=main",
                "error": None,
            })
            result = client.rtsp_url(*CREDS, channel=0, stream_type=0)

        assert result.success is True
        assert result.rtsp_url == "rtsp://10.0.0.1:554/chID=0&streamType=main"

    def test_sub_stream(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({
                "success": True,
                "rtsp_url": "rtsp://10.0.0.1:554/chID=1&streamType=sub",
                "error": None,
            })
            result = client.rtsp_url(*CREDS, channel=1, stream_type=1)

        assert isinstance(result, RtspUrlResult)
        assert "sub" in (result.rtsp_url or "")


# ---------------------------------------------------------------------------
# ptz
# ---------------------------------------------------------------------------

class TestPtz:
    def test_stop(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({"success": True, "error": None})
            result = client.ptz(*CREDS, channel=0, command=0)

        assert result.success is True

    def test_sends_correct_payload(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({"success": True, "error": None})
            client.ptz(*CREDS, channel=2, command=12, speed=6)

            req = mock_open.call_args[0][0]
            body = json.loads(req.data.decode())
            assert body["channel"] == 2
            assert body["command"] == 12
            assert body["speed"] == 6


# ---------------------------------------------------------------------------
# ptz_preset
# ---------------------------------------------------------------------------

class TestPtzPreset:
    def test_goto(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({"success": True, "error": None})
            result = client.ptz_preset(*CREDS, channel=0, command=16, preset_index=1)

        assert result.success is True

    def test_payload(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({"success": True, "error": None})
            client.ptz_preset(*CREDS, channel=0, command=15, preset_index=5)

            req = mock_open.call_args[0][0]
            body = json.loads(req.data.decode())
            assert body["command"] == 15
            assert body["preset_index"] == 5


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

class TestScan:
    def test_success(self, client: SdkHttpClient) -> None:
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.return_value = _mock_response({
                "success": True,
                "device_name": "NVR",
                "cameras": [{"channel": 0, "name": "Cam1"}],
            })
            result = client.scan(*CREDS)

        assert result["success"] is True
        assert len(result["cameras"]) == 1

    def test_connection_error(self, client: SdkHttpClient) -> None:
        import urllib.error
        with patch("pytvt.sdk_http_client.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.URLError("refused")
            result = client.scan(*CREDS)

        assert result["success"] is False


# ---------------------------------------------------------------------------
# constructor / defaults
# ---------------------------------------------------------------------------

class TestClientInit:
    def test_default_url(self) -> None:
        c = SdkHttpClient()
        assert c._base_url == "http://localhost:3000"

    def test_custom_url_strips_trailing_slash(self) -> None:
        c = SdkHttpClient("http://myhost:4000/")
        assert c._base_url == "http://myhost:4000"

    def test_custom_timeout(self) -> None:
        c = SdkHttpClient(timeout=60)
        assert c._timeout == 60

    def test_port_default_in_payload(self, client: SdkHttpClient) -> None:
        payload = json.loads(client._connect_payload("10.0.0.1", "user", "pass"))
        assert payload["port"] == 6036

    def test_custom_port_in_payload(self, client: SdkHttpClient) -> None:
        payload = json.loads(client._connect_payload("10.0.0.1", "user", "pass", port=9008))
        assert payload["port"] == 9008
