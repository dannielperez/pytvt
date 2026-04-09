"""pytvt — TVT NVR camera scanner and management toolkit.

Public API:
    Constants: BackendFamily, IntegrationMode, CompositeStrategy, ExecutionPlan,
               resolve_backend, resolve_execution_plan
    Models:    ScannerConfig, DeviceEntry, CameraInfo, ScanResult, NvrApiError
    NVR API:   NvrClient, Channel, RtspServerConfig, ApiServerConfig, PortConfig
    Web API:   WebApiClient (TVT HTTP API / LAPI)
    Net SDK:   pytvt.netsdk — ctypes bindings for libdvrnetsdk.so (Linux only)
    SDK HTTP:  SdkHttpClient — typed client for tvt-api Docker container
    Unified:   DeviceManager — auto-selects netsdk or sdk_http backend
    Config:    load_config
    Scanner:   scan_single_nvr, load_devices, filter_tvt_devices
    Output:    save_csv, save_json, save_xlsx_per_site, save_failed_devices
    Diff:      diff_scans, load_scan_file, ScanDiff, DeviceDiff
"""

__version__ = "0.5.0"

from .config import load_config
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

__all__ = [
    # Version
    "__version__",
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
    # SDK HTTP client (tvt-api Docker container)
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
