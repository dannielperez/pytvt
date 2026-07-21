"""Tests for pytvt.device_sdk.client — NetSdkClient with mocked native library."""

from __future__ import annotations

import ctypes as ct
import re
from datetime import datetime
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from pytvt.device_sdk import bindings as sdk
from pytvt.device_sdk.client import (
    AlarmOutStatus,
    CallLogEntry,
    ChannelStatus,
    CloudUpgradeStatus,
    DeviceInfo,
    DeviceSession,
    DeviceSupport,
    DeviceUser,
    DiscoveredDevice,
    DiskInfo,
    EncodeStream,
    IpcInfo,
    LogEntry,
    MotionConfig,
    NatLoginFailed,
    NatTimeoutError,
    NatUnavailableError,
    NetSdkCapabilityError,
    NetSdkClient,
    NetSdkError,
    NodeEncodeInfo,
    NvrChannelInfo,
    RecordDevice,
    RecordingDateRange,
    RecordingFile,
    RecordSchedule,
    RecordStatus,
    RecordStatusEx,
    SmartSupport,
    TVTClient,
)
from pytvt.device_sdk.constants import (
    ConnectType,
    DiskProperty,
    DiskStatus,
    PtzCommand,
    PtzSpeed,
    RecordType,
    RollingGateExecute,
    SdkError,
    StreamType,
    TripwireDirection,
)
from pytvt.device_sdk.loader import NetSdkUnavailable
from pytvt.device_sdk.types import (
    DD_TIME,
    NET_SDK_ALRAM_OUT_STATUS,
    NET_SDK_CH_DEVICE_STATUS,
    NET_SDK_DEV_SUPPORT,
    NET_SDK_DEVICE_DISCOVERY_INFO,
    NET_SDK_DEVICEINFO,
    NET_SDK_DISK_INFO,
    NET_SDK_IPC_DEVICE_INFO,
    NET_SDK_JPEGPARA,
    NET_SDK_LOG,
    NET_SDK_NVR_DISKREC_DATE_ITEM,
    NET_SDK_REC_FILE,
    NET_SDK_SMART_SUPPORT,
)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def mock_lib():
    """Create a mock library that passes all SDK calls."""
    lib = MagicMock()
    lib.NET_SDK_Init.return_value = True
    lib.NET_SDK_Cleanup.return_value = True
    lib.NET_SDK_SetConnectTime.return_value = True
    lib.NET_SDK_SetReconnect.return_value = True
    lib.NET_SDK_GetSDKVersion.return_value = (1 << 24) | (3 << 16) | 2  # 1.3.2
    lib.NET_SDK_GetSDKBuildVersion.return_value = 20260116
    lib.NET_SDK_GetLastError.return_value = 0
    lib.NET_SDK_Login.return_value = 1  # valid handle
    lib.NET_SDK_LoginEx.return_value = 2  # valid NAT handle
    lib.NET_SDK_SetNat2Addr.return_value = True
    lib.NET_SDK_Logout.return_value = True
    return lib


@pytest.fixture
def client(mock_lib):
    """Create a NetSdkClient with mocked native library."""
    with patch("pytvt.device_sdk.client.load_sdk", return_value=mock_lib):
        c = NetSdkClient()
        yield c
        c.cleanup()


@pytest.fixture
def session(mock_lib, client):
    """Create a DeviceSession with mocked library."""
    sdk.bind(mock_lib)
    return DeviceSession(handle=1, client=client)


# ── NetSdkClient init/cleanup ──────────────────────────────────────


class TestNetSdkClientInit:
    def test_init_calls_sdk(self, mock_lib):
        with patch("pytvt.device_sdk.client.load_sdk", return_value=mock_lib):
            c = NetSdkClient()
            mock_lib.NET_SDK_Init.assert_called_once()
            mock_lib.NET_SDK_SetConnectTime.assert_called_once_with(5000, 5000)
            mock_lib.NET_SDK_SetReconnect.assert_called_once_with(0, False)
            c.cleanup()

    def test_init_failure(self, mock_lib):
        mock_lib.NET_SDK_Init.return_value = False
        with patch("pytvt.device_sdk.client.load_sdk", return_value=mock_lib), pytest.raises(NetSdkError, match="Init"):
            NetSdkClient()

    def test_context_manager(self, mock_lib):
        with patch("pytvt.device_sdk.client.load_sdk", return_value=mock_lib):
            with NetSdkClient() as c:
                assert c._lib is not None
            mock_lib.NET_SDK_Cleanup.assert_called()

    def test_custom_timeouts(self, mock_lib):
        with patch("pytvt.device_sdk.client.load_sdk", return_value=mock_lib):
            c = NetSdkClient(connect_timeout=10000, recv_timeout=8000, reconnect_interval=5000)
            mock_lib.NET_SDK_SetConnectTime.assert_called_once_with(10000, 8000)
            mock_lib.NET_SDK_SetReconnect.assert_called_once_with(5000, True)
            c.cleanup()


class TestNetSdkClientVersion:
    def test_sdk_version(self, client, mock_lib):
        assert client.sdk_version() == "1.3.2"

    def test_sdk_build_version(self, client, mock_lib):
        assert client.sdk_build_version() == 20260116


# ── Discovery ───────────────────────────────────────────────────────


class TestDiscover:
    def test_discover_empty(self, client, mock_lib):
        mock_lib.NET_SDK_DiscoverDevice.return_value = 0
        result = client.discover()
        assert result == []

    def test_discover_error(self, client, mock_lib):
        mock_lib.NET_SDK_DiscoverDevice.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = 8
        with pytest.raises(NetSdkError, match="DiscoverDevice"):
            client.discover()


# ── Activation ──────────────────────────────────────────────────────


