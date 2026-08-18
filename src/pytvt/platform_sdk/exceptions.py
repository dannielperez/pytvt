"""Management-server exceptions for pytvt."""

from __future__ import annotations

from pytvt.exceptions import PytvtError

from .platform_constants import (
    PLAT_ERROR_BUFFER_TOO_SMALL,
    PLAT_ERROR_DEVICE_BUSY,
    PLAT_ERROR_INVALID_PARAM,
    PLAT_ERROR_NO_SUPPORT,
    PLAT_ERROR_NODE_NET_DISCONNECT,
    PLAT_ERROR_NODE_NET_OFFLINE,
)


class ManagementError(PytvtError):
    """Base class for management-server related failures."""


class ManagementUnsupportedError(ManagementError):
    """Raised when an operation is not yet supported by a backend.

    Deprecated in favour of CapabilityNotAvailable — kept for compatibility.
    """


class ManagementAuthError(ManagementError):
    """Raised when management-server authentication fails."""

    def __init__(self, message: str, *, credential_rejected: bool | None = None) -> None:
        self.credential_rejected = credential_rejected
        super().__init__(message)


# Canonical alias used by SDK/native backends
AuthError = ManagementAuthError


class ManagementNotAuthenticatedError(ManagementError):
    """Raised when an authenticated operation is called before login."""


class TransportError(ManagementError):
    """Raised when a network-level send/receive operation fails."""


_SESSION_PRESERVING_PLATFORM_ERRORS = frozenset(
    {
        PLAT_ERROR_NODE_NET_DISCONNECT,
        PLAT_ERROR_NODE_NET_OFFLINE,
        PLAT_ERROR_INVALID_PARAM,
        PLAT_ERROR_NO_SUPPORT,
        PLAT_ERROR_DEVICE_BUSY,
        PLAT_ERROR_BUFFER_TOO_SMALL,
    }
)


class PlatformOperationError(TransportError):
    """A PlatformSDK operation failure with explicit session validity."""

    def __init__(self, message: str, code: int | None = None) -> None:
        self.code = code
        super().__init__(message)

    @property
    def invalidates_session(self) -> bool:
        """Whether a pooled PlatformSDK login must be discarded.

        Only reviewed channel/operation-local errors preserve the shared
        platform login. Missing, unknown, auth, init, connection, and timeout
        failures remain fail-closed.
        """

        return self.code not in _SESSION_PRESERVING_PLATFORM_ERRORS


class ProtocolError(ManagementError):
    """Raised when a response cannot be parsed according to the expected framing."""


class PlatformCaptureError(ProtocolError):
    """A channel capture returned unusable bytes without invalidating login."""

    invalidates_session = False


class UnsupportedOnSDKFamilyError(ManagementError):
    """Raised when an operation is invalid for the active SDK family."""


class UnsupportedOnPlatformError(ManagementError):
    """Raised when an operation is invalid for the active runtime platform."""


class MissingSymbolError(ManagementError):
    """Raised when a required SDK symbol is missing from the loaded library."""


class CapabilityNotAvailableError(ManagementError):
    """Raised when an operation is not available in the active SDK context."""


class ExperimentalOperationError(ManagementError):
    """Raised when an operation exists only as experimental scaffolding."""


class CapabilityNotAvailable(CapabilityNotAvailableError):
    """Backward-compatible alias for capability unavailability.

    Typical causes:
    - SDK shared library not found or failed to load.
    - Native protocol not yet implemented.
    - Capability requires a feature that is not present.
    """


class SessionExpired(ManagementError):
    """Raised when the management-server session has timed out or been revoked."""
