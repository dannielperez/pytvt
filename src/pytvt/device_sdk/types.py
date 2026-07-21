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
        ("month", ct.c_ubyte),  # 0-11
        ("year", ct.c_ushort),  # actual year - 1900
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
        ("month", ct.c_ubyte),  # 1-12
        ("year", ct.c_ushort),  # actual year
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


class NET_SDK_DEVICE_IP_INFO(ct.Structure):
    """MAC-addressed network provisioning payload for LAN readdressing."""

    _pack_ = 4
    _fields_ = [
        ("szMac", ct.c_char * 36),
        ("szIpAddr", ct.c_char * 64),
        ("szMark", ct.c_char * 36),
        ("szGateway", ct.c_char * 36),
        ("szPassword", ct.c_char * 64),
        ("szDdns1", ct.c_char * 36),
        ("szDdns2", ct.c_char * 36),
        ("ucIPMode", ct.c_ubyte),
    ]


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
        ("diskTotalSpace", ct.c_uint),  # MB
        ("diskFreeSpace", ct.c_uint),  # MB
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


# ── License-plate smart-event payloads ─────────────────────────────


class NET_SDK_IVE_RECT_T(ct.Structure):
    """Unsigned smart-event rectangle used by IPC analytics payloads."""

    _pack_ = 4
    _fields_ = [
        ("X1", ct.c_uint),
        ("Y1", ct.c_uint),
        ("X2", ct.c_uint),
        ("Y2", ct.c_uint),
    ]


class NET_SDK_IVE_POINT_T(ct.Structure):
    """Signed smart-event point used by IPC analytics payloads."""

    _pack_ = 4
    _fields_ = [
        ("X", ct.c_int),
        ("Y", ct.c_int),
    ]


class NET_SDK_IVE_VEHICE_HEAD_INFO(ct.Structure):
    """Header for ``NET_SDK_SMART_EVENT_TYPE_VEHICE`` callback data.

    ``VEHICE`` is the spelling used by the vendor ABI and is retained for
    source/header traceability.
    """

    _pack_ = 4
    _fields_ = [
        ("begin_flag", ct.c_uint),
        ("item_cnt", ct.c_uint),
        ("plate_cnt", ct.c_uint),
        ("relativeTime", ct.c_longlong),
        ("absoluteTime", ct.c_longlong),
        ("softwareVersion", ct.c_uint),
        ("softwareBuildDate", ct.c_uint),
        ("testFlag", ct.c_ubyte),
        ("expandInfoFlag", ct.c_ubyte),
        ("resver1", ct.c_ubyte * 2),
        ("resver", ct.c_uint * 1),
        ("end_flag", ct.c_uint),
    ]


class NET_SDK_IVE_VEHICE_ITEM_INFO(ct.Structure):
    """One image/plate item in an IPC vehicle-recognition callback."""

    _pack_ = 4
    _fields_ = [
        ("begin_flag", ct.c_uint),
        ("data_type", ct.c_uint),
        ("image_type", ct.c_uint),
        ("plateId", ct.c_uint),
        ("plateCharCount", ct.c_uint),
        ("plate", ct.c_char * 32),
        ("plateCharConfid", ct.c_ubyte * 32),
        ("ptPlateCharRect", NET_SDK_IVE_RECT_T * 32),
        ("ptWidth", ct.c_uint),
        ("ptHeight", ct.c_uint),
        ("ptLeftTop", NET_SDK_IVE_POINT_T),
        ("ptRightTop", NET_SDK_IVE_POINT_T),
        ("ptLeftBottom", NET_SDK_IVE_POINT_T),
        ("ptRightBottom", NET_SDK_IVE_POINT_T),
        ("plateWidth", ct.c_ushort),
        ("plateHeight", ct.c_ushort),
        ("plateConfidence", ct.c_uint),
        ("plateIntensity", ct.c_uint),
        ("plateColor", ct.c_ubyte),
        ("plateStyle", ct.c_ubyte),
        ("PlateColorRate", ct.c_ubyte),
        ("vehicleColor", ct.c_ubyte),
        ("plateAngleH", ct.c_uint),
        ("plateAngleV", ct.c_uint),
        ("jpeg_len", ct.c_uint),
        ("jpeg_vir_len", ct.c_uint),
        ("owner", ct.c_char * 32),
        ("listType", ct.c_int),
        ("beginTime", ct.c_ulonglong),
        ("endTime", ct.c_ulonglong),
        ("cardNum", ct.c_uint),
        ("endTimeValidForever", ct.c_ubyte),
        ("iVehicleDirect", ct.c_ubyte),
        ("resrv", ct.c_ubyte * 6),
        ("end_flag", ct.c_uint),
    ]