class TestActivate:
    def test_activate_success(self, client, mock_lib):
        mock_lib.NET_SDK_ActiveDevice.return_value = True
        client.activate("10.0.0.1", 9008, "NewPass123!")
        mock_lib.NET_SDK_ActiveDevice.assert_called_once()

    def test_activate_failure(self, client, mock_lib):
        mock_lib.NET_SDK_ActiveDevice.return_value = False
        mock_lib.NET_SDK_GetLastError.return_value = 20
        with pytest.raises(NetSdkError, match="ActiveDevice"):
            client.activate("10.0.0.1", 9008, "weak")

    def test_activate_by_mac(self, client, mock_lib):
        mock_lib.NET_SDK_ActiveDeviceByMac.return_value = True
        client.activate_by_mac("AA:BB:CC:DD:EE:FF", "NewPass123!")
        mock_lib.NET_SDK_ActiveDeviceByMac.assert_called_once()

    def test_set_device_ip_by_mac_prefers_new_api(self, client, mock_lib):
        mock_lib.NET_SDK_SetDeviceIP.return_value = True

        client.set_device_ip_by_mac(
            "aa:bb:cc:dd:ee:ff",
            "Secret123!",
            ip="192.168.1.50",
            netmask="255.255.255.0",
            gateway="192.168.1.1",
            dns1="8.8.8.8",
            dns2="1.1.1.1",
        )

        mock_lib.NET_SDK_SetDeviceIP.assert_called_once_with(
            b"AA:BB:CC:DD:EE:FF",
            b"Secret123!",
            b"192.168.1.50",
            b"255.255.255.0",
            b"192.168.1.1",
            b"8.8.8.8",
            b"1.1.1.1",
        )

    def test_set_device_ip_by_mac_falls_back_to_legacy_api(self, client, mock_lib):
        del mock_lib.NET_SDK_SetDeviceIP
        mock_lib.NET_SDK_ModifyDeviceNetInfo.return_value = True

        client.set_device_ip_by_mac(
            "AA:BB:CC:DD:EE:FF",
            "Secret123!",
            ip="192.168.1.51",
            netmask="255.255.255.0",
            gateway="192.168.1.1",
            dns1="8.8.4.4",
            dhcp=True,
        )

        mock_lib.NET_SDK_ModifyDeviceNetInfo.assert_called_once()

    def test_set_device_ip_by_mac_raises_when_unavailable(self, client, mock_lib):
        del mock_lib.NET_SDK_SetDeviceIP
        del mock_lib.NET_SDK_ModifyDeviceNetInfo

        with pytest.raises(NetSdkUnavailable, match="does not export"):
            client.set_device_ip_by_mac("AA:BB:CC:DD:EE:FF", "Secret123!", ip="192.168.1.52")


# ── Login ───────────────────────────────────────────────────────────


class TestLogin:
    def test_login_success(self, client, mock_lib):
        session = client.login("10.0.0.1", "admin", "pass")
        assert session.handle == 1
        mock_lib.NET_SDK_Login.assert_called_once()

    def test_login_failure(self, client, mock_lib):
        mock_lib.NET_SDK_Login.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = 1  # PASSWORD_ERROR
        with pytest.raises(NetSdkError, match="Login"):
            client.login("10.0.0.1", "admin", "wrong")

    def test_login_custom_port(self, client, mock_lib):
        client.login("10.0.0.1", "admin", "pass", port=9009)
        call_args = mock_lib.NET_SDK_Login.call_args
        assert call_args[0][1] == 9009


class TestNatLogin:
    def test_login_nat_success(self, client, mock_lib):
        with patch("pytvt.device_sdk.client.ensure_nat_support"):
            session = client.login_nat("ABC123456", "admin", "pass")

        assert session.handle == 2
        assert session.connection_method == "nat"
        call_args = mock_lib.NET_SDK_LoginEx.call_args[0]
        assert call_args[5] == ConnectType.NAT20
        assert call_args[6] == b"ABC123456"

    def test_login_nat_configures_nat20_server(self, client, mock_lib):
        with patch("pytvt.device_sdk.client.ensure_nat_support"):
            client.login_nat(
                "ABC123456",
                "admin",
                "pass",
                nat_server="c2020.autonat.com",
                nat_port=8866,
            )

        mock_lib.NET_SDK_SetNat2Addr.assert_called_once_with(b"c2020.autonat.com", 8866)

    def test_login_nat_invalid_identifier(self, client):
        with pytest.raises(ValueError, match="identifier is required"):
            client.login_nat("", "admin", "pass")

    def test_login_nat_missing_library(self, client):
        with (
            patch("pytvt.device_sdk.client.ensure_nat_support", side_effect=NetSdkUnavailable("missing nat lib")),
            pytest.raises(NatUnavailableError, match="missing nat lib"),
        ):
            client.login_nat("ABC123456", "admin", "pass")

    def test_login_nat_failure(self, client, mock_lib):
        mock_lib.NET_SDK_LoginEx.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = 1
        with patch("pytvt.device_sdk.client.ensure_nat_support"), pytest.raises(NatLoginFailed, match="NAT login"):
            client.login_nat("ABC123456", "admin", "wrong")

    def test_login_nat_timeout(self, client, mock_lib):
        mock_lib.NET_SDK_LoginEx.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = SdkError.NETWORK_RECV_TIMEOUT
        with patch("pytvt.device_sdk.client.ensure_nat_support"), pytest.raises(NatTimeoutError, match="NAT login"):
            client.login_nat("ABC123456", "admin", "pass")


class TestConnectFacade:
    def test_connect_direct_dispatches_to_login(self, client):
        direct_session = MagicMock(spec=DeviceSession)
        with patch.object(client, "login", return_value=direct_session) as mock_login:
            session = client.connect(
                method="direct",
                host="10.0.0.1",
                username="admin",
                password="pass",
            )

        assert session is direct_session
        mock_login.assert_called_once_with("10.0.0.1", "admin", "pass", port=9008)

    def test_connect_nat_falls_back_to_direct(self, client):
        direct_session = MagicMock(spec=DeviceSession)
        with (
            patch.object(client, "login_nat", side_effect=NatTimeoutError("timeout")),
            patch.object(client, "login", return_value=direct_session) as mock_login,
        ):
            session = client.connect(
                method="nat",
                host="10.0.0.1",
                identifier="ABC123456",
                username="admin",
                password="pass",
            )

        assert session is direct_session
        mock_login.assert_called_once_with("10.0.0.1", "admin", "pass", port=9008)

    def test_tvtclient_inherits_connect(self, mock_lib):
        with patch("pytvt.device_sdk.client.load_sdk", return_value=mock_lib):
            client = TVTClient()
            assert isinstance(client, NetSdkClient)
            client.cleanup()


# ── DeviceSession context manager ──────────────────────────────────


class TestDeviceSession:
    def test_context_manager(self, session, mock_lib):
        with session:
            assert session.handle == 1
        mock_lib.NET_SDK_Logout.assert_called_with(1)

    def test_logout_sets_handle_negative(self, session, mock_lib):
        session.logout()
        assert session.handle == -1

    def test_double_logout_safe(self, session, mock_lib):
        session.logout()
        session.logout()  # should not raise
        mock_lib.NET_SDK_Logout.assert_called_once()


# ── DeviceSession.device_info ──────────────────────────────────────


