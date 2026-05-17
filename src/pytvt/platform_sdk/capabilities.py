"""Capability detection for the PlatformSDK management backend.

Probes a :class:`~pytvt.platform_sdk.ManagementClient` by calling each
read-only method inside a ``try`` block and reports which capabilities
are currently reachable.  Unreachable ones raise
:class:`CapabilityNotAvailable`, which we treat as "not available" rather
than a hard error.

The probe is read-only and safe to call on a live session.
"""

from __future__ import annotations

from typing import Any
from typing import Callable

from .exceptions import CapabilityNotAvailable

__all__ = ["detect_capabilities"]


_PROBES: dict[str, str] = {
    "resources": "list_resources_normalized",
    "servers": "list_servers",
    "alarms": "list_alarm_zones",
    "users": "list_users",
    "logs": "list_logs",
    "tv_wall": "list_tv_walls",
}


def detect_capabilities(client: Any) -> dict[str, bool]:
    """Probe ``client`` for each known read capability.

    Returns a dict with boolean ``True``/``False`` for each capability.
    Any exception other than :class:`CapabilityNotAvailable` is treated
    as a negative result as well — detection is intentionally forgiving
    so it can run against partially-functional backends.
    """

    result: dict[str, bool] = {}
    for cap, method_name in _PROBES.items():
        result[cap] = _probe(client, method_name)
    return result


def _probe(client: Any, method_name: str) -> bool:
    method: Callable[..., Any] | None = getattr(client, method_name, None)
    if method is None or not callable(method):
        return False
    try:
        method()
    except CapabilityNotAvailable:
        return False
    except Exception:  # pragma: no cover - defensive: any failure is a negative
        return False
    return True
