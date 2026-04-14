"""pytvt.netsdk — ctypes bindings for the TVT NetSDK (libdvrnetsdk.so).

Provides a Pythonic interface to the TVT C++ SDK binary protocol library
for device management operations not available via HTTP APIs:

- Device discovery and activation
- Rich device info (SN, MAC, firmware, hardware version, AI capabilities)
- PTZ control (pan/tilt/zoom/presets/cruise/track/auto-scan)
- Direct JPEG snapshots (no ffmpeg required)
- Alarm management and smart event subscription
- Recording search and control
- Firmware upgrade with progress tracking
- Disk management and health monitoring
- Device reboot/shutdown
- User management
- Time synchronisation

Platform: Linux x86-64 and aarch64 only ($TVT_SDK_PATH or sdk_path=... to override path).
"""

from .client import NetSdkClient
from .constants import (
    AlarmType,
    CameraType,
    ConnectType,
    DeviceType,
    DiskProperty,
    DiskStatus,
    ImageMode,
    PtzCommand,
    PtzSpeed,
    SdkError,
    SmartEventType,
    StreamType,
)
from .loader import NetSdkUnavailable, is_netsdk_available

__all__ = [
    "NetSdkClient",
    "NetSdkUnavailable",
    "is_netsdk_available",
    "AlarmType",
    "CameraType",
    "ConnectType",
    "DeviceType",
    "DiskProperty",
    "DiskStatus",
    "ImageMode",
    "PtzCommand",
    "PtzSpeed",
    "SdkError",
    "SmartEventType",
    "StreamType",
]