class TestSessionDeviceInfo:
    def test_device_info(self, session, mock_lib):
        def fill_info(handle, info_ptr):
            info = info_ptr._obj if hasattr(info_ptr, "_obj") else info_ptr.contents
            info.szSN = b"ABC123456"
            info.deviceProduct = b"TD-3316E2"
            info.deviceName = b"MyNVR"
            info.deviceType = 3
            info.firmwareVersion = b"V5.0.0"
            info.hardwareVersion = b"HW1.0"
            info.kernelVersion = b"K3.10"
            info.videoInputNum = 16
            info.audioInputNum = 1
            info.sensorInputNum = 4
            info.sensorOutputNum = 2
            return True

        mock_lib.NET_SDK_GetDeviceInfo.side_effect = fill_info
        info = session.device_info()
        assert info.serial_number == "ABC123456"
        assert info.product == "TD-3316E2"
        assert info.video_inputs == 16

    def test_device_info_failure(self, session, mock_lib):
        mock_lib.NET_SDK_GetDeviceInfo.return_value = False
        mock_lib.NET_SDK_GetLastError.return_value = 17
        with pytest.raises(NetSdkError, match="GetDeviceInfo"):
            session.device_info()


# ── DeviceSession.capture_jpeg ─────────────────────────────────────


class TestSessionCapture:
    def test_capture_jpeg(self, session, mock_lib):
        jpeg_data = b"\xff\xd8\xff\xe0" + b"\x00" * 100

        def fill_jpeg(handle, channel, para_ptr, buf, buf_size, returned_ptr):
            ct.memmove(buf, jpeg_data, len(jpeg_data))
            returned_ptr._obj.value = len(jpeg_data)
            return True

        mock_lib.NET_SDK_CaptureJPEGData_V2.side_effect = fill_jpeg
        result = session.capture_jpeg(channel=0)
        assert result[:4] == b"\xff\xd8\xff\xe0"
        assert len(result) == len(jpeg_data)


# ── DeviceSession.ptz ──────────────────────────────────────────────


class TestSessionPtz:
    def test_ptz_left(self, session, mock_lib):
        mock_lib.NET_SDK_PTZControl_Other.return_value = True
        session.ptz(PtzCommand.LEFT, channel=0, speed=PtzSpeed.SPEED_4)
        mock_lib.NET_SDK_PTZControl_Other.assert_called_once_with(
            1,
            0,
            PtzCommand.LEFT,
            PtzSpeed.SPEED_4,
        )

    def test_ptz_failure(self, session, mock_lib):
        mock_lib.NET_SDK_PTZControl_Other.return_value = False
        mock_lib.NET_SDK_GetLastError.return_value = 26
        with pytest.raises(NetSdkError, match="PTZControl"):
            session.ptz(PtzCommand.LEFT)

    def test_ptz_preset(self, session, mock_lib):
        mock_lib.NET_SDK_PTZPreset_Other.return_value = True
        session.ptz_preset(PtzCommand.PRESET_GO, 5, channel=0)
        mock_lib.NET_SDK_PTZPreset_Other.assert_called_once_with(
            1,
            0,
            PtzCommand.PRESET_GO,
            5,
        )


# ── DeviceSession.rtsp_url ─────────────────────────────────────────


class TestSessionRtspUrl:
    def test_rtsp_url(self, session, mock_lib):
        def fill_url(handle, channel, stream, buf):
            url = b"rtsp://10.0.0.1:554/ch0/main"
            ct.memmove(buf, url, len(url))
            return True

        mock_lib.NET_SDK_GetRtspUrl.side_effect = fill_url
        url = session.rtsp_url(0, StreamType.MAIN)
        assert url == "rtsp://10.0.0.1:554/ch0/main"


# ── DeviceSession.alarm ────────────────────────────────────────────


class TestSessionAlarm:
    def test_alarm_subscribe(self, session, mock_lib):
        mock_lib.NET_SDK_SetupAlarmChan.return_value = 100
        handle = session.alarm_subscribe()
        assert handle == 100

    def test_alarm_subscribe_failure(self, session, mock_lib):
        mock_lib.NET_SDK_SetupAlarmChan.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = 26
        with pytest.raises(NetSdkError, match="SetupAlarmChan"):
            session.alarm_subscribe()

    def test_alarm_unsubscribe(self, session, mock_lib):
        mock_lib.NET_SDK_CloseAlarmChan.return_value = True
        session.alarm_unsubscribe(100)
        mock_lib.NET_SDK_CloseAlarmChan.assert_called_once_with(100)


# ── DeviceSession.find_recordings ──────────────────────────────────


class TestSessionRecordings:
    def test_find_recordings_empty(self, session, mock_lib):
        mock_lib.NET_SDK_FindFile.return_value = 1000
        mock_lib.NET_SDK_FindNextFile.return_value = 0
        mock_lib.NET_SDK_FindClose.return_value = True

        result = session.find_recordings(
            0,
            datetime(2024, 1, 1),
            datetime(2024, 1, 2),
        )
        assert result == []
        mock_lib.NET_SDK_FindClose.assert_called_once_with(1000)

    def test_find_recordings_error(self, session, mock_lib):
        mock_lib.NET_SDK_FindFile.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = 36
        with pytest.raises(NetSdkError, match="FindFile"):
            session.find_recordings(
                0,
                datetime(2024, 1, 1),
                datetime(2024, 1, 2),
            )


# ── DeviceSession.disk_info ────────────────────────────────────────


class TestSessionDiskInfo:
    def test_disk_info_empty(self, session, mock_lib):
        mock_lib.NET_SDK_FindDisk.return_value = 2000
        mock_lib.NET_SDK_GetNextDiskInfo.return_value = False
        mock_lib.NET_SDK_FindDiskClose.return_value = True

        result = session.disk_info()
        assert result == []
        mock_lib.NET_SDK_FindDiskClose.assert_called_once_with(2000)

    def test_disk_info_error(self, session, mock_lib):
        mock_lib.NET_SDK_FindDisk.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = 22
        with pytest.raises(NetSdkError, match="FindDisk"):
            session.disk_info()


# ── DeviceSession.find_logs ────────────────────────────────────────


class TestSessionLogs:
    def test_find_logs_empty(self, session, mock_lib):
        mock_lib.NET_SDK_FindDVRLog.return_value = 3000
        mock_lib.NET_SDK_FindNextLog.return_value = 0
        mock_lib.NET_SDK_FindLogClose.return_value = True

        result = session.find_logs(datetime(2024, 1, 1), datetime(2024, 1, 2))
        assert result == []
        mock_lib.NET_SDK_FindLogClose.assert_called_once_with(3000)


