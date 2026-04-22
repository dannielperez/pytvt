"""Management-server exceptions for pytvt."""

from __future__ import annotations

from pytvt.exceptions import PytvtError


class ManagementError(PytvtError):
    """Base class for management-server related failures."""


class ManagementUnsupportedError(ManagementError):
    """Raised when an operation is not yet supported by a backend.

    Deprecated in favour of CapabilityNotAvailable — kept for compatibility.
    """


class ManagementAuthError(ManagementError):
    """Raised when management-server authentication fails."""


# Canonical alias used by SDK/native backends
AuthError = ManagementAuthError


class ManagementNotAuthenticatedError(ManagementError):
    """Raised when an authenticated operation is called before login."""


class TransportError(ManagementError):
    """Raised when a network-level send/receive operation fails."""


class ProtocolError(ManagementError):
    """Raised when a response cannot be parsed according to the expected framing."""


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
