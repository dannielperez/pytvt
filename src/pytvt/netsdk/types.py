"""ctypes structure definitions matching DVR_NET_SDK.h / dvrdvsdefine.h.

Every struct uses ``_pack_ = 4`` matching the SDK's ``#pragma pack(4)``.
Field names mirror the C originals for traceability.
"""

from __future__ import annotations

import ctypes as ct
from datetime import datetime

# ── Time structures ─────────────────────────────────────────────────

class DD_TIME(ct.Structure):
    """Recording time (month 0-11, year = actual - 1900)."""

    _pack_ = 4
    _fields_ = [
        ("second", ct.c_ubyte),
        ("minute", ct.c_ubyte),
        ("hour", ct.c_ubyte),
        ("wday", ct.c_ubyte),
        ("mday", ct.c_ubyte),
        ("month", ct.c_ubyte),       # 0-11
        ("year", ct.c_ushort),       # actual year - 1900
        ("nTotalseconds", ct.c_int),
        ("nMicrosecond", ct.c_int),
    ]

    def to_datetime(self) -> datetime:
        return datetime(
            self.year + 1900,
            self.month + 1,
            max(self.mday, 1),
            self.hour,
            self.minute,
            self.second,
        )

    @classmethod
    def from_datetime(cls, dt: datetime) -> DD_TIME:
        t = cls()
        t.year = dt.year - 1900
        t.month = dt.month - 1
        t.mday = dt.day
        t.hour = dt.hour
        t.minute = dt.minute
        t.second = dt.second
        t.wday = dt.weekday()  # 0=Monday in Python vs 0=Sunday in SDK
        return t


class DD_TIME_EX(ct.Structure):
    """Recording time extended (month 1-12, year = actual)."""

    _pack_ = 4
    _fields_ = [
        ("second", ct.c_ubyte),
        ("minute", ct.c_ubyte),
        ("hour", ct.c_ubyte),
        ("wday", ct.c_ubyte),
        ("mday", ct.c_ubyte),
        ("month", ct.c_ubyte),       # 1-12
        ("year", ct.c_ushort),       # actual year
        ("nTotalseconds", ct.c_int),
        ("nMicrosecond", ct.c_int),
    ]

    def to_datetime(self) -> datetime:
        return datetime(
            self.year,
            max(self.month, 1),
            max(self.mday, 1),
            self.hour,
            self.minute,
            self.second,
        )


# ── Device info ─────────────────────────────────────────────────────

class NET_SDK_DEVICE_FUNC_IPC(ct.Structure):
    """IPC AI capability bitfield (32-bit flags + 7x32 reserved)."""

    _pack_ = 4
    _fields_ = [
        ("flags", ct.c_uint),
        ("resvFunction", ct.c_uint * 7),
    ]

    @property
    def perimeter(self) -> bool:
        return bool(self.flags & (1 << 1))

    @property
    def tripwire(self) -> bool:
        return bool(self.flags & (1 << 2))

    @property
    def osc(self) -> bool:
        return bool(self.flags & (1 << 3))

    @property
    def avd(self) -> bool:
        return bool(self.flags & (1 << 4))

    @property
    def cpc(self) -> bool:
        return bool(self.flags & (1 << 5))

    @property
    def cdd(self) -> bool:
        return bool(self.flags & (1 << 6))

    @property
    def ipd(self) -> bool:
        return bool(self.flags & (1 << 7))

    @property
    def vfd(self) -> bool:
        return bool(self.flags & (1 << 8))

    @property
    def vfd_match(self) -> bool:
        return bool(self.flags & (1 << 9))

    @property
    def vehicle(self) -> bool:
        return bool(self.flags & (1 << 10))

    @property
    def aoi_entry(self) -> bool:
        return bool(self.flags & (1 << 11))

    @property
    def aoi_leave(self) -> bool:
        return bool(self.flags & (1 << 12))

    @property
    def passline_count(self) -> bool:
        return bool(self.flags & (1 << 13))

    @property
    def vfd_detect(self) -> bool:
        return bool(self.flags & (1 << 14))

    @property
    def traffic(self) -> bool:
        return bool(self.flags & (1 << 15))

    @property
    def thermal(self) -> bool:
        return bool(self.flags & (1 << 16))

    @property
    def enabled(self) -> bool:
        return bool(self.flags & 1)