class RECT_16(ct.Structure):
    """Signed 16-bit rectangle used by NVR plate callbacks."""

    _pack_ = 4
    _fields_ = [
        ("left", ct.c_short),
        ("top", ct.c_short),
        ("right", ct.c_short),
        ("bottom", ct.c_short),
    ]


class NET_SDK_IVE_PICTURE_INFO(ct.Structure):
    """Descriptor immediately preceding a smart-event image buffer."""

    _pack_ = 4
    _fields_ = [
        ("iWidth", ct.c_int),
        ("iHeight", ct.c_int),
        ("iPicFormat", ct.c_int),
        ("iPicSize", ct.c_int),
    ]


# ── GUID (channel node id) ──────────────────────────────────────────
# The SDK's `BOOL` typedef resolves to C++ ``bool`` (1 byte) on the Linux
# target — every BOOL-returning binding uses ``ct.c_bool``, so BOOL struct
# fields use ``ct.c_bool`` too. ``LONG`` is 8 bytes here (see bindings.py).


class GUID(ct.Structure):
    """Windows-style GUID used for NVR channel node ids (``guid.h``)."""

    _pack_ = 4
    _fields_ = [
        ("Data1", ct.c_uint),
        ("Data2", ct.c_ushort),
        ("Data3", ct.c_ushort),
        ("Data4", ct.c_ubyte * 8),
    ]

    @property
    def as_string(self) -> str:
        """Render as ``{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}`` (channel-node form)."""
        d4 = bytes(self.Data4)
        clock = d4[:2].hex().upper()
        node = d4[2:].hex().upper()
        return f"{{{self.Data1:08X}-{self.Data2:04X}-{self.Data3:04X}-{clock}-{node}}}"

    @property
    def channel(self) -> int:
        """1-based channel number encoded in Data1 (matches web-CGI node ids)."""
        return self.Data1


class VEHICE_PLATE_INFO(ct.Structure):
    """NVR plate metadata for ``NET_SDK_SMART_EVENT_TYPE_NVR_VEHICLE``."""

    _pack_ = 4
    _fields_ = [
        ("dwPlateID", ct.c_uint),
        ("dwEncryptVer", ct.c_uint),
        ("plateCharCount", ct.c_uint),
        ("plate", ct.c_char * 64),
        ("Rect16", RECT_16),
        ("plateConfidence", ct.c_uint),
        ("plateIntensity", ct.c_uint),
        ("plateColor", ct.c_ubyte),
        ("plateStyle", ct.c_ubyte),
        ("PlateColorRate", ct.c_ubyte),
        ("vehicleColor", ct.c_ubyte),
        ("dwBrand", ct.c_uint),
        ("owner", ct.c_char * 32),
        ("listType", ct.c_int),
        ("dwStartTime", ct.c_uint),
        ("dwEndTime", ct.c_uint),
        ("chlId", GUID),
    ]


# ── User accounts ───────────────────────────────────────────────────


class NET_SDK_USER_GROUP(ct.Structure):
    """User group (``NET_SDK_USER_GROUP``)."""

    _pack_ = 4
    _fields_ = [
        ("szGroupGuid", ct.c_char * 48),
        ("szGroupName", ct.c_char * 128),
    ]