# ── DeviceSession management ───────────────────────────────────────


class TestSessionManagement:
    def test_reboot(self, session, mock_lib):
        mock_lib.NET_SDK_RebootDVR.return_value = True
        session.reboot()
        mock_lib.NET_SDK_RebootDVR.assert_called_once_with(1)

    def test_shutdown(self, session, mock_lib):
        mock_lib.NET_SDK_ShutDownDVR.return_value = True
        session.shutdown()
        mock_lib.NET_SDK_ShutDownDVR.assert_called_once_with(1)

    def test_sync_time(self, session, mock_lib):
        mock_lib.NET_SDK_ChangTime.return_value = True
        session.sync_time(1700000000)
        mock_lib.NET_SDK_ChangTime.assert_called_once_with(1, 1700000000)

    def test_restore_defaults(self, session, mock_lib):
        mock_lib.NET_SDK_RestoreConfig.return_value = True
        session.restore_defaults()
        mock_lib.NET_SDK_RestoreConfig.assert_called_once_with(1)

    def test_unlock_door(self, session, mock_lib):
        mock_lib.NET_SDK_UnlockAccessControl.return_value = True
        session.unlock_door(channel=0)
        mock_lib.NET_SDK_UnlockAccessControl.assert_called_once_with(1, 0)


# ── NetSdkError ─────────────────────────────────────────────────────


class TestNetSdkError:
    def test_with_code(self):
        err = NetSdkError("test", SdkError.PASSWORD_ERROR)
        assert err.code == SdkError.PASSWORD_ERROR
        assert "error=" in str(err)

    def test_without_code(self):
        err = NetSdkError("something failed")
        assert str(err) == "something failed"
        assert err.code is None

    def test_numeric_code(self):
        err = NetSdkError("unknown", 999)
        assert "999" in str(err)


# ── Dataclass construction ──────────────────────────────────────────


class TestDataclasses:
    def test_discovered_device(self):
        d = DiscoveredDevice(
            ip="10.0.0.1",
            mac="AA:BB:CC:DD:EE:FF",
            product="NVR",
            device_name="Test",
            net_port=9008,
            http_port=80,
            activated=True,
            firmware_build=20240101,
        )
        assert d.ip == "10.0.0.1"
        assert d.activated is True

    def test_device_info(self):
        d = DeviceInfo(
            serial_number="SN123",
            product="NVR",
            device_name="Test",
            device_type=3,
            mac="AA:BB:CC:DD:EE:FF",
            ip="10.0.0.1",
            port=9008,
            firmware="V5.0",
            hardware_version="HW1",
            kernel_version="K3",
            build_date="2024-01-01",
            video_inputs=16,
            audio_inputs=1,
            sensor_inputs=4,
            sensor_outputs=2,
        )
        assert d.serial_number == "SN123"
        assert d.video_inputs == 16

    def test_channel_status(self):
        c = ChannelStatus(channel=0, name="CAM1", online=True, channel_type=0)
        assert c.online

    def test_disk_info(self):
        d = DiskInfo(
            index=0,
            status=DiskStatus.NORMAL,
            property=DiskProperty.READ_WRITE,
            total_mb=1024000,
            free_mb=512000,
        )
        assert d.total_mb == 1024000

    def test_smart_support(self):
        s = SmartSupport(pea=True, vfd=True, tripwire=True)
        assert s.pea
        assert not s.fire

    def test_alarm_out_status(self):
        a = AlarmOutStatus(name="Relay1", online=True, active=False)
        assert a.name == "Relay1"
        assert not a.active


# ── DeviceSession.api_call (NET_SDK_ApiInterface) ──────────────────


class TestSessionApiCall:
    def test_api_call_returns_response_and_wraps_request(self, session, mock_lib):
        resp = b'<config status="success"/>'

        def fill(handle, req, url, buf, size, ret_ptr):
            ct.memmove(buf, resp, len(resp))
            ret_ptr._obj.value = len(resp)
            return True

        mock_lib.NET_SDK_ApiInterface.side_effect = fill
        out = session.api_call("queryPlatformCfg", "<inner/>")

        assert out == resp.decode()
        args = mock_lib.NET_SDK_ApiInterface.call_args[0]
        assert args[0] == 1  # login handle
        assert args[2] == b"queryPlatformCfg"  # CGI url
        assert b"<inner/>" in args[1]  # content placed in default envelope
        assert b"NVMS-9000" in args[1]

    def test_api_call_custom_request_envelope(self, session, mock_lib):
        seen = {}

        def fill(handle, req, url, buf, size, ret_ptr):
            seen["req"] = req
            ct.memmove(buf, b"<ok/>", 5)
            ret_ptr._obj.value = 5
            return True

        mock_lib.NET_SDK_ApiInterface.side_effect = fill
        session.api_call(
            "SetServerConfig",
            request='<config version="1.0" xmlns="http://www.ipc.com/ver10"/>',
        )
        assert b"ipc.com/ver10" in seen["req"]  # custom envelope used verbatim
        assert b"NVMS-9000" not in seen["req"]  # default envelope NOT applied

    def test_api_call_failure_raises(self, session, mock_lib):
        mock_lib.NET_SDK_ApiInterface.return_value = False
        mock_lib.NET_SDK_GetLastError.return_value = 1
        with pytest.raises(NetSdkError, match="ApiInterface"):
            session.api_call("queryPlatformCfg")


# ── Encode / record config (queryNodeEncodeInfo / editNodeEncodeInfo) ───────

# Real device shape; note the '&' in a camera name — device XML is NOT well-formed,
# so parsing must be lenient (regex), which is exactly what these tests lock in.
_ENCODE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response version="1.0" cmdUrl="queryNodeEncodeInfo">
    <status>success</status>
    <content type="list" total="2">
        <item id="{00000001-0000-0000-0000-000000000000}" isRTSPChl="false">
            <name>Entrada Principal</name>
            <mainCaps supEnct="h264,h265,h265p" bitType="VBR,CBR"><res fps="30">2560x1440</res><res fps="30">1920x1080</res></mainCaps>
            <main enct="h265p" aGOP="60" mGOP="60"></main>
            <an res="2560x1440" fps="12" bitType="VBR" level="medium" QoI="3072" audio="ON" type="main"></an>
            <ae res="2560x1440" fps="15" bitType="VBR" level="higher" QoI="4096" audio="OFF" type="main"></ae>
            <mainStreamQualityNote>1024,2048,3072,4096</mainStreamQualityNote>
        </item>
        <item id="{0000000C-0000-0000-0000-000000000000}" isRTSPChl="false">
            <name>Caja 1 & 2</name>
            <main enct="h265" aGOP="60" mGOP="60"></main>
            <an res="1920x1080" fps="10" bitType="VBR" level="low" QoI="2048" audio="OFF" type="main"></an>
            <ae res="1920x1080" fps="12" bitType="VBR" level="medium" QoI="2048" audio="OFF" type="main"></ae>
        </item>
    </content>