class NET_SDK_DEVICEINFO(ct.Structure):
    """Full device information returned by NET_SDK_Login / NET_SDK_GetDeviceInfo."""

    _pack_ = 4
    _fields_ = [
        ("localVideoInputNum", ct.c_ubyte),
        ("audioInputNum", ct.c_ubyte),
        ("sensorInputNum", ct.c_ubyte),
        ("sensorOutputNum", ct.c_ubyte),
        ("displayResolutionMask", ct.c_uint),
        ("videoOutputNum", ct.c_ubyte),
        ("netVideoOutputNum", ct.c_ubyte),
        ("netVideoInputNum", ct.c_ubyte),
        ("IVSNum", ct.c_ubyte),
        ("presetNumOneCH", ct.c_ubyte),
        ("cruiseNumOneCH", ct.c_ubyte),
        ("presetNumOneCruise", ct.c_ubyte),
        ("trackNumOneCH", ct.c_ubyte),
        ("userNum", ct.c_ubyte),
        ("netClientNum", ct.c_ubyte),
        ("netFirstStreamNum", ct.c_ubyte),
        ("deviceType", ct.c_ubyte),
        ("doblueStream", ct.c_ubyte),
        ("audioStream", ct.c_ubyte),
        ("talkAudio", ct.c_ubyte),
        ("bPasswordCheck", ct.c_ubyte),
        ("defBrightness", ct.c_ubyte),
        ("defContrast", ct.c_ubyte),
        ("defSaturation", ct.c_ubyte),
        ("defHue", ct.c_ubyte),
        ("videoInputNum", ct.c_ushort),
        ("deviceID", ct.c_ushort),
        ("videoFormat", ct.c_uint),
        ("function", ct.c_uint * 8),
        ("deviceIP", ct.c_uint),
        ("deviceMAC", ct.c_ubyte * 6),
        ("devicePort", ct.c_ushort),
        ("buildDate", ct.c_uint),
        ("buildTime", ct.c_uint),
        ("deviceName", ct.c_char * 36),
        ("firmwareVersion", ct.c_char * 36),
        ("kernelVersion", ct.c_char * 64),
        ("hardwareVersion", ct.c_char * 36),
        ("MCUVersion", ct.c_char * 36),
        ("firmwareVersionEx", ct.c_char * 64),
        ("softwareVer", ct.c_uint),
        ("szSN", ct.c_char * 32),
        ("deviceProduct", ct.c_char * 28),
        ("dwProductType", ct.c_uint),
    ]

    @property
    def mac_str(self) -> str:
        return ":".join(f"{b:02X}" for b in self.deviceMAC)

    @property
    def ip_str(self) -> str:
        ip = self.deviceIP
        return f"{ip & 0xFF}.{(ip >> 8) & 0xFF}.{(ip >> 16) & 0xFF}.{(ip >> 24) & 0xFF}"

    @property
    def build_date_str(self) -> str:
        d = self.buildDate
        return f"{d >> 16}-{(d >> 8) & 0xFF:02d}-{d & 0xFF:02d}"


# ── Discovery info ──────────────────────────────────────────────────

class NET_SDK_DEVICE_DISCOVERY_INFO(ct.Structure):
    """Device found via NET_SDK_DiscoverDevice."""

    _pack_ = 4
    _fields_ = [
        ("deviceType", ct.c_uint),
        ("productType", ct.c_char * 32),
        ("strIP", ct.c_char * 16),
        ("strNetMask", ct.c_char * 16),
        ("strGateWay", ct.c_char * 16),
        ("byMac", ct.c_ubyte * 8),
        ("netPort", ct.c_ushort),
        ("httpPort", ct.c_ushort),
        ("softVer", ct.c_uint),
        ("softBuildDate", ct.c_uint),
        ("ucIPMode", ct.c_ubyte),
        ("dwSecondIP", ct.c_char * 16),
        ("dwSecondMask", ct.c_char * 16),
        ("deviceActivated", ct.c_char),
        ("pwdLevel", ct.c_char),
        ("nocName", ct.c_char * 16),
        ("devName", ct.c_char * 20),
    ]

    @property
    def mac_str(self) -> str:
        return ":".join(f"{b:02X}" for b in self.byMac[:6])

    @property
    def is_activated(self) -> bool:
        return ord(self.deviceActivated) == 2