class NET_SDK_USER_INFO(ct.Structure):
    """Device user account from NET_SDK_GetDeviceUsers."""

    _pack_ = 4
    _fields_ = [
        ("m_szUserName", ct.c_char * 36),
        ("szGroup", NET_SDK_USER_GROUP),
        ("m_szEmail", ct.c_char * 64),
        ("m_szEnabled", ct.c_bool),
        ("m_szAllowModifyPassword", ct.c_bool),
        ("m_szClosePermissionControl", ct.c_bool),
    ]


# ── NVR channel info ────────────────────────────────────────────────


class NVRChlInfoStruct(ct.Structure):
    """IPC info for one NVR channel from NET_SDK_GetNvrChlInfo."""

    _pack_ = 4
    _fields_ = [
        ("softwareVersion", ct.c_int),
        ("productType", ct.c_int),
        ("deviceType", ct.c_int),
        ("supportSoftEncrypt", ct.c_int),
        ("detailedSoftwareVersion", ct.c_char * 64),
        ("mac", ct.c_char * 32),
    ]


class NVRChlListStruct(ct.Structure):
    """Online channel GUID list from NET_SDK_QueryOnlineChlList."""

    _pack_ = 4
    _fields_ = [
        ("chlList", (ct.c_char * 64) * 256),
    ]


# ── Recording status / device ──────────────────────────────────────


class NET_SDK_RECORD_STATUS(ct.Structure):
    """Per-channel recording status from NET_SDK_GetRecordStatus."""

    _pack_ = 4
    _fields_ = [
        ("dwRecordType", ct.c_uint),  # DD_RECORD_TYPE
        ("dwChannel", ct.c_uint),  # 0-based
    ]


class NET_SDK_RECORD_STATUS_EX(ct.Structure):
    """Extended recording status from NET_SDK_GetRecordStatusEx."""

    _pack_ = 4
    _fields_ = [
        ("deviceName", ct.c_char * 128),
        ("dwRecordType", ct.c_uint),  # DD_RECORD_TYPE
        ("dwChannel", ct.c_uint),  # 0-based
        ("dwRecordStatus", ct.c_uint),  # DD_RECORD_STATUS
        ("dwStreamType", ct.c_uint),  # NET_SDK_STREAM_TYPE
        ("dwResolution", ct.c_uint),  # hi16=width, lo16=height
        ("dwFrameRate", ct.c_uint),
        ("dwQuality", ct.c_uint),  # bitrate cap /Kbps
        ("dwBitType", ct.c_uint),  # DD_VIDEO_ENCODE_MODE
        ("dwLevel", ct.c_uint),  # DD_IMAGE_QUALITY
    ]

    @property
    def resolution_str(self) -> str:
        return f"{(self.dwResolution >> 16) & 0xFFFF}x{self.dwResolution & 0xFFFF}"


class NET_SDK_RECORD_DEVICE(ct.Structure):
    """Recording device (channel node) from NET_SDK_GetRecordDevice."""

    _pack_ = 4
    _fields_ = [
        ("nodeChlID", GUID),
        ("deviceName", ct.c_char * 36),
    ]


# ── Access control (door / gate / call log) ─────────────────────────


class UNLOCK_PARAM(ct.Structure):
    """Door-unlock parameters for NET_SDK_UnlockAccessControlEx (by value)."""

    _pack_ = 4
    _fields_ = [
        ("lockID", ct.c_int),  # 1-based; 0/absent unlocks all locks
        ("resv", ct.c_char * 64),
    ]


