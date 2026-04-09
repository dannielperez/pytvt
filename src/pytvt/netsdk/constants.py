"""SDK constants translated from DVR_NET_SDK.h / dvrdvstypedef.h enums.

All values match the C SDK exactly so they can be passed to/from ctypes
bindings without translation.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag


# ── Stream types ────────────────────────────────────────────────────

class StreamType(IntEnum):
    """NET_SDK_STREAM_TYPE — video stream selection."""

    MAIN = 0
    SUB = 1
    THIRD = 2
    FOURTH = 3


# ── Device types ────────────────────────────────────────────────────

class DeviceType(IntEnum):
    """NET_SDK_DEVICE_TYPE — type of TVT device."""

    DVR = 0
    DVS = 1
    IPCAMERA = 2
    NVR = 3
    SUPERDVR = 4
    DECODER = 5
    N_DVR = 6


# ── Connect types ──────────────────────────────────────────────────

class ConnectType(IntEnum):
    """NET_SDK_CONNECT_TYPE — SDK login transport."""

    TCP = 0
    NAT = 1
    NAT20 = 2


# ── Camera / PTZ support ───────────────────────────────────────────

class CameraType(IntEnum):
    """NET_SDK_CAMERA_TYPE — PTZ capability of an IPC."""

    NOT_SUPPORT_PTZ = 0
    DOME_SUPPORT_PTZ = 1  # dome camera
    SUPPORT_PTZ = 2       # speed dome


# ── PTZ commands ────────────────────────────────────────────────────

class PtzCommand(IntEnum):
    """PTZ_CMD_TYPE — pan/tilt/zoom/preset/cruise/track commands."""

    STOP = 0
    LEFT = 1
    RIGHT = 2
    UP = 3
    DOWN = 4
    LEFT_UP = 5
    LEFT_DOWN = 6
    RIGHT_UP = 7
    RIGHT_DOWN = 8
    FOCUS_NEAR = 9
    FOCUS_FAR = 10
    ZOOM_OUT = 11
    ZOOM_IN = 12
    IRIS_OPEN = 13
    IRIS_CLOSE = 14
    PRESET_SET = 15
    PRESET_GO = 16
    PRESET_DEL = 17
    CRUISE_CFG = 18
    ENTER_CRUISE_MODE = 19
    CRUISE_SET = 20
    LEAVE_CRUISE_MODE = 21
    CRUISE_RUN = 22
    CRUISE_STOP = 23
    CRUISE_DEL = 24
    TRACK_START = 25
    TRACK_STOP = 26
    TRACK_START_RECORD = 27
    TRACK_STOP_RECORD = 28
    AUTO_SCAN_START = 29
    AUTO_SCAN_STOP = 30
    RESET = 0xF0


class PtzSpeed(IntEnum):
    """PTZ_SPEED — speed levels 1-8."""

    SPEED_1 = 1
    SPEED_2 = 2
    SPEED_3 = 3
    SPEED_4 = 4
    SPEED_5 = 5
    SPEED_6 = 6
    SPEED_7 = 7
    SPEED_8 = 8


# ── Alarm types ─────────────────────────────────────────────────────

class AlarmType(IntEnum):
    """NET_SDK_N9000_ALARM_TYPE — alarm event codes."""

    MOTION = 0x01
    SENSOR = 0x02
    VIDEO_LOSS = 0x03
    FRONT_OFFLINE = 0x04
    OSC = 0x05                       # object removed/left
    AVD = 0x06                       # video abnormal
    AVD_SCENE = 0x07                 # scene change
    AVD_CLARITY = 0x08               # blurry video
    AVD_COLOR = 0x09                 # color shift
    PEA_TRIPWIRE = 0x0A              # tripwire
    PEA_PERIMETER = 0x0B             # perimeter intrusion
    VFD = 0x0C                       # face detection
    CDD = 0x0D                       # crowd density
    IPD = 0x0E                       # people intrusion
    CPC = 0x0F                       # people counting
    FACE_MATCH = 0x10                # face match (NVR)
    FACE_MATCH_FOR_IPC = 0x11        # face match (IPC)
    PEA_FOR_IPC = 0x12               # tripwire/perimeter (IPC)
    TRAJECT = 0x13                   # target tracking
    VEHICLE = 0x14                   # vehicle/plate recognition
    AOI_ENTRY = 0x15                 # area of interest entry
    AOI_LEAVE = 0x16                 # area of interest leave
    PASSLINE = 0x17                  # line crossing count
    TRAFFIC = 0x18                   # traffic statistics
    DOOR_BELL = 0x19                 # doorbell (IPC only)
    PVD = 0x1A                       # illegal parking
    LOITER = 0x1B                    # loitering
    ASD = 0x1C                       # audio abnormal
    EXCEPTION = 0x41
    IP_CONFLICT = 0x42
    DISK_IO_ERROR = 0x43
    DISK_FULL = 0x44
    RAID_SUBHEALTH = 0x45
    RAID_UNAVAILABLE = 0x46
    ILLEGAL_ACCESS = 0x47
    NET_DISCONNECT = 0x48
    NO_DISK = 0x49
    SIGNAL_SHELTER = 0x4A
    HDD_PULL_OUT = 0x4B
    ALARM_OUT = 0x51
    DISARM = 0x52
    ARM = 0x53
    TEMPERATURE_ALARM = 0x61         # AI thermal
    FIREPOINT = 0x62                 # AI fire detection
    BINOCULAR_OVERMAN = 0x63         # binocular overcrowding
    BINOCULAR_REVERSE = 0x64         # binocular wrong-way
    FRONT_ONLINE = 0x65
    VIDEO_LOSS_OFF = 0x66
    AVD_OFF = 0x67
    DISK_IO_ERROR_OFF = 0x68
    DISK_FULL_OFF = 0x69
    NO_DISK_OFF = 0x6A


# ── Smart event types ──────────────────────────────────────────────

class SmartEventType(IntEnum):
    """NET_SDK_SMART_EVENT_TYPE — intelligent analytics event codes."""

    OSC = 0x05                       # object removed/left (IPC)
    AVD = 0x06                       # video abnormal (IPC)
    VFD = 0x0C                       # face detection (IPC)
    CDD = 0x0D                       # crowd density
    IPD = 0x0E                       # people intrusion
    CPC = 0x0F                       # people counting
    FACE_MATCH = 0x10                # face match (NVR)
    FACE_MATCH_FOR_IPC = 0x11
    PEA_FOR_IPC = 0x12               # tripwire/perimeter (IPC)
    TRAJECT = 0x13                   # target tracking
    VEHICLE = 0x14                   # vehicle/plate (IPC)
    AOI_ENTRY = 0x15
    AOI_LEAVE = 0x16
    PASSLINE = 0x17                  # line crossing (target snap)
    TRAFFIC = 0x18                   # traffic statistics
    PEA_TARGET = 0x19                # tripwire/perimeter with snap
    VSD = 0x1A                       # video structured data
    TEMPERATURE_ALARM = 0x1B
    FIREPOINT = 0x1C
    NVR_VEHICLE = 0x1D
    NVR_AOI_ENTRY = 0x1E
    NVR_AOI_LEAVE = 0x1F
    PVD = 0x20                       # illegal parking
    LOITER = 0x21
    ASD = 0x22                       # audio abnormal
    VFD_MATCH_FAILED = 0x23
    BINOCULAR_COUNT = 0x24
    FALLING = 0x25                   # fall detection
    TRIPWIRE = 0x26                  # tripwire with snap
    HEATMAP = 0x27
    THERMAL_PEA = 0x28               # thermal intrusion
    THERMAL_AOI_ENTRY = 0x29
    THERMAL_AOI_LEAVE = 0x2A


# ── Disk status / property ─────────────────────────────────────────

class DiskStatus(IntEnum):
    """NET_SDK_DISK_STATUS — HDD status."""

    NORMAL = 0
    NOT_FORMATTED = 1
    FORMATTING = 2
    EXCEPTION = 3


class DiskProperty(IntEnum):
    """NET_SDK_DISK_PROPERTY — HDD read/write mode."""

    READ_WRITE = 0
    READ_ONLY = 1
    REDUNDANCY = 2


# ── Image format ───────────────────────────────────────────────────

class ImageMode(IntEnum):
    """IMAGE_MODE — snapshot image format."""

    JPG = 0
    PNG = 1
    BMP = 2


# ── Recording types ────────────────────────────────────────────────

class RecordType(IntFlag):
    """DD_RECORD_TYPE — recording event types (bitmask)."""

    NONE = 0x0000
    MANUAL = 0x0001
    SCHEDULE = 0x0002
    MOTION = 0x0004
    SENSOR = 0x0008
    BEHAVIOR = 0x0010
    SHELTER = 0x0020
    OVERSPEED = 0x0040
    OVERBOUND = 0x0080
    OSC = 0x0100
    AVD = 0x0200
    TRIPWIRE = 0x0400
    PERIMETER = 0x0800
    VFD = 0x1000
    POS = 0x2000
    PIR = 0x4000
    INTELLIGENT = OSC | AVD | TRIPWIRE | PERIMETER | VFD
    ALL = 0xFFFFFFFF


# ── Search device types ────────────────────────────────────────────

class SearchDeviceType(IntFlag):
    """SEARCH_DEVICE_TYPE — device families for discovery filtering."""

    STANDARD = 0x001
    ONVIF = 0x002
    UPNP = 0x004
    AIPSTAR = 0x008
    DAHUA = 0x010
    HIKVISION = 0x020
    UNIVIEW = 0x040
    YCX = 0x080
    SPECO = 0x100
    ALL = 0xFFFF


# ── SDK error codes ─────────────────────────────────────────────────

class SdkError(IntEnum):
    """NET_SDK_ERROR — SDK function return / GetLastError codes."""

    SUCCESS = 0
    PASSWORD_ERROR = 1
    NOENOUGH_AUTH = 2
    NOINIT = 3
    CHANNEL_ERROR = 4
    OVER_MAXLINK = 5
    LOGIN_REFUSED = 6
    VERSION_NOMATCH = 7
    NETWORK_FAIL_CONNECT = 8
    NETWORK_NOT_CONNECT = 9
    NETWORK_SEND_ERROR = 10
    NETWORK_RECV_ERROR = 11
    NETWORK_RECV_TIMEOUT = 12
    NETWORK_ERRORDATA = 13
    ORDER_ERROR = 14
    OPER_BY_OTHER = 15
    OPER_NOPERMIT = 16
    COMMAND_TIMEOUT = 17
    ERROR_SERIALPORT = 18
    ERROR_ALARMPORT = 19
    PARAMETER_ERROR = 20
    CHAN_EXCEPTION = 21
    NODISK = 22
    ERROR_DISKNUM = 23
    DISK_FULL = 24
    DISK_ERROR = 25
    NOSUPPORT = 26
    BUSY = 27
    MODIFY_FAIL = 28
    PASSWORD_FORMAT_ERROR = 29
    DISK_FORMATING = 30
    DVR_NORESOURCE = 31
    DVR_OPRATE_FAILED = 32
    OPEN_HOSTSOUND_FAIL = 33
    DVR_VOICEOPENED = 34
    TIME_INPUTERROR = 35
    NOSPECFILE = 36
    CREATEFILE_ERROR = 37
    FILEOPENFAIL = 38
    OPERNOTFINISH = 39
    GETPLAYTIMEFAIL = 40
    PLAYFAIL = 41
    FILEFORMAT_ERROR = 42
    DIR_ERROR = 43
    ALLOC_RESOURCE_ERROR = 44
    AUDIO_MODE_ERROR = 45
    NOENOUGH_BUF = 46
    CREATESOCKET_ERROR = 47
    SETSOCKET_ERROR = 48
    MAX_NUM = 49
    USERNOTEXIST = 50
    WRITEFLASHERROR = 51
    UPGRADEFAIL = 52
    CARDHAVEINIT = 53
    PLAYERFAILED = 54
    MAX_USERNUM = 55
    GETLOCALIPANDMACFAIL = 56
    NOENCODEING = 57
    IPMISMATCH = 58
    MACMISMATCH = 59
    UPGRADELANGMISMATCH = 60
    MAX_PLAYERPORT = 61
    NOSPACEBACKUP = 62
    NODEVICEBACKUP = 63
    PICTURE_BITS_ERROR = 64
    PICTURE_DIMENSION_ERROR = 65
    PICTURE_SIZE_ERROR = 66
    LOADPLAYERSDKFAILED = 67
    LOADPLAYERSDKPROC_ERROR = 68
    LOADDSSDKFAILED = 69
    LOADDSSDKPROC_ERROR = 70
    DSSDK_ERROR = 71
    VOICEMONOPOLIZE = 72
    JOINMULTICASTFAILED = 73
    CREATEDIR_ERROR = 74
    BINDSOCKET_ERROR = 75
    SOCKETCLOSE_ERROR = 76
    USERID_ISUSING = 77
    PROGRAM_EXCEPTION = 78
    WRITEFILE_FAILED = 79
    FORMAT_READONLY = 80
    WITHSAMEUSERNAME = 81
    DEVICETYPE_ERROR = 82
    LANGUAGE_ERROR = 83
    PARAVERSION_ERROR = 84
    FILE_SUCCESS = 85
    FILE_NOFIND = 86
    NOMOREFILE = 87
    FILE_EXCEPTION = 88
    TRY_LATER = 89
    DEVICE_OFFLINE = 90
    CREATEJPEGSTREAM_FAIL = 91
    USER_ERROR_NO_USER = 92
    USER_ERROR_USER_OR_PASSWORD_IS_NULL = 93
    USER_ERROR_ALREADY_LOGIN = 94
    USER_ERROR_SYSTEM_BUSY = 95
    DEVICE_NOT_SUPPORT = 96
    USER_ERROR_SYSTEM_NO_READY = 97
    CHANNEL_OFFLINE = 98
    GETREADYINFO_FAIL = 99
    NORESOURCE = 100
    DEVICE_QUERYSYSTEMCAPS_FAIL = 101
    INBUFFER_TOSMALL = 102
    NO_PASSWORD_STRENGTH = 103
    FILE_NOT_MATCH_PRODUCT = 104
    FACE_PICTURE_INCONFORMITY = 107
    FACE_ITEM_OVER_LIMIT = 108
    FACE_PICTURE_FORMAT_UNSUPPORTED = 109
    FACE_PICTURE_DATA_INCORRECT = 110
    FACE_EXPORT_FAIL = 111
    FACE_IMPORT_FAIL = 112
    FACE_VERIFY_FAIL = 113
    FACE_PICTURE_TOO_LARGE = 114
    FACE_UPDATE_FEATURE_FAIL = 115
    FACE_ADD_FEATURE_FAIL = 116
    FACE_SEND_PICTURE_FAIL = 117
    FACE_SAVE_PICTURE_FAIL = 118
    FACE_SAVE_INFO_FAIL = 119
    FACE_SAVE_FEATURE_FAIL = 120
    FACE_EXIST = 121
    FACE_UNKNOWN_OPERATE_ERROR = 122
    FACE_NOT_UPLOAD = 123
