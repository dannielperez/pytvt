"""Sidecar bridge backend for management-server integration.

This backend delegates management operations to an external sidecar bridge process.
It is a first-class runtime mode for environments where the native Linux SDK is not
available (e.g., macOS development hosts, Windows CI runners).

Current operations are provisional; not all management endpoints are implemented yet.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
from typing import Any

from .base import BaseManagementBackend
from .context import CapabilityMap, PlatformIdentity, SDKContext, SDKIdentity
from .exceptions import CapabilityNotAvailable, ExperimentalOperationError, ManagementAuthError
from .models import AlarmSubscription, DeviceStatus, ManagedChannel, ManagedDevice, ServerInfo


class SidecarManagementBackend(BaseManagementBackend):
    """Backend that delegates management commands to an external sidecar bridge process."""

    def __init__(self, host: str, port: int = 6003, bridge_command: str | None = None) -> None:
        self.host = host
        self.port = port
        self.bridge_command = (bridge_command or os.environ.get("PYTVT_MGMT_SIDECAR_CMD") or "").strip()
        self._authenticated = False

    @staticmethod
    def _os_family() -> str:
        name = (platform.system() or "unknown").lower()
        return "macos" if name == "darwin" else name

    def load_sdk(self) -> bool:
        return bool(self.bridge_command)

    def get_context(self) -> SDKContext:
        return SDKContext(
            platform=PlatformIdentity(
                os_family=self._os_family(),
                arch=platform.machine() or None,
                runtime_kind="sidecar",
            ),
            sdk=SDKIdentity(
                vendor="tvt",
                sdk_name="NetClientSDK",
                sdk_family="unknown",
                sdk_version=None,
            ),
            product_scope={"management_server"},
            capabilities=CapabilityMap(
                supports_init=bool(self.bridge_command),
                supports_login=False,
                supports_login_ex=False,
                supports_logout=False,
                supports_device_enumeration=False,
                supports_alarm_subscription=False,
                supports_management_server_login="experimental",
            ),
            notes=[
                "Sidecar backend is a supported runtime mode for SDK-agnostic environments.",
                "Some management operations remain provisional and are not yet implemented.",
            ],
        )

    def diagnostics(self) -> dict[str, Any]:
        context = self.get_context().as_dict()
        caps_obj = context.get("capabilities")
        caps: dict[str, Any] = caps_obj if isinstance(caps_obj, dict) else {}
        return {
            "backend": "sidecar",
            "context": context,
            "platform": context["platform"],
            "sdk": context["sdk"],
            "product_scope": context["product_scope"],
            "capabilities": context["capabilities"],
            "notes": context["notes"],
            "capability_evidence": {
                "supports_login": {
                    "source": "backend",
                    "symbols": [],
                    "confirmed": False,
                },
                "supports_login_ex": {
                    "source": "backend",
                    "symbols": [],
                    "confirmed": False,
                },
                "supports_logout": {
                    "source": "backend",
                    "symbols": [],
                    "confirmed": False,
                },
                "supports_management_server_validation": {
                    "source": "backend",
                    "confirmed": False,
                    "note": "Provisional until validated against correct Linux management SDK.",
                },
            },
            "sdk_family": "unknown",
            "supports_login": bool(caps["supports_login"]),
            "supports_login_ex": bool(caps["supports_login_ex"]),
            "supports_device_enumeration": bool(caps["supports_device_enumeration"]),
            "supports_management_server_validation": caps["supports_management_server_login"],
            "bridge_command_configured": bool(self.bridge_command),
            "note": (
                "Sidecar backend is a supported runtime mode. "
                "Some operations remain provisional and are not yet implemented."
            ),
        }

    def _run_bridge(self, command: str, extra_args: list[str] | None = None) -> dict[str, Any]:
        if not self.bridge_command:
            raise CapabilityNotAvailable("Sidecar backend selected but PYTVT_MGMT_SIDECAR_CMD is not configured.")

        args = extra_args or []
        cmd = f"{self.bridge_command} {command} {' '.join(shlex.quote(item) for item in args)}".strip()
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=False)
        payload: dict[str, Any] = {}
        stdout = (result.stdout or "").strip()
        if stdout:
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                payload = {
                    "ok": False,
                    "command": command,
                    "data": {"error": "invalid_json", "stdout": stdout},
                }
        if result.returncode != 0 and not payload:
            payload = {
                "ok": False,
                "command": command,
                "data": {"error": "bridge_command_failed", "stderr": (result.stderr or "").strip()},
            }
        return payload

    def login(self, username: str, password: str, device_id: str | None = None) -> bool:
        del device_id
        payload = self._run_bridge(
            "login",
            [
                "--host",
                self.host,
                "--port",
                str(self.port),
                "--username",
                username,
                "--password",
                password,
            ],
        )
        if payload.get("ok"):
            self._authenticated = True
            return True
        raise ManagementAuthError(f"Sidecar login failed: {payload}")

    def get_server_info(self) -> ServerInfo:
        raise ExperimentalOperationError("sidecar backend does not expose get_server_info yet")

    def list_devices(self) -> list[ManagedDevice]:
        raise ExperimentalOperationError("sidecar backend does not expose list_devices yet")

    def list_channels(self) -> list[ManagedChannel]:
        raise ExperimentalOperationError("sidecar backend does not expose list_channels yet")

    def get_device_statuses(self) -> list[DeviceStatus]:
        raise ExperimentalOperationError("sidecar backend does not expose get_device_statuses yet")

    def subscribe_alarms(self) -> AlarmSubscription:
        raise ExperimentalOperationError("sidecar backend does not expose subscribe_alarms yet")

    def close(self) -> None:
        if not self._authenticated:
            return
        self._run_bridge("logout")
        self._authenticated = False

    def supports_sdk(self) -> bool:
        return bool(self.bridge_command)

    def supports_native_protocol(self) -> bool:
        return False
