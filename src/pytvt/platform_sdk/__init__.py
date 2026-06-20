"""Management-server integration for pytvt.

This package is additive and currently provisional. It does not replace
legacy/stable device-NVR flows exposed through the core `pytvt` APIs.
"""

from .client import ManagementClient
from .context import CapabilityMap, PlatformIdentity, SDKContext, SDKIdentity
from .exceptions import (
    AuthError,
    CapabilityNotAvailable,
    CapabilityNotAvailableError,
    ExperimentalOperationError,
    ManagementAuthError,
    ManagementError,
    ManagementNotAuthenticatedError,
    ManagementUnsupportedError,
    MissingSymbolError,
    ProtocolError,
    SessionExpired,
    TransportError,
    UnsupportedOnPlatformError,
    UnsupportedOnSDKFamilyError,
)
from .models import (
    AlarmEvent,
    AlarmSubscription,
    DeviceStatus,
    ManagedChannel,
    ManagedDevice,
    ManagementServerInfo,
    ServerInfo,
)
from .platform_backend import PlatformSdkManagementBackend
from .sdk import (
    EvidenceRecord,
    ResolvedSymbol,
    SdkDiagnostics,
    export_evidence_records,
    export_evidence_schema,
    inspect_sdk_library,
)
from .sidecar import SidecarManagementBackend

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