# ── IPC device info (from NVR) ─────────────────────────────────────

class NET_SDK_IPC_DEVICE_INFO(ct.Structure):
    """IPC channel info from NVR via NET_SDK_GetDeviceIPCInfo."""

    _pack_ = 4
    _fields_ = [
        ("deviceID", ct.c_uint),
        ("channel", ct.c_ushort),
        ("guid", ct.c_ubyte * 48),
        ("status", ct.c_ushort),
        ("szEtherName", ct.c_char * 16),
        ("szServer", ct.c_char * 64),
        ("nPort", ct.c_ushort),
        ("nHttpPort", ct.c_ushort),
        ("nCtrlPort", ct.c_ushort),
        ("szID", ct.c_char * 64),
        ("username", ct.c_char * 36),
        ("manufacturerId", ct.c_uint),
        ("manufacturerName", ct.c_char * 36),
        ("productModel", ct.c_char * 36),
        ("bUseDefaultCfg", ct.c_ubyte),
        ("bPOEDevice", ct.c_ubyte),
        ("resv", ct.c_ubyte * 2),
        ("szChlname", ct.c_char * 36),
    ]

    @property
    def is_online(self) -> bool:
        return self.status == 1


# ── Channel status ──────────────────────────────────────────────────

DD_MAX_CAMERA_NAME_BUF_LEN = 64

class NET_SDK_CH_DEVICE_STATUS(ct.Structure):
    """Channel device status from NET_SDK_GetDeviceCHStatus."""

    _pack_ = 4
    _fields_ = [
        ("channel", ct.c_ushort),
        ("status", ct.c_ushort),
        ("chlType", ct.c_uint),
        ("name", ct.c_char * DD_MAX_CAMERA_NAME_BUF_LEN),
        ("resv", ct.c_ubyte * 32),
    ]

    @property
    def is_online(self) -> bool:
        return self.status == 1


# ── Device support ──────────────────────────────────────────────────

class NET_SDK_DEV_SUPPORT(ct.Structure):
    """Device capability flags from NET_SDK_GetDeviceSupportFunction."""

    _pack_ = 4
    _fields_ = [
        ("flags", ct.c_uint),
        ("resv", ct.c_uint * 15),
    ]

    @property
    def thermometry(self) -> bool:
        return bool(self.flags & (1 << 0))

    @property
    def vfd(self) -> bool:
        return bool(self.flags & (1 << 1))

    @property
    def vfd_match(self) -> bool:
        return bool(self.flags & (1 << 2))

    @property
    def thermal(self) -> bool:
        return bool(self.flags & (1 << 3))

    @property
    def passline(self) -> bool:
        return bool(self.flags & (1 << 4))


# ── Smart event support ────────────────────────────────────────────

class NET_SDK_SMART_SUPPORT(ct.Structure):
    """Smart event capability flags from NET_SDK_GetSmarEventSupport."""

    _pack_ = 4
    _fields_ = [
        ("supportAOIEntry", ct.c_int),
        ("supportAOILeave", ct.c_int),
        ("supportASD", ct.c_int),
        ("supportAudioAlarmOut", ct.c_int),
        ("supportAutoTrack", ct.c_int),
        ("supportAvd", ct.c_int),
        ("supportBinocularCount", ct.c_int),
        ("supportCdd", ct.c_int),
        ("supportCpc", ct.c_int),
        ("supportFire", ct.c_int),
        ("supportHeatMap", ct.c_int),
        ("supportInvokeEventTypeConfig", ct.c_int),
        ("supportIpd", ct.c_int),
        ("supportLoitering", ct.c_int),
        ("supportOsc", ct.c_int),
        ("supportPassLine", ct.c_int),
        ("supportPea", ct.c_int),
        ("supportPvd", ct.c_int),
        ("supportRegionStatistics", ct.c_int),
        ("supportTemperature", ct.c_int),
        ("supportTripwire", ct.c_int),
        ("supportVehiclePlate", ct.c_int),
        ("supportVfd", ct.c_int),
        ("supportVideoMetadata", ct.c_int),
        ("supportWhiteLightAlarmOut", ct.c_int),
    ]


