"""pytvt — TVT NVR camera scanner and management toolkit.

Public API:
    Constants: BackendFamily, IntegrationMode, CompositeStrategy, ExecutionPlan,
               resolve_backend, resolve_execution_plan
    Models:    ScannerConfig, DeviceEntry, CameraInfo, ScanResult, NvrApiError
    NVR API:   NvrClient, Channel, RtspServerConfig, ApiServerConfig, PortConfig
    Web API:   WebApiClient (TVT HTTP API / LAPI)
    Net SDK:   pytvt.netsdk — ctypes bindings for libdvrnetsdk.so (Linux only;
               requires a vendor-supplied shared library — NOT included in this package)
    SDK HTTP:  SdkHttpClient — typed client for a compatible SDK bridge service
    Unified:   DeviceManager — auto-selects netsdk or sdk_http backend
    Config:    load_config
    Scanner:   scan_single_nvr, load_devices, filter_tvt_devices
    Output:    save_csv, save_json, save_xlsx_per_site, save_failed_devices
    Diff:      diff_scans, load_scan_file, ScanDiff, DeviceDiff

Native / vendor SDK note
------------------------
The ``netsdk`` sub-package provides ctypes bindings for ``libdvrnetsdk.so``,
the proprietary TVT C++ SDK binary.  This native library is **not** distributed
with ``pytvt`` on PyPI.  On Linux x86-64/aarch64, set ``$TVT_SDK_PATH``
or pass ``sdk_path=...`` to point at your local copy of the library. All other backends
(``protocol``, ``sdk_http``, ``webapi``) are pure-Python and work on any platform
without any native dependencies.

The ``sdk-local`` backend (Node.js subprocess + native SDK) is also not included
in the pip package.  Set ``$TVT_SCAN_SCRIPT`` to the path of your local
``scan_nvr.mjs`` bridge script to enable it.
"""

__version__ = "0.6.1"

from .config import load_config
from .connection_pool import ConnectionPool, PoolStats, SessionMetrics, SessionResult, connect_many
from .constants import BackendFamily, CompositeStrategy, ExecutionPlan, IntegrationMode, resolve_backend
from .device_manager import Backend, DeviceManager, NoBackendAvailable, available_backends
from .diff import DeviceDiff, ScanDiff, diff_scans, load_scan_file
from .exceptions import BackendError, PytvtError, RegistryError
from .models import (
    ApiServerConfig,
    CameraInfo,
    Channel,
    DeviceEntry,
    NvrApiError,
    PortConfig,
    RtspServerConfig,
    ScannerConfig,
    ScanResult,
)
from .nvr_api import NvrClient
from .output import save_csv, save_failed_devices, save_json, save_xlsx_per_site
from .registry import resolve_execution_plan
from .scanner import filter_tvt_devices, load_devices, scan_single_nvr
from .sdk_http_client import CommandResult, DeviceInfoResult, DeviceTimeResult, RtspUrlResult, SdkHttpClient
from .webapi import WebApiClient

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
    "ApiServerConfig",
    "CameraInfo",
    "Channel",
    "DeviceEntry",
    "NvrApiError",
    "PortConfig",
    "RtspServerConfig",
    "ScannerConfig",
    "ScanResult",
    # NVR API client (NVR CGI / session auth)
    "NvrClient",
    # Web API client (TVT HTTP API / LAPI / Basic auth)
    "WebApiClient",
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
]
