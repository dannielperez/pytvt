"""pytvt — TVT NVR camera scanner and management toolkit.

Public API:
    Constants: BackendFamily, IntegrationMode, CompositeStrategy, ExecutionPlan,
               resolve_backend, resolve_execution_plan
    Models:    ScannerConfig, DeviceEntry, CameraInfo, ScanResult, NvrApiError
    Config:    load_config
    Scanner:   scan_single_nvr, load_devices, filter_tvt_devices
    Output:    save_csv, save_json, save_xlsx_per_site, save_failed_devices
    Diff:      diff_scans, load_scan_file, ScanDiff, DeviceDiff
"""

__version__ = "0.2.0"

from .config import load_config
from .constants import BackendFamily, CompositeStrategy, ExecutionPlan, IntegrationMode, resolve_backend
from .diff import DeviceDiff, ScanDiff, diff_scans, load_scan_file
from .exceptions import BackendError, PytvtError, RegistryError
from .models import (
    CameraInfo,
    DeviceEntry,
    NvrApiError,
    ScannerConfig,
    ScanResult,
)
from .output import save_csv, save_failed_devices, save_json, save_xlsx_per_site
from .registry import resolve_execution_plan
from .scanner import filter_tvt_devices, load_devices, scan_single_nvr

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
    "CameraInfo",
    "DeviceEntry",
    "NvrApiError",
    "ScannerConfig",
    "ScanResult",
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