# ── Disk info ───────────────────────────────────────────────────────

class NET_SDK_DISK_INFO(ct.Structure):
    """Disk information from NET_SDK_GetNextDiskInfo."""

    _pack_ = 4
    _fields_ = [
        ("diskIndex", ct.c_long),
        ("diskStatus", ct.c_short),
        ("diskProperty", ct.c_short),
        ("diskTotalSpace", ct.c_uint),   # MB
        ("diskFreeSpace", ct.c_uint),    # MB
    ]


# ── Alarm info ──────────────────────────────────────────────────────

class NET_SDK_ALARMINFO(ct.Structure):
    """Alarm event from device message callback."""

    _pack_ = 4
    _fields_ = [
        ("dwAlarmType", ct.c_uint),
        ("dwSensorIn", ct.c_uint),
        ("dwChannel", ct.c_uint),
        ("dwDisk", ct.c_uint),
    ]


class NET_SDK_ALARMINFO_EX(ct.Structure):
    """Extended alarm event with sensor name and timestamp."""

    _pack_ = 4
    _fields_ = [
        ("dwAlarmType", ct.c_uint),
        ("dwSensorIn", ct.c_uint),
        ("dwChannel", ct.c_uint),
        ("dwDisk", ct.c_uint),
        ("sensorName", ct.c_char * 36),
        ("alarmTime", ct.c_char * 20),
        ("resv", ct.c_char * 128),
    ]


# ── Recording file info ────────────────────────────────────────────

class NET_SDK_REC_FILE(ct.Structure):
    """Recording file information from NET_SDK_FindNextFile."""

    _pack_ = 4
    _fields_ = [
        ("dwChannel", ct.c_uint),
        ("bFileLocked", ct.c_uint),
        ("startTime", DD_TIME),
        ("stopTime", DD_TIME),
        ("dwRecType", ct.c_uint),
        ("dwPartition", ct.c_uint),
        ("dwFileIndex", ct.c_uint),
    ]


class NET_SDK_REC_TIME(ct.Structure):
    """Recording time range from NET_SDK_FindNextFile (time mode)."""

    _pack_ = 4
    _fields_ = [
        ("dwChannel", ct.c_uint),
        ("startTime", DD_TIME),
        ("stopTime", DD_TIME),
    ]


# ── JPEG params ─────────────────────────────────────────────────────

class NET_SDK_JPEGPARA(ct.Structure):
    """JPEG capture parameters."""

    _pack_ = 4
    _fields_ = [
        ("wPicSize", ct.c_ushort),
        ("wPicQuality", ct.c_ushort),
    ]


# ── Log entry ───────────────────────────────────────────────────────

MAX_NAMELEN = 36
MAX_CONTENTLEN = 512


class NET_SDK_LOG(ct.Structure):
    """Device log entry from NET_SDK_FindNextLog."""

    _pack_ = 4
    _fields_ = [
        ("strLogTime", DD_TIME),
        ("dwMajorType", ct.c_uint),
        ("dwMinorType", ct.c_uint),
        ("sNetUser", ct.c_char * MAX_NAMELEN),
        ("dwRemoteHostAddr", ct.c_uint),
        ("sContent", ct.c_char * MAX_CONTENTLEN),
    ]


# ── NVR disk recording dates ───────────────────────────────────────

class NET_SDK_NVR_DISKREC_DATE_ITEM(ct.Structure):
    """NVR disk recording date range."""

    _pack_ = 4
    _fields_ = [
        ("diskCount", ct.c_uint),
        ("diskIndex", ct.c_uint),
        ("szDiskSizeGB", ct.c_char * 16),
        ("szStartDate", ct.c_char * 32),
        ("szEndDate", ct.c_char * 32),
    ]


# ── Alarm output status ────────────────────────────────────────────

class NET_SDK_ALRAM_OUT_STATUS(ct.Structure):
    """Alarm relay output status."""

    _pack_ = 4
    _fields_ = [
        ("szName", ct.c_char * 64),
        ("bOnlineStatus", ct.c_int),
        ("bSwitch", ct.c_int),
    ]
