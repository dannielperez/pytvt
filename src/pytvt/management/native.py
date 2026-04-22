"""Native protocol backend scaffold for TVT management-server.

This backend is intentionally a stub.  Protocol details are unknown and must be
evidence-driven before any implementation is attempted.

TODOs (in dependency order)
----------------------------
1. Capture TCP traffic on port 6003 during a real management-server login from
   the official client (use scripts/re/capture_6003_login.sh).
2. Determine packet framing: magic bytes, length prefix, command word layout.
3. Determine auth handshake: challenge/response, plaintext, or session token.
4. Reverse-engineer device listing and status query message formats.
5. Map server responses to ManagedDevice / DeviceStatus structures.
6. Only then: implement the socket adapter and parser here.
"""

from __future__ import annotations

import platform
from typing import Any

from .base import BaseManagementBackend
from .context import CapabilityMap
from .context import PlatformIdentity
from .context import SDKContext
from .context import SDKIdentity
from .exceptions import CapabilityNotAvailable
from .models import AlarmSubscription, DeviceStatus, ManagedChannel, ManagedDevice, ServerInfo

_NOT_IMPLEMENTED_MSG = (
    "Native protocol not implemented yet. "
    "Packet capture evidence is required before any native implementation. "
    "See TODO list in native.py for the required validation steps."
)


class NativeManagementBackend(BaseManagementBackend):
    """Native transport backend placeholder.

    All methods raise CapabilityNotAvailable until the protocol is
    reverse-engineered from captured traffic.
    """

    def __init__(self, host: str, port: int = 6003) -> None:
        self.host = host
        self.port = port

    @staticmethod
    def _os_family() -> str:
        name = (platform.system() or "unknown").lower()
        return "macos" if name == "darwin" else name

    def load_sdk(self) -> bool:
        return False

    def get_context(self) -> SDKContext:
        return SDKContext(
            platform=PlatformIdentity(
                os_family=self._os_family(),
                arch=platform.machine() or None,
                runtime_kind="native",
            ),
            sdk=SDKIdentity(
                vendor="tvt",
                sdk_name=None,
                sdk_family="unknown",
                sdk_version=None,
            ),
            product_scope={"management_server"},
            capabilities=CapabilityMap(
                supports_init=False,
                supports_login=False,
                supports_login_ex=False,
                supports_logout=False,
                supports_device_enumeration=False,
                supports_alarm_subscription=False,
                supports_management_server_login="not_implemented",
            ),
            notes=[_NOT_IMPLEMENTED_MSG],
        )

    def diagnostics(self) -> dict[str, Any]:
        context = self.get_context().as_dict()
        caps_obj = context.get("capabilities")
        caps: dict[str, Any] = caps_obj if isinstance(caps_obj, dict) else {}
        return {
            "backend": "native_protocol",
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
                    "note": "Native protocol backend remains unimplemented and provisional.",
                },
            },
            "sdk_family": "unknown",
            "supports_login": bool(caps["supports_login"]),
            "supports_login_ex": bool(caps["supports_login_ex"]),
            "supports_device_enumeration": bool(caps["supports_device_enumeration"]),
            "supports_management_server_validation": caps["supports_management_server_login"],
            "note": _NOT_IMPLEMENTED_MSG,
        }

    def login(self, username: str, password: str, device_id: str | None = None) -> bool:
        # TODO: auth framing unknown — do not guess.
        # Requires: capture_6003_login.sh output + tshark_auth_compare.sh analysis.
        del username, password, device_id
        raise CapabilityNotAvailable(_NOT_IMPLEMENTED_MSG)

    def get_server_info(self) -> ServerInfo:
        # TODO: server info command word unknown.
        raise CapabilityNotAvailable(_NOT_IMPLEMENTED_MSG)

    def list_devices(self) -> list[ManagedDevice]:
        # TODO: device enumeration command unknown.
        raise CapabilityNotAvailable(_NOT_IMPLEMENTED_MSG)

    def list_channels(self) -> list[ManagedChannel]:
        # TODO: channel enumeration command unknown.
        raise CapabilityNotAvailable(_NOT_IMPLEMENTED_MSG)

    def get_device_statuses(self) -> list[DeviceStatus]:
        # TODO: status query command unknown.
        raise CapabilityNotAvailable(_NOT_IMPLEMENTED_MSG)

    def subscribe_alarms(self) -> AlarmSubscription:
        # TODO: alarm subscription stream framing unknown.
        raise CapabilityNotAvailable(_NOT_IMPLEMENTED_MSG)

    def close(self) -> None:
        # Nothing to release — no socket opened.
        pass

    def supports_sdk(self) -> bool:
        return False

    def supports_native_protocol(self) -> bool:
        # Returns False until at least login is implemented.
        return False
