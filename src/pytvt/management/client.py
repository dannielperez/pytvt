"""Public management-server client facade for pytvt.

Backend selection
-----------------
ManagementClient selects exactly ONE backend at login time:

    1. native_linux_sdk — explicit Linux SDK backend
    2. sidecar           — explicit sidecar bridge backend
    3. native_protocol  — explicit native protocol backend stub
    4. auto             — deterministic SDK-first fallback to native_protocol

A single session ALWAYS uses one backend.  SDK + native mixing is never allowed
within the same session.

Usage
-----
    client = ManagementClient("192.168.1.100", sdk_path="/opt/tvtsdk/libTVTNet.so")
    client.login("admin", "password")
    info    = client.get_server_info()
    devices = client.list_devices()
    client.close()
"""

from __future__ import annotations

import logging
from typing import Literal

from .base import BaseManagementBackend
from .context import SDKContext
from .exceptions import (
    CapabilityNotAvailable,
    ManagementNotAuthenticatedError,
)
from .models import AlarmSubscription, DeviceStatus, ManagedChannel, ManagedDevice, ServerInfo
from .native import NativeManagementBackend
from .sidecar import SidecarManagementBackend
from .sdk import SdkManagementBackend

logger = logging.getLogger(__name__)

SdkLoginMode = Literal["login", "login_ex"]
SdkConnectType = Literal["tcp", "nat", "nat20"]
BackendMode = Literal["auto", "native_linux_sdk", "native_protocol", "sidecar"]


