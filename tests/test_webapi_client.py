"""Tests for pytvt.web_api.client — WebApiClient with mocked HTTP."""

from __future__ import annotations

import http.client
from base64 import b64encode
from unittest.mock import MagicMock, patch

import pytest

from pytvt.web_api.client import LAPI_BASE, WebApiClient
from pytvt.web_api.errors import (
    AuthenticationError,
    DeviceOfflineError,
    UnsupportedFunctionError,
    WebApiError,
)
from pytvt.web_api.models import (
    ChannelInfo,
    DateTimeInfo,
    DeviceInfo,
    DiskInfo,
    ImageConfig,
    SnapshotResult,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _ok_response(body: bytes, status: int = 200, content_type: str = "application/xml"):
    """Build a mock HTTPResponse."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.getheader.return_value = content_type
    return resp


def _xml_ok(inner: str) -> bytes:
    """Wrap inner XML in a ResponseStatus=200 envelope."""
    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f"<Response>"
        f"  <ResponseStatus>"
        f"    <statusCode>200</statusCode>"
        f"    <subStatusCode>0</subStatusCode>"
        f"  </ResponseStatus>"
        f"  {inner}"
        f"</Response>"
    ).encode()


def _xml_error(status_code: int, sub_code: int = 0, msg: str = "Error") -> bytes:
    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f"<ResponseStatus>"
        f"  <statusCode>{status_code}</statusCode>"
        f"  <subStatusCode>{sub_code}</subStatusCode>"
        f"  <statusString>{msg}</statusString>"
        f"</ResponseStatus>"
    ).encode()


@pytest.fixture()
def client():
    return WebApiClient("192.168.1.100", "admin", "test123")


# ── Constructor ──────────────────────────────────────────────────────


class TestClientInit:
    def test_basic_auth_header(self, client):
        expected = b64encode(b"admin:test123").decode()
        assert client._auth_header == f"Basic {expected}"

    def test_defaults(self, client):
        assert client.port == 80
        assert client.timeout == 10
        assert client.use_https is False
        assert client._supported_apis is None


# ── get_supported_apis ───────────────────────────────────────────────


class TestGetSupportedAPIs:
    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_parses_api_names(self, mock_conn_cls, client):
        body = _xml_ok(
            "<SupportedAPIs>"
            "  <SupportedAPI><apiName>GetDeviceInfo</apiName></SupportedAPI>"
            "  <SupportedAPI><apiName>GetSnapshot</apiName></SupportedAPI>"
            "  <SupportedAPI><apiName>GetChannelInfo</apiName></SupportedAPI>"
            "</SupportedAPIs>"
        )
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body)

        apis = client.get_supported_apis()
        assert "GetDeviceInfo" in apis
        assert "GetSnapshot" in apis
        assert "GetChannelInfo" in apis
        assert client._supported_apis == apis

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_supports_cached(self, mock_conn_cls, client):
        body = _xml_ok("<SupportedAPIs>  <SupportedAPI><apiName>GetDeviceInfo</apiName></SupportedAPI></SupportedAPIs>")
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body)

        assert client.supports("GetDeviceInfo") is True
        assert client.supports("GetSnapshot") is False
        # Only one HTTP call despite two supports() calls
        assert mock_conn.request.call_count == 1


# ── get_device_info ──────────────────────────────────────────────────


class TestGetDeviceInfo:
    def test_legacy_positional_constructor_order_is_preserved(self):
        info = DeviceInfo(
            "name",
            "id",
            "model",
            "serial",
            "mac",
            "firmware",
            "release-date",
            "boot",
            "hardware",
            "NVR",
            11,
            12,
            13,
            14,
            15,
            16,
            17,
        )

        assert info.p2p_security_code == ""
        assert info.telecontrol_id == 11
        assert info.video_input_num == 12
        assert info.video_output_num == 13
        assert info.audio_input_num == 14
        assert info.audio_output_num == 15
        assert info.alarm_input_num == 16
        assert info.alarm_output_num == 17

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_parses_device_info(self, mock_conn_cls, client):
        body = _xml_ok(
            "<DeviceInfo>"
            "  <deviceName>TD-3316H2</deviceName>"
            "  <deviceID>DEV001</deviceID>"
            "  <deviceModel>TD-3316H2</deviceModel>"
            "  <serialNumber>SN123456789</serialNumber>"
            "  <macAddress>58:5B:69:AA:BB:CC</macAddress>"
            "  <firmwareVersion>5.0.0.20240101</firmwareVersion>"
            "  <deviceType>NVR</deviceType>"
            "  <videoInputNum>16</videoInputNum>"
            "  <audioInputNum>1</audioInputNum>"
            "</DeviceInfo>"
        )
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body)

        info = client.get_device_info()
        assert isinstance(info, DeviceInfo)
        assert info.device_name == "TD-3316H2"
        assert info.serial_number == "SN123456789"
        assert info.mac_address == "58:5B:69:AA:BB:CC"
        assert info.device_type == "NVR"
        assert info.video_input_num == 16
        assert info.audio_input_num == 1

    @pytest.mark.parametrize(
        "element_name",
        [
            "natSecurityCode",
            "nat_security_code",
            "securityCode",
            "security_code",
            "verifyCode",
            "verify_code",
            "verificationCode",
        ],
    )
    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_parses_p2p_security_code_aliases(self, mock_conn_cls, client, element_name):
        body = _xml_ok(f"<DeviceInfo>  <{element_name}>P2P-CODE-1</{element_name}></DeviceInfo>")
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body)

        info = client.get_device_info()

        assert info.p2p_security_code == "P2P-CODE-1"
        assert "P2P-CODE-1" not in repr(info)


# ── get_channel_info ─────────────────────────────────────────────────


class TestGetChannelInfo:
    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_parses_multiple_channels(self, mock_conn_cls, client):
        body = _xml_ok(
            "<ChannelInfoList>"
            "  <ChannelInfo>"
            "    <channelID>1</channelID>"
            "    <channelName>Front Door</channelName>"
            "    <ipAddress>10.0.0.101</ipAddress>"
            "    <channelType>digital</channelType>"
            "    <online>true</online>"
            "  </ChannelInfo>"
            "  <ChannelInfo>"
            "    <channelID>2</channelID>"
            "    <channelName>Back Yard</channelName>"
            "    <online>false</online>"
            "  </ChannelInfo>"
            "</ChannelInfoList>"
        )
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body)

        channels = client.get_channel_info()
        assert len(channels) == 2
        assert channels[0].channel_id == 1
        assert channels[0].channel_name == "Front Door"
        assert channels[0].online is True
        assert channels[1].channel_id == 2
        assert channels[1].online is False


# ── get_disk_info ────────────────────────────────────────────────────


class TestGetDiskInfo:
    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_parses_disks(self, mock_conn_cls, client):
        body = _xml_ok(
            "<DiskInfoList>"
            "  <DiskInfo>"
            "    <diskID>1</diskID>"
            "    <diskName>SATA1</diskName>"
            "    <diskType>SATA</diskType>"
            "    <status>Normal</status>"
            "    <capacity>1024000</capacity>"
            "    <freeSpace>512000</freeSpace>"
            "    <property>ReadWrite</property>"
            "  </DiskInfo>"
            "</DiskInfoList>"
        )
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body)

        disks = client.get_disk_info()
        assert len(disks) == 1
        assert disks[0].disk_id == 1
        assert disks[0].status == "Normal"
        assert disks[0].capacity_mb == 1024000
        assert disks[0].free_mb == 512000


# ── get_date_and_time ────────────────────────────────────────────────


class TestGetDateAndTime:
    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_parses_datetime(self, mock_conn_cls, client):
        body = _xml_ok(
            "<DateAndTime>"
            "  <dateTimeMode>NTP</dateTimeMode>"
            "  <localTime>2026-04-07T12:00:00</localTime>"
            "  <timeZone>CST-8:00:00</timeZone>"
            "  <ntpServerAddress>pool.ntp.org</ntpServerAddress>"
            "  <ntpPort>123</ntpPort>"
            "  <ntpRefreshInterval>60</ntpRefreshInterval>"
            "</DateAndTime>"
        )
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body)

        dt = client.get_date_and_time()
        assert isinstance(dt, DateTimeInfo)
        assert dt.mode == "NTP"
        assert dt.ntp_server == "pool.ntp.org"
        assert dt.ntp_interval == 60


# ── modify_password ──────────────────────────────────────────────────


class TestModifyPassword:
    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_updates_stored_credentials(self, mock_conn_cls, client):
        body = _xml_ok("")
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body)

        client.modify_password("test123", "NewPass456!")

        assert client.password == "NewPass456!"
        expected = b64encode(b"admin:NewPass456!").decode()
        assert client._auth_header == f"Basic {expected}"

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_sends_correct_xml(self, mock_conn_cls, client):
        body = _xml_ok("")
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body)

        client.modify_password("test123", "NewPass456!")

        call_args = mock_conn.request.call_args
        sent_body = call_args[1].get("body") or call_args[0][2]
        if isinstance(sent_body, bytes):
            sent_body = sent_body.decode()
        assert "<oldPassword>test123</oldPassword>" in sent_body
        assert "<newPassword>NewPass456!</newPassword>" in sent_body
        assert "<userName>admin</userName>" in sent_body


# ── get_image_config ─────────────────────────────────────────────────


class TestGetImageConfig:
    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_parses_image_config(self, mock_conn_cls, client):
        body = _xml_ok(
            "<ImageConfig>"
            "  <brightness>128</brightness>"
            "  <contrast>64</contrast>"
            "  <saturation>50</saturation>"
            "  <sharpness>30</sharpness>"
            "  <WDREnabled>true</WDREnabled>"
            "  <WDRLevel>80</WDRLevel>"
            "</ImageConfig>"
        )
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body)

        cfg = client.get_image_config(channel_id=1)
        assert isinstance(cfg, ImageConfig)
        assert cfg.brightness == 128
        assert cfg.wdr_enabled is True
        assert cfg.wdr_level == 80


# ── Snapshot routing ─────────────────────────────────────────────────


class TestSnapshotRouting:
    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_webapi_snapshot_success(self, mock_conn_cls, client):
        jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 200  # fake JPEG
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(jpeg_bytes, content_type="image/jpeg")

        result = client.get_snapshot_webapi(channel_id=1)
        assert result.success is True
        assert result.method == "webapi"
        assert result.image_data == jpeg_bytes

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_webapi_snapshot_too_small(self, mock_conn_cls, client):
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(b"tiny", content_type="image/jpeg")

        result = client.get_snapshot_webapi(channel_id=1)
        assert result.success is False
        assert "invalid" in result.error.lower() or "empty" in result.error.lower()

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_get_snapshot_tries_both_methods(self, mock_conn_cls, client):
        """When GetSnapshot fails, falls back to GetSnapshotByTime."""
        client._supported_apis = {"GetSnapshot", "GetSnapshotByTime"}

        jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 200
        call_count = 0

        def side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call (GetSnapshot via _get_raw) — return small/bad data
                return _ok_response(b"bad", content_type="text/html")
            else:
                # Second call (GetSnapshotByTime) — return JPEG
                return _ok_response(jpeg_bytes, 200, "image/jpeg")

        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.side_effect = side_effect

        result = client.get_snapshot(channel_id=1)
        assert result.success is True
        assert result.method == "webapi_by_time"

    def test_get_snapshot_no_supported_method(self, client):
        client._supported_apis = set()
        result = client.get_snapshot()
        assert result.success is False
        assert "No supported" in result.error

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_rtsp_fallback_no_url(self, mock_conn_cls, client):
        """Without rtsp_url, RTSP fallback is skipped."""
        client._supported_apis = set()

        result = client.get_snapshot_with_rtsp_fallback(channel_id=1)
        assert result.success is False
        assert "RTSP fallback not configured" in result.error


# ── Error handling ───────────────────────────────────────────────────


class TestErrorHandling:
    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_401_raises_auth_error(self, mock_conn_cls, client):
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(b"", status=401)

        with pytest.raises(AuthenticationError):
            client.get_device_info()

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_connection_refused_raises_offline(self, mock_conn_cls, client):
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.request.side_effect = ConnectionRefusedError("refused")

        with pytest.raises(DeviceOfflineError):
            client.get_device_info()

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_timeout_raises_offline(self, mock_conn_cls, client):
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.request.side_effect = TimeoutError("timed out")

        with pytest.raises(DeviceOfflineError):
            client.get_device_info()

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_tvt_error_code_in_response(self, mock_conn_cls, client):
        body = _xml_error(400, 12, "Not supported")
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(body, status=200)

        with pytest.raises(UnsupportedFunctionError):
            client.get_device_info()

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_http_500_raises_webapi_error(self, mock_conn_cls, client):
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _ok_response(b"Server Error", status=500)

        with pytest.raises(WebApiError):
            client.get_device_info()
