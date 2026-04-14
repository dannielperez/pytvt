"""Tests for pytvt.netsdk.types — ctypes struct correctness."""

from __future__ import annotations

import ctypes as ct
from datetime import datetime

import pytest

from pytvt.netsdk.types import (
    DD_TIME,
    DD_TIME_EX,
    NET_SDK_ALARMINFO,
    NET_SDK_ALARMINFO_EX,
    NET_SDK_ALRAM_OUT_STATUS,
    NET_SDK_CH_DEVICE_STATUS,
    NET_SDK_DEV_SUPPORT,
    NET_SDK_DEVICE_DISCOVERY_INFO,
    NET_SDK_DEVICE_FUNC_IPC,
    NET_SDK_DEVICEINFO,
    NET_SDK_DISK_INFO,
    NET_SDK_IPC_DEVICE_INFO,
    NET_SDK_JPEGPARA,
    NET_SDK_LOG,
    NET_SDK_NVR_DISKREC_DATE_ITEM,
    NET_SDK_REC_FILE,
    NET_SDK_REC_TIME,
    NET_SDK_SMART_SUPPORT,
)

# ── DD_TIME ─────────────────────────────────────────────────────────

class TestDDTime:
    def test_pack_alignment(self):
        assert DD_TIME._pack_ == 4

    def test_to_datetime(self):
        t = DD_TIME()
        t.year = 124    # 2024 - 1900
        t.month = 5     # June (0-indexed)
        t.mday = 15
        t.hour = 10
        t.minute = 30
        t.second = 45
        assert t.to_datetime() == datetime(2024, 6, 15, 10, 30, 45)

    def test_from_datetime(self):
        dt = datetime(2025, 1, 20, 14, 5, 30)
        t = DD_TIME.from_datetime(dt)
        assert t.year == 125     # 2025 - 1900
        assert t.month == 0      # January = 0
        assert t.mday == 20
        assert t.hour == 14
        assert t.minute == 5
        assert t.second == 30

    def test_roundtrip(self):
        original = datetime(2024, 12, 31, 23, 59, 59)
        t = DD_TIME.from_datetime(original)
        assert t.to_datetime() == original

    def test_january_boundary(self):
        dt = datetime(2024, 1, 1, 0, 0, 0)
        t = DD_TIME.from_datetime(dt)
        assert t.month == 0
        assert t.to_datetime() == dt

    def test_december_boundary(self):
        dt = datetime(2024, 12, 25, 12, 0, 0)
        t = DD_TIME.from_datetime(dt)
        assert t.month == 11
        assert t.to_datetime() == dt


# ── DD_TIME_EX ──────────────────────────────────────────────────────

class TestDDTimeEX:
    def test_to_datetime(self):
        t = DD_TIME_EX()
        t.year = 2024
        t.month = 6
        t.mday = 15
        t.hour = 10
        t.minute = 30
        t.second = 45
        assert t.to_datetime() == datetime(2024, 6, 15, 10, 30, 45)


# ── NET_SDK_DEVICEINFO ──────────────────────────────────────────────

class TestDeviceInfo:
    def test_pack_alignment(self):
        assert NET_SDK_DEVICEINFO._pack_ == 4

    def test_mac_str(self):
        info = NET_SDK_DEVICEINFO()
        info.deviceMAC[0] = 0xAA
        info.deviceMAC[1] = 0xBB
        info.deviceMAC[2] = 0xCC
        info.deviceMAC[3] = 0xDD
        info.deviceMAC[4] = 0xEE
        info.deviceMAC[5] = 0xFF
        assert info.mac_str == "AA:BB:CC:DD:EE:FF"

    def test_ip_str(self):
        info = NET_SDK_DEVICEINFO()
        # IP is stored in little-endian: 10.200.50.251 -> 0xFB32C80A
        info.deviceIP = (10) | (200 << 8) | (50 << 16) | (251 << 24)
        assert info.ip_str == "10.200.50.251"

    def test_build_date_str(self):
        info = NET_SDK_DEVICEINFO()
        # 2024-06-15 -> (2024 << 16) | (6 << 8) | 15
        info.buildDate = (2024 << 16) | (6 << 8) | 15
        assert info.build_date_str == "2024-06-15"

    def test_device_name(self):
        info = NET_SDK_DEVICEINFO()
        info.deviceName = b"TD-3316E2-A"
        assert info.deviceName == b"TD-3316E2-A"


# ── NET_SDK_DEVICE_FUNC_IPC ────────────────────────────────────────

class TestDeviceFuncIpc:
    def test_no_flags(self):
        func = NET_SDK_DEVICE_FUNC_IPC()
        func.flags = 0
        assert not func.perimeter
        assert not func.tripwire
        assert not func.vfd
        assert not func.vehicle

    def test_all_flags(self):
        func = NET_SDK_DEVICE_FUNC_IPC()
        func.flags = 0xFFFFFFFF
        assert func.perimeter
        assert func.tripwire
        assert func.osc
        assert func.avd
        assert func.cpc
        assert func.cdd
        assert func.ipd
        assert func.vfd
        assert func.vfd_match
        assert func.vehicle
        assert func.aoi_entry
        assert func.aoi_leave
        assert func.passline_count
        assert func.vfd_detect
        assert func.traffic
        assert func.thermal
        assert func.enabled

    def test_individual_flag(self):
        func = NET_SDK_DEVICE_FUNC_IPC()
        func.flags = 1 << 10  # vehicle
        assert func.vehicle
        assert not func.perimeter
        assert not func.tripwire