class ManagementClient:
    """Facade that enforces a single-backend session against a TVT management server."""

    def __init__(
        self,
        host: str,
        *,
        port: int = 6003,
        sdk_path: str | None = None,
        prefer_sdk: bool = True,
        sdk_login_mode: SdkLoginMode = "login",
        sdk_connect_type: SdkConnectType = "tcp",
        backend_mode: BackendMode = "auto",
        sidecar_command: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.sdk_path = sdk_path
        self.prefer_sdk = prefer_sdk
        self.sdk_login_mode: SdkLoginMode = sdk_login_mode
        self.sdk_connect_type: SdkConnectType = sdk_connect_type
        self.backend_mode: BackendMode = backend_mode
        self.sidecar_command = sidecar_command
        self._backend: BaseManagementBackend | None = None

    # ------------------------------------------------------------------
    # Backend selection (called once at login; frozen for session lifetime)
    # ------------------------------------------------------------------

    def _select_backend(self) -> BaseManagementBackend:
        """Return the backend that will be used for this session.

                The decision is deterministic and never implicit about backend family:
                - native_linux_sdk  -> SDK backend only
                - sidecar           -> sidecar backend only
                - native_protocol   -> native backend only
                - auto              -> SDK when ready, otherwise native protocol stub

        Never returns a partially-capable backend.
        """
        if self.backend_mode == "native_linux_sdk":
            sdk = SdkManagementBackend(
                self.host,
                port=self.port,
                sdk_path=self.sdk_path,
                login_mode=self.sdk_login_mode,
                login_connect_type=self.sdk_connect_type,
            )
            logger.debug("ManagementClient: native_linux_sdk backend forced for %s", self.host)
            return sdk

        if self.backend_mode == "sidecar":
            logger.debug("ManagementClient: sidecar backend forced for %s", self.host)
            return SidecarManagementBackend(
                self.host,
                port=self.port,
                bridge_command=self.sidecar_command,
            )

        if self.backend_mode == "native_protocol":
            logger.debug("ManagementClient: native_protocol backend forced for %s", self.host)
            return NativeManagementBackend(self.host, port=self.port)

        if self.prefer_sdk and self.sdk_path:
            sdk = SdkManagementBackend(
                self.host,
                port=self.port,
                sdk_path=self.sdk_path,
                login_mode=self.sdk_login_mode,
                login_connect_type=self.sdk_connect_type,
            )
            if sdk.load_sdk() and sdk.supports_sdk():
                logger.debug("ManagementClient: SDK backend selected for %s", self.host)
                return sdk
            else:
                logger.debug(
                    "ManagementClient: SDK path provided but library unavailable — "
                    "falling back to native stub for %s",
                    self.host,
                )

        logger.debug("ManagementClient: native backend selected for %s", self.host)
        return NativeManagementBackend(self.host, port=self.port)

    def _require_backend(self) -> BaseManagementBackend:
        """Return the active backend or raise if not logged in."""
        if self._backend is None:
            raise ManagementNotAuthenticatedError(
                "ManagementClient is not logged in. Call login() first."
            )
        return self._backend

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def login(self, username: str, password: str, device_id: str | None = None) -> bool:
        """Authenticate with the management server.

        Selects the backend once and freezes the choice for this session.
        Raises CapabilityNotAvailable if the selected backend cannot log in.
        """
        if self._backend is not None:
            logger.warning(
                "ManagementClient.login() called while already logged in — closing previous session"
            )
            self.close()

        backend = self._select_backend()
        result = backend.login(username, password, device_id=device_id)
        if result:
            self._backend = backend
        return result

    def close(self) -> None:
        """Close the session and release backend resources."""
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    # ------------------------------------------------------------------
    # Management operations — all require an active session
    # ------------------------------------------------------------------

    def get_server_info(self) -> ServerInfo:
        return self._require_backend().get_server_info()

    def list_devices(self) -> list[ManagedDevice]:
        return self._require_backend().list_devices()

    def list_devices_for_login_routing(self) -> list[dict[str, str]]:
        backend = self._require_backend()
        if hasattr(backend, "list_devices_for_login_routing"):
            method = getattr(backend, "list_devices_for_login_routing")
            return method()

        rows: list[dict[str, str]] = []
        for device in backend.list_devices():
            raw_data = device.raw_data if isinstance(device.raw_data, dict) else {}
            device_sn = (
                str(raw_data.get("szID") or "").strip()
                or str(raw_data.get("szSN") or "").strip()
                or str(device.device_id).strip()
            )
            rows.append(
                {
                    "device_sn": device_sn,
                    "name": str(device.name).strip(),
                    "ip": str(device.ip_address).strip(),
                }
            )
        return rows

    def list_channels(self) -> list[ManagedChannel]:
        return self._require_backend().list_channels()

    def get_device_statuses(self) -> list[DeviceStatus]:
        return self._require_backend().get_device_statuses()

    def subscribe_alarms(self) -> AlarmSubscription:
        return self._require_backend().subscribe_alarms()

    # ------------------------------------------------------------------
    # Capability probes (do not require a session)
    # ------------------------------------------------------------------

    def supports_sdk(self) -> bool:
        """Return True if the SDK backend would be usable for this configuration."""
        if self.backend_mode == "sidecar":
            return False
        if not self.sdk_path:
            return False
        probe = SdkManagementBackend(
            self.host,
            port=self.port,
            sdk_path=self.sdk_path,
            login_mode=self.sdk_login_mode,
            login_connect_type=self.sdk_connect_type,
        )
        return probe.supports_sdk()

    def supports_native_protocol(self) -> bool:
        """Return True if the native protocol backend claims to be functional."""
        probe = NativeManagementBackend(self.host, port=self.port)
        return probe.supports_native_protocol()

    def get_sdk_diagnostics(self) -> dict[str, object]:
        """Return SDK diagnostics for the current configuration.

        If an SDK backend is already active, reuse its diagnostics. Otherwise,
        run a fresh probe using the current host/port/sdk_path configuration.
        """
        backend = self._backend
        if backend is not None:
            return backend.diagnostics()

        probe = SdkManagementBackend(
            self.host,
            port=self.port,
            sdk_path=self.sdk_path,
            login_mode=self.sdk_login_mode,
            login_connect_type=self.sdk_connect_type,
        )
        return probe.get_sdk_diagnostics()

    def get_backend_diagnostics(self) -> dict[str, object]:
        """Return diagnostics for the active or deterministically selected backend."""
        backend = self._backend or self._select_backend()
        payload = backend.diagnostics()
        payload["backend_mode"] = self.backend_mode
        return payload

    def get_backend_context(self) -> SDKContext:
        """Return normalized SDK context for the active or selected backend."""
        backend = self._backend or self._select_backend()
        return backend.get_context()

    @property
    def backend_name(self) -> str | None:
        """Return the active backend name, if a session has selected one."""
        if self._backend is None:
            return None
        if isinstance(self._backend, SdkManagementBackend):
            return "sdk"
        if isinstance(self._backend, SidecarManagementBackend):
            return "sidecar"
        if isinstance(self._backend, NativeManagementBackend):
            return "native"
        return self._backend.__class__.__name__.lower()

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "ManagementClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
