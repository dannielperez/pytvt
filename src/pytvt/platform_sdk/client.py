"""Public management-server client facade for pytvt.

Backend selection
-----------------
ManagementClient selects exactly ONE backend at login time:

    1. native_linux_sdk — NVR device SDK (NET_SDK_* symbols, libdvrnetsdk.so)
    2. platform_sdk     — NVMS PlatformSDK (Plat_* symbols, libPlatClientSDK.so)
    3. sidecar           — sidecar bridge backend
    4. native_protocol  — native protocol backend stub
    5. web               — NVMS web management API (``/service/*``, TVT-1..TVT-3)
    6. auto             — SDK-first fallback to native_protocol

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
from .platform_backend import PlatformSdkManagementBackend
from .platform_models import (
    PlatformAlarmZone,
    PlatformResource,
    PlatformServer,
)
from .sdk import SdkManagementBackend
from .sidecar import SidecarManagementBackend
from .web_backend import WebManagementBackend
from .web_session import DEFAULT_TIMEOUT as WEB_DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)

SdkLoginMode = Literal["login", "login_ex"]
SdkConnectType = Literal["tcp", "nat", "nat20"]
BackendMode = Literal["auto", "native_linux_sdk", "platform_sdk", "native_protocol", "sidecar", "web"]


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
        platform_sdk_path: str | None = None,
        web_scheme: str = "http",
        web_port: int | None = None,
        web_timeout: float = WEB_DEFAULT_TIMEOUT,
        web_verify_tls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.sdk_path = sdk_path
        self.prefer_sdk = prefer_sdk
        self.sdk_login_mode: SdkLoginMode = sdk_login_mode
        self.sdk_connect_type: SdkConnectType = sdk_connect_type
        self.backend_mode: BackendMode = backend_mode
        self.sidecar_command = sidecar_command
        self.platform_sdk_path = platform_sdk_path
        self.web_scheme = web_scheme
        self.web_port = web_port
        self.web_timeout = web_timeout
        self.web_verify_tls = web_verify_tls
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

        if self.backend_mode == "platform_sdk":
            logger.debug("ManagementClient: platform_sdk backend forced for %s", self.host)
            return PlatformSdkManagementBackend(
                self.host,
                port=self.port,
                sdk_path=self.platform_sdk_path or self.sdk_path,
            )

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

        if self.backend_mode == "web":
            logger.debug("ManagementClient: web backend forced for %s", self.host)
            return WebManagementBackend(
                self.host,
                scheme=self.web_scheme,
                port=self.web_port,
                timeout=self.web_timeout,
                verify_tls=self.web_verify_tls,
            )

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
                    "ManagementClient: SDK path provided but library unavailable — falling back to native stub for %s",
                    self.host,
                )

        logger.debug("ManagementClient: native backend selected for %s", self.host)
        return NativeManagementBackend(self.host, port=self.port)

    def _require_backend(self) -> BaseManagementBackend:
        """Return the active backend or raise if not logged in."""
        if self._backend is None:
            raise ManagementNotAuthenticatedError("ManagementClient is not logged in. Call login() first.")
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
            logger.warning("ManagementClient.login() called while already logged in — closing previous session")
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
            method = backend.list_devices_for_login_routing
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

    def list_resources(self) -> list[dict[str, object]]:
        """Return raw management resources if backend supports it."""

        backend = self._require_backend()
        if hasattr(backend, "list_resources"):
            method = backend.list_resources
            return method()

        rows: list[dict[str, object]] = []
        for device in backend.list_devices():
            rows.append(
                {
                    "kind": "device",
                    "id": device.device_id,
                    "name": device.name,
                    "ip": device.ip_address,
                    "status": device.status,
                    "raw_data": device.raw_data,
                }
            )
        for channel in backend.list_channels():
            rows.append(
                {
                    "kind": "channel",
                    "id": channel.channel_id,
                    "parent_device_id": channel.device_id,
                    "name": channel.name,
                    "status": channel.status,
                    "raw_data": channel.raw_data,
                }
            )
        return rows

    def list_server_connection_events(self) -> list[dict[str, object]]:
        """Return raw server-connect callback data when available."""

        backend = self._require_backend()
        if hasattr(backend, "list_server_connection_events"):
            method = backend.list_server_connection_events
            return method()
        return []

    def list_transfer_servers(self) -> list[PlatformServer] | list[dict[str, object]]:
        """Return transfer/server nodes when backend exposes that callback data."""

        backend = self._require_backend()
        if hasattr(backend, "list_transfer_servers"):
            method = backend.list_transfer_servers
            return method()
        return []

    # ------------------------------------------------------------------
    # PlatformSDK-specific read methods
    # ------------------------------------------------------------------
    #
    # Each of the following helpers transparently passes through to the
    # underlying PlatformSDK backend when it supports the call, and raises
    # :class:`CapabilityNotAvailable` otherwise so that callers can feature
    # detect without catching AttributeError.

    def _platform_call(self, method_name: str, *args: object, **kwargs: object) -> object:
        backend = self._require_backend()
        method = getattr(backend, method_name, None)
        if method is None:
            raise CapabilityNotAvailable(f"{method_name} is only available on the platform_sdk backend.")
        return method(*args, **kwargs)

    # -- Normalized resource views --
    def list_resources_normalized(self) -> list[PlatformResource]:
        return self._platform_call("list_resources_normalized")  # type: ignore[return-value]

    def list_areas(self) -> list[PlatformResource]:
        return self._platform_call("list_areas")  # type: ignore[return-value]

    def list_devices_normalized(self) -> list[PlatformResource]:
        return self._platform_call("list_devices_normalized")  # type: ignore[return-value]

    def list_channels_normalized(self) -> list[PlatformResource]:
        return self._platform_call("list_channels_normalized")  # type: ignore[return-value]

    def list_resources_tree(self) -> list[dict[str, object]]:
        return self._platform_call("list_resources_tree")  # type: ignore[return-value]

    def find_resource_by_guid(self, guid: str) -> PlatformResource | None:
        return self._platform_call("find_resource_by_guid", guid)  # type: ignore[return-value]

    def find_resource_by_name(self, name: str) -> list[PlatformResource]:
        return self._platform_call("find_resource_by_name", name)  # type: ignore[return-value]

    # -- Server enumeration --
    def list_servers(self) -> list[PlatformServer]:
        return self._platform_call("list_servers")  # type: ignore[return-value]

    def list_storage_servers(self) -> list[PlatformServer]:
        return self._platform_call("list_storage_servers")  # type: ignore[return-value]

    def list_access_servers(self) -> list[PlatformServer]:
        return self._platform_call("list_access_servers")  # type: ignore[return-value]

    def list_alarm_servers(self) -> list[PlatformServer]:
        return self._platform_call("list_alarm_servers")  # type: ignore[return-value]

    def list_tv_wall_servers(self) -> list[PlatformServer]:
        return self._platform_call("list_tv_wall_servers")  # type: ignore[return-value]

    def list_intelligent_analysis_servers(self) -> list[PlatformServer]:
        return self._platform_call("list_intelligent_analysis_servers")  # type: ignore[return-value]

    def list_alarm_zones(self) -> list[PlatformAlarmZone]:
        return self._platform_call("list_alarm_zones")  # type: ignore[return-value]

    # -- Write scaffolds (dry_run=True by default) --
    def create_user(self, *, dry_run: bool = True, **payload: object) -> dict[str, object]:
        return self._platform_call("create_user", dry_run=dry_run, **payload)  # type: ignore[return-value]

    def create_permission_group(self, *, dry_run: bool = True, **payload: object) -> dict[str, object]:
        return self._platform_call("create_permission_group", dry_run=dry_run, **payload)  # type: ignore[return-value]

    def create_transfer_server(self, *, dry_run: bool = True, **payload: object) -> dict[str, object]:
        return self._platform_call("create_transfer_server", dry_run=dry_run, **payload)  # type: ignore[return-value]

    def create_tv_wall(self, *, dry_run: bool = True, **payload: object) -> dict[str, object]:
        return self._platform_call("create_tv_wall", dry_run=dry_run, **payload)  # type: ignore[return-value]

    def add_device(self, *, dry_run: bool = True, **payload: object) -> dict[str, object]:
        return self._platform_call("add_device", dry_run=dry_run, **payload)  # type: ignore[return-value]

    # -- Not-yet-reachable APIs (raise CapabilityNotAvailable) --
    def list_users(self) -> list[object]:
        return self._platform_call("list_users")  # type: ignore[return-value]

    def list_permission_groups(self) -> list[object]:
        return self._platform_call("list_permission_groups")  # type: ignore[return-value]

    def list_logs(self, **kwargs: object) -> list[object]:
        return self._platform_call("list_logs", **kwargs)  # type: ignore[return-value]

    def list_alarm_logs(self, **kwargs: object) -> list[object]:
        return self._platform_call("list_alarm_logs", **kwargs)  # type: ignore[return-value]

    def list_operation_logs(self, **kwargs: object) -> list[object]:
        return self._platform_call("list_operation_logs", **kwargs)  # type: ignore[return-value]

    def list_exception_logs(self, **kwargs: object) -> list[object]:
        return self._platform_call("list_exception_logs", **kwargs)  # type: ignore[return-value]

    def list_tv_walls(self) -> list[object]:
        return self._platform_call("list_tv_walls")  # type: ignore[return-value]

    def list_alarm_events(self) -> list[object]:
        return self._platform_call("list_alarm_events")  # type: ignore[return-value]

    def list_active_alarms(self) -> list[object]:
        return self._platform_call("list_active_alarms")  # type: ignore[return-value]

    # -- Web-backend read methods (TVT-6/TVT-9; CapabilityNotAvailable until shipped) --
    def list_status_logs(self, **kwargs: object) -> list[object]:
        return self._platform_call("list_status_logs", **kwargs)  # type: ignore[return-value]

    def get_server_statuses(self) -> list[object]:
        return self._platform_call("get_server_statuses")  # type: ignore[return-value]

    def get_acs_statuses(self) -> list[object]:
        return self._platform_call("get_acs_statuses")  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Capability probes (do not require a session)
    # ------------------------------------------------------------------

    def supports_sdk(self) -> bool:
        """Return True if the SDK backend would be usable for this configuration."""
        if self.backend_mode in ("sidecar", "web"):
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
        if isinstance(self._backend, PlatformSdkManagementBackend):
            return "platform_sdk"
        if isinstance(self._backend, SidecarManagementBackend):
            return "sidecar"
        if isinstance(self._backend, WebManagementBackend):
            return "web"
        if isinstance(self._backend, NativeManagementBackend):
            return "native"
        return self._backend.__class__.__name__.lower()

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> ManagementClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
