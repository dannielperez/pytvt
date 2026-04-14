"""Tests for pytvt.netsdk.client — NetSdkClient with mocked native library."""

from __future__ import annotations

import ctypes as ct
from datetime import datetime
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from pytvt.netsdk import bindings as sdk
from pytvt.netsdk.client import (
    AlarmOutStatus,
    ChannelStatus,
    DeviceInfo,
    DeviceSession,
    DeviceSupport,
    DiscoveredDevice,
    DiskInfo,
    IpcInfo,
    LogEntry,
    NetSdkClient,
    NetSdkError,
    RecordingDateRange,
    RecordingFile,
    SmartSupport,
)
from pytvt.netsdk.constants import (
    DiskProperty,
    DiskStatus,
    PtzCommand,
    PtzSpeed,
    RecordType,
    SdkError,
    StreamType,
)
from pytvt.netsdk.types import (
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
    lib.NET_SDK_Logout.return_value = True
    return lib


@pytest.fixture
def client(mock_lib):
    """Create a NetSdkClient with mocked native library."""
    with patch("pytvt.netsdk.client.load_sdk", return_value=mock_lib):
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
        with patch("pytvt.netsdk.client.load_sdk", return_value=mock_lib):
            c = NetSdkClient()
            mock_lib.NET_SDK_Init.assert_called_once()
            mock_lib.NET_SDK_SetConnectTime.assert_called_once_with(5000, 5000)
            mock_lib.NET_SDK_SetReconnect.assert_called_once_with(0, False)
            c.cleanup()

    def test_init_failure(self, mock_lib):
        mock_lib.NET_SDK_Init.return_value = False
        with patch("pytvt.netsdk.client.load_sdk", return_value=mock_lib), \
             pytest.raises(NetSdkError, match="Init"):
            NetSdkClient()

    def test_context_manager(self, mock_lib):
        with patch("pytvt.netsdk.client.load_sdk", return_value=mock_lib):
            with NetSdkClient() as c:
                assert c._lib is not None
            mock_lib.NET_SDK_Cleanup.assert_called()

    def test_custom_timeouts(self, mock_lib):
        with patch("pytvt.netsdk.client.load_sdk", return_value=mock_lib):
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
            info = info_ptr._obj if hasattr(info_ptr, '_obj') else info_ptr.contents
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
            1, 0, PtzCommand.LEFT, PtzSpeed.SPEED_4,
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
            1, 0, PtzCommand.PRESET_GO, 5,
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
            0, datetime(2024, 1, 1), datetime(2024, 1, 2),
        )
        assert result == []
        mock_lib.NET_SDK_FindClose.assert_called_once_with(1000)

    def test_find_recordings_error(self, session, mock_lib):
        mock_lib.NET_SDK_FindFile.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = 36
        with pytest.raises(NetSdkError, match="FindFile"):
            session.find_recordings(
                0, datetime(2024, 1, 1), datetime(2024, 1, 2),
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
            ip="10.0.0.1", mac="AA:BB:CC:DD:EE:FF", product="NVR",
            device_name="Test", net_port=9008, http_port=80,
            activated=True, firmware_build=20240101,
        )
        assert d.ip == "10.0.0.1"
        assert d.activated is True

    def test_device_info(self):
        d = DeviceInfo(
            serial_number="SN123", product="NVR", device_name="Test",
            device_type=3, mac="AA:BB:CC:DD:EE:FF", ip="10.0.0.1",
            port=9008, firmware="V5.0", hardware_version="HW1",
            kernel_version="K3", build_date="2024-01-01",
            video_inputs=16, audio_inputs=1,
            sensor_inputs=4, sensor_outputs=2,
        )
        assert d.serial_number == "SN123"
        assert d.video_inputs == 16

    def test_channel_status(self):
        c = ChannelStatus(channel=0, name="CAM1", online=True, channel_type=0)
        assert c.online

    def test_disk_info(self):
        d = DiskInfo(
            index=0, status=DiskStatus.NORMAL, property=DiskProperty.READ_WRITE,
            total_mb=1024000, free_mb=512000,
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