# ── NET_SDK_DEVICE_DISCOVERY_INFO ──────────────────────────────────

class TestDeviceDiscoveryInfo:
    def test_mac_str(self):
        info = NET_SDK_DEVICE_DISCOVERY_INFO()
        for i in range(6):
            info.byMac[i] = 0x10 + i
        assert info.mac_str == "10:11:12:13:14:15"

    def test_is_activated(self):
        info = NET_SDK_DEVICE_DISCOVERY_INFO()
        info.deviceActivated = b"\x02"
        assert info.is_activated

    def test_not_activated(self):
        info = NET_SDK_DEVICE_DISCOVERY_INFO()
        info.deviceActivated = b"\x00"
        assert not info.is_activated


# ── NET_SDK_IPC_DEVICE_INFO ────────────────────────────────────────

class TestIpcDeviceInfo:
    def test_is_online(self):
        info = NET_SDK_IPC_DEVICE_INFO()
        info.status = 1
        assert info.is_online

    def test_is_offline(self):
        info = NET_SDK_IPC_DEVICE_INFO()
        info.status = 0
        assert not info.is_online


# ── NET_SDK_CH_DEVICE_STATUS ───────────────────────────────────────

class TestChDeviceStatus:
    def test_is_online(self):
        s = NET_SDK_CH_DEVICE_STATUS()
        s.status = 1
        assert s.is_online

    def test_is_offline(self):
        s = NET_SDK_CH_DEVICE_STATUS()
        s.status = 0
        assert not s.is_online


# ── NET_SDK_DEV_SUPPORT ────────────────────────────────────────────

class TestDevSupport:
    def test_no_support(self):
        s = NET_SDK_DEV_SUPPORT()
        s.flags = 0
        assert not s.thermometry
        assert not s.vfd
        assert not s.thermal

    def test_full_support(self):
        s = NET_SDK_DEV_SUPPORT()
        s.flags = 0x1F
        assert s.thermometry
        assert s.vfd
        assert s.vfd_match
        assert s.thermal
        assert s.passline


# ── NET_SDK_DISK_INFO ──────────────────────────────────────────────

class TestDiskInfo:
    def test_fields(self):
        d = NET_SDK_DISK_INFO()
        d.diskIndex = 1
        d.diskStatus = 0
        d.diskProperty = 0
        d.diskTotalSpace = 1024000
        d.diskFreeSpace = 512000
        assert d.diskTotalSpace == 1024000


# ── NET_SDK_JPEGPARA ───────────────────────────────────────────────

class TestJpegPara:
    def test_default_values(self):
        p = NET_SDK_JPEGPARA()
        p.wPicSize = 0xFF
        p.wPicQuality = 0
        assert p.wPicSize == 0xFF
        assert p.wPicQuality == 0


# ── NET_SDK_REC_FILE ───────────────────────────────────────────────

class TestRecFile:
    def test_time_fields(self):
        rec = NET_SDK_REC_FILE()
        rec.startTime.year = 124
        rec.startTime.month = 0
        rec.startTime.mday = 1
        rec.startTime.hour = 0
        rec.startTime.minute = 0
        rec.startTime.second = 0
        assert rec.startTime.to_datetime() == datetime(2024, 1, 1, 0, 0, 0)


# ── NET_SDK_LOG ─────────────────────────────────────────────────────

class TestLog:
    def test_content_field(self):
        log = NET_SDK_LOG()
        log.sContent = b"Test log entry"
        assert log.sContent == b"Test log entry"

    def test_user_field(self):
        log = NET_SDK_LOG()
        log.sNetUser = b"admin"
        assert log.sNetUser == b"admin"


# ── NET_SDK_SMART_SUPPORT ──────────────────────────────────────────

class TestSmartSupport:
    def test_default_zero(self):
        s = NET_SDK_SMART_SUPPORT()
        assert s.supportPea == 0
        assert s.supportVfd == 0

    def test_field_count(self):
        # 25 support fields
        assert len(NET_SDK_SMART_SUPPORT._fields_) == 25


# ── Struct size sanity checks ──────────────────────────────────────

class TestStructSizes:
    """Ensure struct sizes are reasonable (not zero, aligned to pack=4)."""

    @pytest.mark.parametrize("struct_cls", [
        DD_TIME, DD_TIME_EX, NET_SDK_DEVICEINFO, NET_SDK_DEVICE_FUNC_IPC,
        NET_SDK_DEVICE_DISCOVERY_INFO, NET_SDK_IPC_DEVICE_INFO,
        NET_SDK_CH_DEVICE_STATUS, NET_SDK_DEV_SUPPORT, NET_SDK_SMART_SUPPORT,
        NET_SDK_DISK_INFO, NET_SDK_ALARMINFO, NET_SDK_ALARMINFO_EX,
        NET_SDK_REC_FILE, NET_SDK_REC_TIME, NET_SDK_JPEGPARA, NET_SDK_LOG,
        NET_SDK_NVR_DISKREC_DATE_ITEM, NET_SDK_ALRAM_OUT_STATUS,
    ])
    def test_size_positive(self, struct_cls):
        assert ct.sizeof(struct_cls) > 0

    def test_dd_time_size(self):
        # 6 bytes + 2 pad + 2 (ushort year) + 2 pad + 4 + 4 = 16 or similar
        assert ct.sizeof(DD_TIME) >= 12

    def test_jpegpara_size(self):
        assert ct.sizeof(NET_SDK_JPEGPARA) == 4  # two ushorts