class CALL_RECORD(ct.Structure):
    """Intercom call-log entry from NET_SDK_GetCallLog."""

    _pack_ = 4
    _fields_ = [
        ("missedCall", ct.c_bool),
        ("devName", ct.c_char * 32),
        ("callType", ct.c_uint),  # ACCESS_CONTROL_CALL_TYPE
        ("recordTime", DD_TIME),
        ("startTime", DD_TIME),
        ("endTime", DD_TIME),
        ("chlId", GUID),
        ("devType", ct.c_uint),  # ACCESS_CONTROL_DEVICE_TYPE
        ("communityNo", ct.c_uint),
        ("sectorNo", ct.c_uint),
        ("buildingNo", ct.c_uint),
        ("unitNo", ct.c_uint),
        ("floorNo", ct.c_uint),
        ("doorStationNo", ct.c_uint),
        ("resv", ct.c_char * 32),
    ]


class CALL_RECORD_QUERY_PARAM(ct.Structure):
    """Query window for NET_SDK_GetCallLog (passed by const ref)."""

    _pack_ = 4
    _fields_ = [
        ("startTime", DD_TIME),
        ("endTime", DD_TIME),
        ("pageIndex", ct.c_uint),  # 1-based
        ("pageSize", ct.c_uint),
        ("resv", ct.c_char * 32),
    ]


# ── Cloud upgrade ───────────────────────────────────────────────────


class CLOUD_UPGRADE_INFO(ct.Structure):
    """Cloud-upgrade state from NET_SDK_GetCloudUpgradeInfo."""

    _pack_ = 4
    _fields_ = [
        ("chlid", ct.c_int),  # -1 = NVR itself, else channel
        ("state", ct.c_char * 32),
        ("progress", ct.c_int),  # 0-10000 (basis points)
        ("version", ct.c_char * 128),
        ("newVersionGUID", ct.c_char * 128),
    ]


# ── Smart-event rule geometry ───────────────────────────────────────


class NET_DVR_IVE_POINT_T(ct.Structure):
    """A single rule/tripwire point (NET_DVR_IVE_POINT_T)."""

    _pack_ = 4
    _fields_ = [
        ("X", ct.c_int),
        ("Y", ct.c_int),
    ]


MAX_RULE_NUMBER = 4


class RULE_POINT(ct.Structure):
    """Up to six on-screen rule-box corners (``_rule_point``)."""

    _pack_ = 4
    _fields_ = [(f"m_RulePoint_{axis}{i}", ct.c_int) for i in range(1, 7) for axis in ("x", "y")]


class RULE_POINT_LIST(ct.Structure):
    """Rule-box overlay list for NET_SDK_ShowRuleBoxList (by value)."""

    _pack_ = 4
    _fields_ = [
        ("rule_point", RULE_POINT * MAX_RULE_NUMBER),
    ]


# ── Callback prototypes ─────────────────────────────────────────────
# CALLBACK is cdecl on Linux, so CFUNCTYPE (cdecl) matches. Binary buffers are
# passed as c_void_p (not c_char_p) so embedded NULs survive; wrappers copy out
# the requested length with ctypes.string_at.

# void(POINTERHANDLE lVoiceComHandle, char* pRecvDataBuffer, DWORD dwBufSize,
#      BYTE byAudioFlag, void* pUser)
TALK_DATA_CALLBACK = ct.CFUNCTYPE(None, ct.c_longlong, ct.c_void_p, ct.c_uint, ct.c_ubyte, ct.c_void_p)

# void(LONG lUserID, LONG channelID, DWORD dwCommand, char* pBuf, DWORD dwBufLen,
#      void* pUser)
SUBSCRIBE_CALLBACK_V2 = ct.CFUNCTYPE(None, ct.c_long, ct.c_long, ct.c_uint, ct.c_void_p, ct.c_uint, ct.c_void_p)


class NET_DVR_SUBSCRIBE_REPLY(ct.Structure):
    """Opaque renewal/unsubscribe token returned by ``NET_SDK_SmartSubscrib``."""

    _pack_ = 4
    _fields_ = [
        ("serverAddress", ct.c_char * 256),
        ("currentTime", ct.c_longlong),
        ("terminationTime", ct.c_longlong),
        ("resv", ct.c_char * 64),
    ]
