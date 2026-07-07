"""pytvt.device_sdk — ctypes bindings for the TVT NetSDK (libdvrnetsdk.so).

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

from .client import (
    CallLogEntry,
    CloudUpgradeStatus,
    DeviceUser,
    EncodeStream,
    NatLoginFailed,
    NatTimeoutError,
    NatUnavailableError,
    NetSdkCapabilityError,
    NetSdkClient,
    NetSdkError,
    NodeEncodeInfo,
    NvrChannelInfo,
    RecordDevice,
    RecordSchedule,
    RecordStatus,
    RecordStatusEx,
    TVTClient,
)
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
    RollingGateExecute,
    SdkError,
    SmartEventType,
    StreamType,
    TripwireDirection,
)
from .loader import NetSdkUnavailable, ensure_nat_support, is_netsdk_available
from .model_capabilities import (
    FEATURE_SMART_EVENTS,
    MODEL_CAPABILITIES,
    CameraFeature,
    features_for_model,
    is_known_model,
    model_supports,
    models_supporting,
    normalize_model,
    smart_events_for_model,
)

__all__ = [
    "NetSdkClient",
    "TVTClient",
    "NetSdkError",
    "NetSdkUnavailable",
    "NetSdkCapabilityError",
    "EncodeStream",
    "NodeEncodeInfo",
    "RecordSchedule",
    "CallLogEntry",
    "CloudUpgradeStatus",
    "DeviceUser",
    "NvrChannelInfo",
    "RecordDevice",
    "RecordStatus",
    "RecordStatusEx",
    "NatUnavailableError",
    "NatLoginFailed",
    "NatTimeoutError",
    "ensure_nat_support",
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
    "RollingGateExecute",
    "SdkError",
    "SmartEventType",
    "StreamType",
    "TripwireDirection",
    "CameraFeature",
    "MODEL_CAPABILITIES",
    "FEATURE_SMART_EVENTS",
    "normalize_model",
    "is_known_model",
    "features_for_model",
    "model_supports",
    "smart_events_for_model",
    "models_supporting",
]
