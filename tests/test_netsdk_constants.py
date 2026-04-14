"""Tests for pytvt.netsdk.constants — enum/flag values match the C SDK."""

from __future__ import annotations

import pytest

from pytvt.netsdk.constants import (
    AlarmType,
    CameraType,
    ConnectType,
    DeviceType,
    DiskProperty,
    DiskStatus,
    ImageMode,
    PtzCommand,
    PtzSpeed,
    RecordType,
    SdkError,
    SearchDeviceType,
    SmartEventType,
    StreamType,
)

# ── StreamType ──────────────────────────────────────────────────────


class TestStreamType:
    def test_values(self):
        assert StreamType.MAIN == 0
        assert StreamType.SUB == 1
        assert StreamType.THIRD == 2
        assert StreamType.FOURTH == 3

    def test_count(self):
        assert len(StreamType) == 4


# ── DeviceType ──────────────────────────────────────────────────────


class TestDeviceType:
    def test_nvr(self):
        assert DeviceType.NVR == 3

    def test_ipcamera(self):
        assert DeviceType.IPCAMERA == 2

    def test_count(self):
        assert len(DeviceType) == 7


# ── ConnectType ─────────────────────────────────────────────────────


class TestConnectType:
    def test_tcp(self):
        assert ConnectType.TCP == 0

    def test_nat(self):
        assert ConnectType.NAT == 1


# ── PtzCommand ──────────────────────────────────────────────────────


class TestPtzCommand:
    def test_directional(self):
        assert PtzCommand.LEFT == 1
        assert PtzCommand.RIGHT == 2
        assert PtzCommand.UP == 3
        assert PtzCommand.DOWN == 4
        assert PtzCommand.LEFT_UP == 5

    def test_zoom(self):
        assert PtzCommand.ZOOM_IN == 12
        assert PtzCommand.ZOOM_OUT == 11

    def test_preset(self):
        assert PtzCommand.PRESET_SET == 15
        assert PtzCommand.PRESET_GO == 16
        assert PtzCommand.PRESET_DEL == 17

    def test_stop(self):
        assert PtzCommand.STOP == 0

    def test_reset(self):
        assert PtzCommand.RESET == 0xF0


# ── PtzSpeed ────────────────────────────────────────────────────────


class TestPtzSpeed:
    def test_range(self):
        assert PtzSpeed.SPEED_1 == 1
        assert PtzSpeed.SPEED_8 == 8

    def test_count(self):
        assert len(PtzSpeed) == 8


# ── AlarmType ───────────────────────────────────────────────────────


class TestAlarmType:
    def test_motion(self):
        assert AlarmType.MOTION == 0x01

    def test_video_loss(self):
        assert AlarmType.VIDEO_LOSS == 0x03

    def test_tripwire(self):
        assert AlarmType.PEA_TRIPWIRE == 0x0A

    def test_face(self):
        assert AlarmType.VFD == 0x0C
        assert AlarmType.FACE_MATCH == 0x10

    def test_exception_range(self):
        assert AlarmType.EXCEPTION == 0x41
        assert AlarmType.DISK_FULL == 0x44

    def test_fire(self):
        assert AlarmType.FIREPOINT == 0x62


# ── SmartEventType ──────────────────────────────────────────────────


class TestSmartEventType:
    def test_face_detection(self):
        assert SmartEventType.VFD == 0x0C

    def test_falling(self):
        assert SmartEventType.FALLING == 0x25

    def test_heatmap(self):
        assert SmartEventType.HEATMAP == 0x27


# ── DiskStatus ──────────────────────────────────────────────────────


class TestDiskStatus:
    def test_values(self):
        assert DiskStatus.NORMAL == 0
        assert DiskStatus.NOT_FORMATTED == 1
        assert DiskStatus.EXCEPTION == 3


# ── DiskProperty ────────────────────────────────────────────────────


class TestDiskProperty:
    def test_values(self):
        assert DiskProperty.READ_WRITE == 0
        assert DiskProperty.READ_ONLY == 1
        assert DiskProperty.REDUNDANCY == 2


# ── ImageMode ───────────────────────────────────────────────────────


class TestImageMode:
    def test_values(self):
        assert ImageMode.JPG == 0
        assert ImageMode.PNG == 1
        assert ImageMode.BMP == 2


# ── RecordType (IntFlag) ───────────────────────────────────────────


class TestRecordType:
    def test_bitmask_combination(self):
        combined = RecordType.MOTION | RecordType.SENSOR
        assert combined == 0x000C

    def test_intelligent_composite(self):
        expected = RecordType.OSC | RecordType.AVD | RecordType.TRIPWIRE | RecordType.PERIMETER | RecordType.VFD
        assert expected == RecordType.INTELLIGENT

    def test_all(self):
        assert RecordType.ALL == 0xFFFFFFFF

    def test_membership(self):
        combo = RecordType.MOTION | RecordType.SCHEDULE
        assert RecordType.MOTION in combo
        assert RecordType.SENSOR not in combo


# ── SearchDeviceType (IntFlag) ──────────────────────────────────────


class TestSearchDeviceType:
    def test_individual(self):
        assert SearchDeviceType.ONVIF == 0x002
        assert SearchDeviceType.HIKVISION == 0x020
        assert SearchDeviceType.DAHUA == 0x010

    def test_combination(self):
        combo = SearchDeviceType.STANDARD | SearchDeviceType.ONVIF
        assert combo == 0x003


# ── SdkError ────────────────────────────────────────────────────────


class TestSdkError:
    def test_success(self):
        assert SdkError.SUCCESS == 0

    def test_password_error(self):
        assert SdkError.PASSWORD_ERROR == 1

    def test_network(self):
        assert SdkError.NETWORK_FAIL_CONNECT == 8
        assert SdkError.NETWORK_RECV_TIMEOUT == 12

    def test_nosupport(self):
        assert SdkError.NOSUPPORT == 26

    def test_lookup(self):
        assert SdkError(0) == SdkError.SUCCESS
        assert SdkError(20) == SdkError.PARAMETER_ERROR


# ── CameraType ──────────────────────────────────────────────────────


class TestCameraType:
    def test_values(self):
        assert CameraType.NOT_SUPPORT_PTZ == 0
        assert CameraType.DOME_SUPPORT_PTZ == 1
        assert CameraType.SUPPORT_PTZ == 2