</response>"""

_RECORD_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response cmdUrl="queryRecordScheduleList"><status>success</status>
    <content type="list" total="1">
        <item id="{00000001-0000-0000-0000-000000000000}">
            <name><![CDATA[Entrada Principal]]></name>
            <scheduleRec><switch>true</switch></scheduleRec>
            <motionRec><switch>true</switch></motionRec>
            <alarmRec><switch>false</switch></alarmRec>
            <intelligentRec><switch>true</switch></intelligentRec>
        </item>
    </content>
</response>"""

_MOTION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response cmdUrl="queryMotion"><status>success</status><content>
    <chl id="{0000001C-0000-0000-0000-000000000000}"><param>
        <switch>true</switch>
        <objectFilter><car><switch>false</switch></car><person><switch>true</switch></person></objectFilter>
        <sensitivity min="0" max="100">60</sensitivity>
        <holdTime unit="s">20</holdTime><holdTimeNote>5,10,20,30</holdTimeNote>
        <area type="list" count="3"><itemType minLen="4" maxLen="4"/>
            <item>1111</item><item>1011</item><item>0001</item>
        </area>
    </param></chl>
</content></response>"""

_MOTION_XML_UPDATED = _MOTION_XML.replace(
    '<sensitivity min="0" max="100">60</sensitivity>',
    '<sensitivity min="0" max="100">40</sensitivity>',
)


class TestNodeEncodeInfo:
    def test_parses_continuous_event_and_codec(self, session):
        with patch.object(session, "api_call", return_value=_ENCODE_XML):
            nodes = session.node_encode_info()
        assert [n.channel for n in nodes] == [1, 12]
        ch1 = nodes[0]
        assert ch1.name == "Entrada Principal"
        assert ch1.codec == "h265p" and ch1.a_gop == 60 and ch1.m_gop == 60
        assert ch1.continuous == EncodeStream(
            kind="continuous",
            resolution="2560x1440",
            fps=12,
            bitrate_type="VBR",
            quality="medium",
            max_bitrate=3072,
            audio=True,
            codec="h265p",
        )
        assert ch1.event.fps == 15 and ch1.event.quality == "higher" and ch1.event.audio is False
        assert ch1.supported_resolutions == ("2560x1440", "1920x1080")
        assert ch1.supported_codecs == ("h264", "h265", "h265p")
        assert ch1.allowed_bitrates == (1024, 2048, 3072, 4096)

    def test_lenient_parse_of_malformed_name(self, session):
        # '&' would break a strict XML parser; the channel must still parse.
        with patch.object(session, "api_call", return_value=_ENCODE_XML):
            nodes = session.node_encode_info()
        assert nodes[1].name == "Caja 1 & 2"
        assert nodes[1].codec == "h265" and nodes[1].continuous.audio is False

    def test_non_success_status_raises(self, session):
        with patch.object(session, "api_call", return_value="<response><status>fail</status></response>"):
            with pytest.raises(NetSdkError, match="queryNodeEncodeInfo"):
                session.node_encode_info()


class TestRecordSchedule:
    def test_parses_mode_switches(self, session):
        with patch.object(session, "api_call", return_value=_RECORD_XML):
            rows = session.record_schedule()
        r = rows[0]
        assert r.channel == 1 and r.name == "Entrada Principal"
        assert r.schedule is True and r.motion is True
        assert r.alarm is False and r.intelligent is True


class TestMotionConfig:
    def test_query_uses_channel_condition_and_parses_device_bounds(self, session):
        with patch.object(session, "api_call", return_value=_MOTION_XML) as call:
            config = session.motion_config(28)

        assert config == MotionConfig(
            channel=28,
            node_id="{0000001C-0000-0000-0000-000000000000}",
            enabled=True,
            sensitivity=60,
            sensitivity_min=0,
            sensitivity_max=100,
            hold_time_seconds=20,
            allowed_hold_times=(5, 10, 20, 30),
            mask=("1111", "1011", "0001"),
            person_filter=True,
            car_filter=False,
        )
        assert config.rows == 3 and config.columns == 4
        assert config.active_cells == 8 and config.total_cells == 12
        assert config.coverage_percent == pytest.approx(66.67, abs=0.01)
        request = call.call_args.kwargs["request"]
        assert "<chlId>{0000001C-0000-0000-0000-000000000000}</chlId>" in request
        assert "<requireField><param/></requireField>" in request

    def test_query_rejects_invalid_response(self, session):
        broken = _MOTION_XML.replace('<itemType minLen="4" maxLen="4"/>', '<itemType minLen="5" maxLen="5"/>')
        with patch.object(session, "api_call", return_value=broken):
            with pytest.raises(NetSdkError, match="invalid area mask"):
                session.motion_config(28)

    def test_query_rejects_response_for_another_channel(self, session):
        wrong_channel = _MOTION_XML.replace("{0000001C-", "{0000001B-")
        with patch.object(session, "api_call", return_value=wrong_channel):
            with pytest.raises(NetSdkError, match="returned config for"):
                session.motion_config(28)

    def test_optional_hold_time_and_filters_may_be_absent(self, session):
        minimal = re.sub(r"\s*<objectFilter>.*?</objectFilter>", "", _MOTION_XML, flags=re.S)
        minimal = re.sub(r"\s*<holdTime.*?</holdTime><holdTimeNote>.*?</holdTimeNote>", "", minimal)
        with patch.object(session, "api_call", return_value=minimal):
            config = session.motion_config(28)
        assert config.hold_time_seconds is None and config.allowed_hold_times == ()
        assert config.person_filter is None and config.car_filter is None

    def test_query_non_success_status_raises(self, session):
        with patch.object(session, "api_call", return_value="<response><status>fail</status></response>"):
            with pytest.raises(NetSdkError, match="queryMotion"):
                session.motion_config(28)


class TestSetMotionConfig:
    def test_rmw_preserves_mask_and_verifies_update(self, session):
        calls = []
        query_count = 0

        def fake(url, content="", *, request=None, buf_size=131072):
            nonlocal query_count
            calls.append((url, content, request))
            if url == "queryMotion":
                query_count += 1
                return _MOTION_XML if query_count == 1 else _MOTION_XML_UPDATED
            return "<response><status>success</status></response>"

        with patch.object(session, "api_call", side_effect=fake):
            updated = session.set_motion_config(28, sensitivity=40)

        assert updated.sensitivity == 40
        edit = next(content for url, content, _ in calls if url == "editMotion")
        assert '<chl id="{0000001C-0000-0000-0000-000000000000}">' in edit
        assert '<sensitivity min="0" max="100">40</sensitivity>' in edit
        assert "<item>1111</item><item>1011</item><item>0001</item>" in edit
        assert "<person><switch>true</switch></person>" in edit
        assert "<car><switch>false</switch></car>" in edit

    def test_noop_does_not_write(self, session):
        with patch.object(session, "api_call", return_value=_MOTION_XML) as call:
            result = session.set_motion_config(28, sensitivity=60)
        assert result.sensitivity == 60
        assert [args.args[0] for args in call.call_args_list] == ["queryMotion"]

    @pytest.mark.parametrize("value", [-1, 101])
    def test_rejects_sensitivity_outside_reported_bounds(self, session, value):
        with patch.object(session, "api_call", return_value=_MOTION_XML):
            with pytest.raises(ValueError, match="sensitivity must be between 0 and 100"):
                session.set_motion_config(28, sensitivity=value)

    def test_rejects_wrong_mask_shape(self, session):
        with patch.object(session, "api_call", return_value=_MOTION_XML):
            with pytest.raises(ValueError, match="3 binary rows of 4 columns"):
                session.set_motion_config(28, mask=("1111", "101x", "0001"))

    def test_verification_mismatch_raises(self, session):
        def fake(url, content="", *, request=None, buf_size=131072):
            return _MOTION_XML if url == "queryMotion" else "<response><status>success</status></response>"

        with patch.object(session, "api_call", side_effect=fake):
            with pytest.raises(NetSdkError, match="verification mismatch"):
                session.set_motion_config(28, sensitivity=40)


class TestSetNodeEncode:
    def test_rmw_applies_overrides_and_preserves_rest(self, session):
        calls = []

        def fake(url, content="", *, request=None, buf_size=131072):
            calls.append((url, request))
            if url == "queryNodeEncodeInfo":
                return _ENCODE_XML
            return "<response><status>success</status></response>"

        with patch.object(session, "api_call", side_effect=fake):
            session.set_node_encode(
                1, continuous={"max_bitrate": 2048, "audio": False}, event={"audio": False}, verify=False
            )
        edit = next(req for url, req in calls if url == "editNodeEncodeInfo")
        # both an and ae written, as one item each, under editNodeEncodeInfo url
        assert 'url="editNodeEncodeInfo"' in edit
        assert "<an " in edit and "<ae " in edit
        # override applied
        assert 'QoI="2048"' in edit and 'audio="OFF"' in edit
        # untouched fields preserved from current config
        assert 'res="2560x1440"' in edit and 'fps="12"' in edit
        assert 'enct="h265p"' in edit and 'aGOP="60"' in edit

    def test_reject_raises(self, session):
        def fake(url, content="", *, request=None, buf_size=131072):
            if url == "queryNodeEncodeInfo":
                return _ENCODE_XML
            return "<response><status>fail</status></response>"

        with patch.object(session, "api_call", side_effect=fake):
            with pytest.raises(NetSdkError, match="rejected"):
                session.set_node_encode(1, continuous={"fps": 15})

    def test_unknown_channel_raises(self, session):
        with patch.object(session, "api_call", return_value=_ENCODE_XML):
            with pytest.raises(NetSdkError, match="channel 99 not found"):
                session.set_node_encode(99, continuous={"fps": 12})


class TestPackageExports:
    """The encode-config surface is part of the package's public API.

    Consumers (e.g. downstream application's tvt_sdk boundary) import these from
    ``pytvt.device_sdk`` — not by reaching into ``.client`` — so keep them
    promoted in the package ``__init__``.
    """

    def test_encode_config_names_promoted_to_package(self):
        import pytvt.device_sdk as pkg
        from pytvt.device_sdk import client as client_mod

        for name in ("EncodeStream", "MotionConfig", "NodeEncodeInfo", "RecordSchedule", "NetSdkError"):
            assert name in pkg.__all__
            assert getattr(pkg, name) is getattr(client_mod, name)


# ══════════════════════════════════════════════════════════════════
# NetSDK 1.3.2 additions
# ══════════════════════════════════════════════════════════════════


class TestAccessControlV132:
    def test_unlock_door_ex(self, session, mock_lib):
        mock_lib.NET_SDK_UnlockAccessControlEx.return_value = True
        session.unlock_door_ex(lock_id=3, channel=2)
        args = mock_lib.NET_SDK_UnlockAccessControlEx.call_args.args
        assert args[0] == 1  # handle
        assert args[1] == 2  # channel
        assert args[2].lockID == 3  # UNLOCK_PARAM by value

    def test_unlock_door_ex_failure(self, session, mock_lib):
        mock_lib.NET_SDK_UnlockAccessControlEx.return_value = False
        mock_lib.NET_SDK_GetLastError.return_value = 16
        with pytest.raises(NetSdkError, match="UnlockAccessControlEx"):
            session.unlock_door_ex()

    def test_rolling_gate_control(self, session, mock_lib):
        mock_lib.NET_SDK_RollingGateControl.return_value = True
        session.rolling_gate_control(RollingGateExecute.DOWN)
        mock_lib.NET_SDK_RollingGateControl.assert_called_once_with(1, 2)

    def test_missing_symbol_raises_capability_error(self, session, mock_lib):
        # Simulate an older libdvrnetsdk.so that lacks the 1.3.2 symbol.
        mock_lib.NET_SDK_RollingGateControl = None
        with pytest.raises(NetSdkCapabilityError, match="RollingGateControl"):
            session.rolling_gate_control(RollingGateExecute.UP)


class TestCallLogV132:
    def test_call_log(self, session, mock_lib):
        def fill(handle, query_ptr, buf, max_num, num_ptr, total_ptr):
            buf[0].missedCall = True
            buf[0].devName = b"Door Panel 1"
            buf[0].callType = 3
            buf[0].recordTime = DD_TIME.from_datetime(datetime(2026, 6, 1, 9, 30, 0))
            buf[0].startTime = DD_TIME.from_datetime(datetime(2026, 6, 1, 9, 30, 5))
            buf[0].endTime = DD_TIME.from_datetime(datetime(2026, 6, 1, 9, 30, 20))
            buf[0].buildingNo = 7
            buf[0].unitNo = 402
            num_ptr._obj.value = 1
            total_ptr._obj.value = 58
            return True

        mock_lib.NET_SDK_GetCallLog.side_effect = fill
        entries, total = session.call_log(datetime(2026, 6, 1), datetime(2026, 6, 2), page=1, page_size=10)
        assert total == 58
        assert len(entries) == 1
        e = entries[0]
        assert e.missed is True
        assert e.device_name == "Door Panel 1"
        assert e.call_type == 3
        assert e.building_no == 7
        assert e.unit_no == 402
        assert e.record_time == datetime(2026, 6, 1, 9, 30, 0)


class TestUserAccountsV132:
    def test_device_users(self, session, mock_lib):
        def fill(handle, buf, count_ptr):
            buf[0].m_szUserName = b"admin"
            buf[0].szGroup.szGroupName = b"Administrators"
            buf[0].szGroup.szGroupGuid = b"{group-guid}"
            buf[0].m_szEmail = b"soc@uniquesecpr.com"
            buf[0].m_szEnabled = True
            buf[0].m_szAllowModifyPassword = True
            buf[0].m_szClosePermissionControl = False
            count_ptr._obj.value = 1
            return True

        mock_lib.NET_SDK_GetDeviceUsers.side_effect = fill
        users = session.device_users()
        assert len(users) == 1
        u = users[0]
        assert u.name == "admin"
        assert u.group_name == "Administrators"
        assert u.enabled is True
        assert u.close_permission_control is False

    def test_modify_integrate_user(self, session, mock_lib):
        mock_lib.NET_SDK_ModifyIntegrateUser.return_value = True
        session.modify_integrate_user("integrator", "s3cret")
        mock_lib.NET_SDK_ModifyIntegrateUser.assert_called_once_with(1, b"integrator", b"s3cret")


class TestNvrChannelsV132:
    def test_online_channels(self, session, mock_lib):
        def fill(handle, list_ptr, size_ptr):
            lst = list_ptr._obj if hasattr(list_ptr, "_obj") else list_ptr.contents
            lst.chlList[0].value = b"{00000001-0000-0000-0000-000000000000}"
            lst.chlList[1].value = b"{00000002-0000-0000-0000-000000000000}"
            size_ptr._obj.value = 2
            return True

        mock_lib.NET_SDK_QueryOnlineChlList.side_effect = fill
        chans = session.online_channels()
        assert chans == [
            "{00000001-0000-0000-0000-000000000000}",
            "{00000002-0000-0000-0000-000000000000}",
        ]

    def test_nvr_channel_info(self, session, mock_lib):
        def fill(handle, chl_id, info_ptr):
            info = info_ptr._obj if hasattr(info_ptr, "_obj") else info_ptr.contents
            info.softwareVersion = 5
            info.detailedSoftwareVersion = b"V5.1.2-build90116"
            info.productType = 42
            info.deviceType = 2
            info.supportSoftEncrypt = 1
            info.mac = b"00:11:22:33:44:55"
            return True

        mock_lib.NET_SDK_GetNvrChlInfo.side_effect = fill
        info = session.nvr_channel_info("{00000001-0000-0000-0000-000000000000}")
        assert info.detailed_software_version == "V5.1.2-build90116"
        assert info.supports_soft_encrypt is True
        assert info.mac == "00:11:22:33:44:55"
        # chlId passed as bytes
        assert mock_lib.NET_SDK_GetNvrChlInfo.call_args.args[1] == (b"{00000001-0000-0000-0000-000000000000}")


class TestRecordStatusV132:
    def test_record_status(self, session, mock_lib):
        def fill(handle, buf, max_num):
            buf[0].dwChannel = 0
            buf[0].dwRecordType = 4
            buf[1].dwChannel = 1
            buf[1].dwRecordType = 2
            return 2

        mock_lib.NET_SDK_GetRecordStatus.side_effect = fill
        result = session.record_status()
        assert result == [RecordStatus(channel=0, record_type=4), RecordStatus(channel=1, record_type=2)]

    def test_record_status_error(self, session, mock_lib):
        mock_lib.NET_SDK_GetRecordStatus.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = 26
        with pytest.raises(NetSdkError, match="GetRecordStatus"):
            session.record_status()

    def test_record_status_ex(self, session, mock_lib):
        def fill(handle, buf, max_num):
            buf[0].deviceName = b"Front Cam"
            buf[0].dwChannel = 0
            buf[0].dwRecordType = 4
            buf[0].dwRecordStatus = 1
            buf[0].dwStreamType = 0
            buf[0].dwResolution = (2560 << 16) | 1440
            buf[0].dwFrameRate = 25
            buf[0].dwQuality = 4096
            return 1

        mock_lib.NET_SDK_GetRecordStatusEx.side_effect = fill
        result = session.record_status_ex()
        assert len(result) == 1
        r = result[0]
        assert r.device_name == "Front Cam"
        assert r.resolution == "2560x1440"
        assert r.frame_rate == 25
        assert r.bitrate_cap_kbps == 4096

    def test_record_devices(self, session, mock_lib):
        def fill(handle, buf, max_num):
            buf[0].nodeChlID.Data1 = 3
            buf[0].deviceName = b"Lobby"
            return 1

        mock_lib.NET_SDK_GetRecordDevice.side_effect = fill
        devs = session.record_devices()
        assert len(devs) == 1
        assert devs[0].channel == 3
        assert devs[0].name == "Lobby"
        assert devs[0].node_id.startswith("{00000003-")

    def test_playback_sync_handle(self, session, mock_lib):
        mock_lib.NET_SDK_GetPlayBackSyncHandle.return_value = 7777
        assert session.playback_sync_handle(2) == 7777
        mock_lib.NET_SDK_GetPlayBackSyncHandle.assert_called_once_with(1, 2)

    def test_playback_sync_handle_error(self, session, mock_lib):
        mock_lib.NET_SDK_GetPlayBackSyncHandle.return_value = 0
        mock_lib.NET_SDK_GetLastError.return_value = 31
        with pytest.raises(NetSdkError, match="GetPlayBackSyncHandle"):
            session.playback_sync_handle(0)


class TestThermalCaptureV132:
    def test_capture_thermal_jpeg(self, session, mock_lib):
        jpeg = b"\xff\xd8\xff\xe1thermal"

        def fill(handle, channel, resolution, buf, buf_size, returned_ptr):
            ct.memmove(buf, jpeg, len(jpeg))
            returned_ptr._obj.value = len(jpeg)
            return True

        mock_lib.NET_SDK_CaptureThermalJpeg.side_effect = fill
        out = session.capture_thermal_jpeg(channel=1)
        assert out == jpeg


class TestCloudUpgradeV132:
    def test_cloud_upgrade(self, session, mock_lib):
        mock_lib.NET_SDK_CloudUpgrade.return_value = True
        session.cloud_upgrade("{ver-guid}")
        mock_lib.NET_SDK_CloudUpgrade.assert_called_once_with(1, b"{ver-guid}")

    def test_cloud_upgrade_node(self, session, mock_lib):
        mock_lib.NET_SDK_CloudUpgradeNode.return_value = True
        session.cloud_upgrade_node(5, "{ver-guid}")
        mock_lib.NET_SDK_CloudUpgradeNode.assert_called_once_with(1, 5, b"{ver-guid}")

    def test_cloud_upgrade_info(self, session, mock_lib):
        def fill(handle, buf, max_num, count_ptr):
            buf[0].chlid = -1
            buf[0].state = b"downloading"
            buf[0].progress = 4250  # 42.50%
            buf[0].version = b"V5.0.0"
            buf[0].newVersionGUID = b"{new-guid}"
            count_ptr._obj.value = 1
            return True

        mock_lib.NET_SDK_GetCloudUpgradeInfo.side_effect = fill
        infos = session.cloud_upgrade_info()
        assert len(infos) == 1
        assert infos[0].channel == -1
        assert infos[0].state == "downloading"
        assert infos[0].progress_pct == 42.5
        assert infos[0].new_version_guid == "{new-guid}"


class TestSmartEventConfigV132:
    def test_get_smart_event_config(self, session, mock_lib):
        payload = b"<config>tripwire</config>"

        def fill(handle, command, channel, buf, buf_size, returned_ptr):
            ct.memmove(buf, payload, len(payload))
            returned_ptr._obj.value = len(payload)
            return True

        mock_lib.NET_SDK_GetSmartEventConfig.side_effect = fill
        out = session.get_smart_event_config(command=4, channel=0)
        assert out == payload

    def test_edit_smart_event_config(self, session, mock_lib):
        mock_lib.NET_SDK_EditSmartEventConfig.return_value = True
        session.edit_smart_event_config(4, 0, b"<config/>")
        args = mock_lib.NET_SDK_EditSmartEventConfig.call_args.args
        assert args[0] == 1 and args[1] == 4 and args[2] == 0
        assert args[4] == len(b"<config/>")

    def test_edit_smart_event_point(self, session, mock_lib):
        mock_lib.NET_SDK_EditSmartEventPoint.return_value = True
        session.edit_smart_event_point(4, 0, [(10, 20), (30, 40)], direction=TripwireDirection.RIGHT_OR_TOP)
        args = mock_lib.NET_SDK_EditSmartEventPoint.call_args.args
        assert args[4] == 2  # point count
        assert args[5] == int(TripwireDirection.RIGHT_OR_TOP)
        assert args[3][0].X == 10 and args[3][1].Y == 40


class TestRuleOverlayV132:
    def test_show_rule(self, session, mock_lib):
        mock_lib.NET_SDK_ShowRule.return_value = True
        session.show_rule(play_handle=555, channel=2, show=True)
        mock_lib.NET_SDK_ShowRule.assert_called_once_with(555, 1, 2, True)

    def test_show_rule_boxes(self, session, mock_lib):
        mock_lib.NET_SDK_ShowRuleBoxList.return_value = True
        session.show_rule_boxes(555, [[1, 2, 3, 4]])
        args = mock_lib.NET_SDK_ShowRuleBoxList.call_args.args
        assert args[0] == 555
        assert args[1].rule_point[0].m_RulePoint_x1 == 1
        assert args[1].rule_point[0].m_RulePoint_y2 == 4


class TestVoiceTalkV132:
    def test_start_voice_talk(self, session, mock_lib):
        received = []

        def fake(handle, need_raw, cb, user, channel):
            # Simulate the SDK delivering one audio frame via the callback.
            data = b"pcmframe"
            b = (ct.c_char * len(data)).from_buffer_copy(data)
            cb(9001, ct.cast(b, ct.c_void_p), len(data), 1, None)
            return 9001

        mock_lib.NET_SDK_StartVoiceComTalk.side_effect = fake
        h = session.start_voice_talk(lambda d, flag: received.append((d, flag)), channel=0)
        assert h == 9001
        assert received == [(b"pcmframe", 1)]
        # Callback retained to survive GC while the SDK holds it.
        assert 9001 in session._voice_callbacks

    def test_start_voice_talk_error(self, session, mock_lib):
        mock_lib.NET_SDK_StartVoiceComTalk.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = 27
        with pytest.raises(NetSdkError, match="StartVoiceComTalk"):
            session.start_voice_talk(lambda d, flag: None)


class TestSubscribeV2:
    def test_subscribe_v2_registers_and_dispatches(self, client, mock_lib):
        events = []

        def fake(cb, user):
            if not cb:
                return True
            payload = b'{"evt":"motion"}'
            b = (ct.c_char * len(payload)).from_buffer_copy(payload)
            cb(1, 2, 0x2A, ct.cast(b, ct.c_void_p), len(payload), None)
            return True

        mock_lib.NET_SDK_SetSubscribCallBack_V2.side_effect = fake
        client.subscribe_v2(lambda uid, ch, cmd, data: events.append((uid, ch, cmd, data)))
        assert events == [(1, 2, 0x2A, b'{"evt":"motion"}')]
        assert client._subscribe_callback is not None

    def test_subscribe_v2_clear(self, client, mock_lib):
        mock_lib.NET_SDK_SetSubscribCallBack_V2.return_value = True
        client.subscribe_v2(None)
        assert client._subscribe_callback is None
        mock_lib.NET_SDK_SetSubscribCallBack_V2.assert_called_once()


class TestPackageExportsV132:
    def test_v132_names_promoted_to_package(self):
        import pytvt.device_sdk as pkg

        for name in (
            "CallLogEntry",
            "CloudUpgradeStatus",
            "DeviceUser",
            "NvrChannelInfo",
            "RecordDevice",
            "RecordStatus",
            "RecordStatusEx",
            "RollingGateExecute",
            "TripwireDirection",
        ):
            assert name in pkg.__all__
            assert hasattr(pkg, name)
