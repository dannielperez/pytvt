"""Abstract backend contract for TVT management-server support."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .context import SDKContext
from .models import AlarmSubscription, DeviceStatus, ManagedChannel, ManagedDevice, ServerInfo


class BaseManagementBackend(ABC):
    """Backend interface for management-server operations.

    Implementations may use SDK adapters or native protocol clients. Unknown
    vendor details remain TODO until lab captures and SDK traces are validated.
    """

    @abstractmethod
    def load_sdk(self) -> bool:
        """Initialize backend-specific SDK/runtime plumbing and report readiness."""

    @abstractmethod
    def diagnostics(self) -> dict[str, Any]:
        """Return structured backend diagnostics and capability metadata."""

    @abstractmethod
    def get_context(self) -> SDKContext:
        """Return normalized SDK context for this backend instance."""

    @abstractmethod
    def login(self, username: str, password: str, device_id: str | None = None) -> bool:
        """Authenticate and initialize a session."""

    @abstractmethod
    def get_server_info(self) -> ServerInfo:
        """Fetch high-level server metadata for diagnostics and inventory."""

    @abstractmethod
    def list_devices(self) -> list[ManagedDevice]:
        """List devices enrolled in the management server."""

    @abstractmethod
    def list_channels(self) -> list[ManagedChannel]:
        """List media channels exposed by managed devices."""

    @abstractmethod
    def get_device_statuses(self) -> list[DeviceStatus]:
        """Fetch operational statuses for managed devices."""

    @abstractmethod
    def subscribe_alarms(self) -> AlarmSubscription:
        """Start an alarm subscription and return a subscription handle."""

    @abstractmethod
    def close(self) -> None:
        """Close sessions, sockets, and backend resources."""

    @abstractmethod
    def supports_sdk(self) -> bool:
        """Return whether this backend can use a validated SDK path."""

    @abstractmethod
    def supports_native_protocol(self) -> bool:
        """Return whether this backend supports native protocol transport."""
