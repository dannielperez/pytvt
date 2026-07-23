"""pytvt — TVT NVR camera scanner and management toolkit.

Public API:
    Constants: BackendFamily, IntegrationMode, CompositeStrategy, ExecutionPlan,
               resolve_backend, resolve_execution_plan
    Models:    ScannerConfig, DeviceEntry, CameraInfo, ScanResult, NvrApiError
    NVR API:   NvrClient, Channel, NvrLanFreeDevice, LanFreeDevice, RtspServerConfig, ApiServerConfig, PortConfig
               (errors: NvrApiError, NvrApiResponseShapeError)
    Web API:   WebApiClient (TVT HTTP API / LAPI)
    Alarm:     parse_alarm_frame, ParsedAlarmFrame — decode TVT alarm-server push
               frames (JSON / binary / HTTP / length-prefixed)
    Net SDK:   pytvt.device_sdk — ctypes bindings for libdvrnetsdk.so (Linux only;
               requires a vendor-supplied shared library — NOT included in this package)
    SDK HTTP:  SdkHttpClient — typed client for a compatible SDK bridge service
    Unified:   DeviceManager — auto-selects netsdk or sdk_http backend
    Config:    load_config
    Scanner:   scan_single_nvr, load_devices, filter_tvt_devices
    Output:    save_csv, save_json, save_xlsx_per_site, save_failed_devices
    Diff:      diff_scans, load_scan_file, ScanDiff, DeviceDiff
    StatusLog: parse_status_log, parse_node_name, classify_event_type,
               StatusLogEvent, NodeIdentity — parse the NVMS Status Log
               connectivity export (vocabulary + node-name grammar; severity
               and correlation are the consumer's job)

Native / vendor SDK note
------------------------
The ``netsdk`` sub-package provides ctypes bindings for ``libdvrnetsdk.so``,
the proprietary TVT C++ SDK binary.  This native library is **not** distributed
with ``pytvt`` on PyPI.  On Linux x86-64/aarch64, set ``$TVT_SDK_PATH``
or pass ``sdk_path=...`` to point at your local copy of the library. All other backends
(``protocol``, ``sdk_http``, ``webapi``) are pure-Python and work on any platform
without any native dependencies.

The ``sdk-local`` backend is implemented directly in Python on top of the
native SDK bindings. It still requires a local vendor SDK installation, but it
no longer depends on Node.js or any bridge scripts.
"""

__version__ = "1.2.0"

from . import workflows  # Provisional — technician-facing workflow orchestrations.
from .alarm_protocol import ParsedAlarmFrame, parse_alarm_frame
from .alarm_server import AlarmServer
from .config import load_config
from .connection_pool import ConnectionPool, PoolStats, SessionMetrics, SessionResult, connect_many
from .constants import BackendFamily, CompositeStrategy, ExecutionPlan, IntegrationMode, resolve_backend
from .device_sdk.http_client import CommandResult, DeviceInfoResult, DeviceTimeResult, RtspUrlResult, SdkHttpClient
from .device_sdk.manager import Backend, DeviceManager, NoBackendAvailable, available_backends
from .diff import DeviceDiff, ScanDiff, diff_scans, load_scan_file
from .exceptions import BackendError, PytvtError, RegistryError
from .models import (
    AiResource,
    AiResourceChannel,
    AlarmServerConfig,
    ApiServerConfig,
    CameraInfo,
    Channel,
    DeviceEntry,
    FaceDbGroup,
    FaceEvent,
    LanFreeDevice,
    NvrApiError,
    NvrApiResponseShapeError,
    NvrFaceDetectionConfig,
    NvrLanFreeDevice,
    PlatformAccessConfig,
    PlatformAccessDisabledError,
    PortConfig,
    RtspServerConfig,
    ScannerConfig,
    ScanResult,
)
from .output import save_csv, save_failed_devices, save_json, save_xlsx_per_site
from .platform_sdk import ManagementClient, ManagementError
from .registry import resolve_execution_plan
from .scanner import filter_tvt_devices, load_devices, scan_single_nvr
from .status_log import (
    NodeIdentity,
    StatusLogEvent,
    classify_event_type,
    parse_node_name,
    parse_status_log,
)
from .web_api import WebApiClient
from .xml_api import NvrClient


# Lazy diagnostics accessor — avoids heavy ctypes probes at import time.
def diagnostics(sdk_path=None):
    """Run runtime diagnostics and return a :class:`~pytvt.diagnostics.DiagnosticsReport`."""
    from .diagnostics import diagnostics as _diag

    return _diag(sdk_path=sdk_path)


__all__ = [
    # Version
    "__version__",
    # Diagnostics
    "diagnostics",
    # Alarm-server wire protocol
    "ParsedAlarmFrame",
    "parse_alarm_frame",
    "AlarmServer",
    # Constants / enums
    "BackendFamily",
    "CompositeStrategy",
    "ExecutionPlan",
    "IntegrationMode",
    "resolve_backend",
    "resolve_execution_plan",
    # Exceptions
    "BackendError",
    "PytvtError",
    "RegistryError",
    # Models
    "AiResource",
    "AiResourceChannel",
    "AlarmServerConfig",
    "ApiServerConfig",
    "CameraInfo",
    "Channel",
    "DeviceEntry",
    "FaceDbGroup",
    "FaceEvent",
    "NvrFaceDetectionConfig",
    "NvrLanFreeDevice",
    "LanFreeDevice",
    "NvrApiError",
    "NvrApiResponseShapeError",
    "PlatformAccessConfig",
    "PlatformAccessDisabledError",
    "PortConfig",
    "RtspServerConfig",
    "ScannerConfig",
    "ScanResult",
    # NVR API client (NVR CGI / session auth)
    "NvrClient",
    # Web API client (TVT HTTP API / LAPI / Basic auth)
    "WebApiClient",
    # Management server facade
    "ManagementClient",
    "ManagementError",
    # SDK HTTP client
    "SdkHttpClient",
    "CommandResult",
    "DeviceInfoResult",
    "DeviceTimeResult",
    "RtspUrlResult",
    # Unified device manager (auto backend selection)
    "DeviceManager",
    "Backend",
    "NoBackendAvailable",
    "available_backends",
    # Config
    "load_config",
    # Connection pool
    "ConnectionPool",
    "PoolStats",
    "SessionMetrics",
    "SessionResult",
    "connect_many",
    # Scanner
    "filter_tvt_devices",
    "load_devices",
    "scan_single_nvr",
    # Output
    "save_csv",
    "save_failed_devices",
    "save_json",
    "save_xlsx_per_site",
    # Diff
    "DeviceDiff",
    "ScanDiff",
    "diff_scans",
    "load_scan_file",
    # Status Log parser (NVMS connectivity export)
    "NodeIdentity",
    "StatusLogEvent",
    "classify_event_type",
    "parse_node_name",
    "parse_status_log",
    # Workflows (Provisional)
    "workflows",
]
