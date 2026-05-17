"""Management-server integration for pytvt.

This package is additive and currently provisional. It does not replace
legacy/stable device-NVR flows exposed through the core `pytvt` APIs.
"""

from .client import ManagementClient
from .context import CapabilityMap
from .context import PlatformIdentity
from .context import SDKContext
from .context import SDKIdentity
from .exceptions import AuthError
from .exceptions import CapabilityNotAvailable
from .exceptions import CapabilityNotAvailableError
from .exceptions import ExperimentalOperationError
from .exceptions import ManagementAuthError
from .exceptions import ManagementError
from .exceptions import ManagementNotAuthenticatedError
from .exceptions import ManagementUnsupportedError
from .exceptions import MissingSymbolError
from .exceptions import ProtocolError
from .exceptions import SessionExpired
from .exceptions import TransportError
from .exceptions import UnsupportedOnPlatformError
from .exceptions import UnsupportedOnSDKFamilyError
from .models import AlarmEvent
from .models import AlarmSubscription
from .models import DeviceStatus
from .models import ManagedChannel
from .models import ManagedDevice
from .models import ManagementServerInfo
from .models import ServerInfo
from .platform_backend import PlatformSdkManagementBackend
from .sidecar import SidecarManagementBackend
from .sdk import ResolvedSymbol
from .sdk import SdkDiagnostics
from .sdk import EvidenceRecord
from .sdk import export_evidence_records
from .sdk import export_evidence_schema
from .sdk import inspect_sdk_library

__all__ = [
    # Client
    "ManagementClient",
    # Exceptions
    "AuthError",
    "CapabilityMap",
    "CapabilityNotAvailable",
    "CapabilityNotAvailableError",
    "ExperimentalOperationError",
    "ManagementAuthError",
    "MissingSymbolError",
    "ManagementError",
    "ManagementNotAuthenticatedError",
    "ManagementUnsupportedError",
    "PlatformIdentity",
    "ProtocolError",
    "SDKContext",
    "SDKIdentity",
    "SessionExpired",
    "TransportError",
    "UnsupportedOnPlatformError",
    "UnsupportedOnSDKFamilyError",
    # Models
    "AlarmEvent",
    "AlarmSubscription",
    "DeviceStatus",
    "EvidenceRecord",
    "ManagedChannel",
    "ManagedDevice",
    "ManagementServerInfo",
    "ResolvedSymbol",
    "PlatformSdkManagementBackend",
    "SidecarManagementBackend",
    "SdkDiagnostics",
    "ServerInfo",
    "export_evidence_records",
    "export_evidence_schema",
    "inspect_sdk_library",
]
