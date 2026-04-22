"""Minimal stable contract for SDK-backed management validation.

This contract is intentionally transport-agnostic and excludes backend-specific
experimental knobs like NAT/connect_type/deviceSN.
"""

from __future__ import annotations

from typing import Any, Protocol

from .context import SDKContext


class ManagementSDKContract(Protocol):
    def load_sdk(self) -> bool: ...

    def diagnostics(self) -> dict[str, Any]: ...

    def get_context(self) -> SDKContext: ...

    def init(self) -> None: ...

    def login(self, host: str, port: int, username: str, password: str) -> int: ...

    def logout(self, handle: int) -> None: ...

    def cleanup(self) -> None: ...

    def list_devices(self, handle: int) -> list[dict[str, Any]]: ...


def stable_contract_definition() -> dict[str, dict[str, Any]]:
    """Return canonical stable method set for SDK-backed backends."""
    return {
        "load_sdk": {"required": True},
        "diagnostics": {"required": True},
        "get_context": {"required": True},
        "init": {"required": True},
        "login": {
            "required": True,
            "signature": "login(host: str, port: int, username: str, password: str) -> int",
        },
        "logout": {"required": True},
        "cleanup": {"required": True},
        "list_devices": {"required": False},
    }
