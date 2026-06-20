"""Lane-routing strategy for pytvt 1.0.

The ``strategy`` lane codifies *which lane handles which operation*.
In 1.0 it is intentionally small — it provides:

  * :class:`Lane` — an enum of the seven public lanes.
  * :class:`OperationKind` — the canonical set of operation categories.
  * :class:`LanePlan` — a typed description of how to route one operation.
  * :class:`LaneResult` — a typed result wrapping a lane call's outcome.
  * :func:`plan_for` — the default routing table.

Callers may build their own plan dict and pass it to higher-level
orchestrators; the function provided here is the recommended default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any

__all__ = [
    "Lane",
    "OperationKind",
    "LanePlan",
    "LaneResult",
    "plan_for",
]


# ── Lanes ────────────────────────────────────────────────────────────


@unique
class Lane(str, Enum):
    """The seven public lanes that pytvt 1.0 exposes."""

    DEVICE_SDK = "device_sdk"
    PLATFORM_SDK = "platform_sdk"
    WEB_API = "web_api"
    XML_API = "xml_api"
    CAPABILITIES = "capabilities"
    STRATEGY = "strategy"
    WORKFLOWS = "workflows"

    def __str__(self) -> str:  # pragma: no cover — cosmetic
        return self.value


# ── Operation categories ─────────────────────────────────────────────


@unique
class OperationKind(str, Enum):
    """Canonical operation categories used for routing.

    These are deliberately coarse — finer-grained dispatch happens
    inside each lane.
    """

    # Discovery / inventory
    DISCOVER = "discover"
    INVENTORY = "inventory"
    HEALTH = "health"
    TOPOLOGY = "topology"

    # Device-level reads / writes
    DEVICE_INFO = "device_info"
    SCAN_CHANNELS = "scan_channels"
    PTZ = "ptz"
    ALARM = "alarm"

    # Account / security
    ROTATE_PASSWORD = "rotate_password"
    LIST_USERS = "list_users"

    # Network / config
    CHANGE_SUBNET = "change_subnet"
    PLATFORM_ACCESS = "platform_access"

    # Validation / verification
    VALIDATE = "validate"

    def __str__(self) -> str:  # pragma: no cover — cosmetic
        return self.value


# ── Plan / result ────────────────────────────────────────────────────


@dataclass(frozen=True)
class LanePlan:
    """Routing plan for one operation.

    ``primary`` is tried first.  If it raises, the orchestrator may
    fall back through ``fallbacks`` in order.  An empty ``fallbacks``
    means "fail hard if primary fails".
    """

    operation: OperationKind
    primary: Lane
    fallbacks: tuple[Lane, ...] = ()
    notes: str = ""

    def all_lanes(self) -> tuple[Lane, ...]:
        """Return ``(primary, *fallbacks)`` in order."""
        return (self.primary, *self.fallbacks)


@dataclass
class LaneResult:
    """Outcome of running an operation through a lane."""

    operation: OperationKind
    lane: Lane
    success: bool = False
    value: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


# ── Default routing table ────────────────────────────────────────────


_DEFAULT_ROUTES: dict[OperationKind, LanePlan] = {
    # Discovery prefers the platform server (it sees everything),
    # but falls back to direct device probes.
    OperationKind.DISCOVER: LanePlan(OperationKind.DISCOVER, Lane.PLATFORM_SDK, (Lane.XML_API, Lane.DEVICE_SDK)),
    OperationKind.INVENTORY: LanePlan(OperationKind.INVENTORY, Lane.PLATFORM_SDK, (Lane.XML_API,)),
    OperationKind.HEALTH: LanePlan(OperationKind.HEALTH, Lane.PLATFORM_SDK, (Lane.XML_API,)),
    OperationKind.TOPOLOGY: LanePlan(OperationKind.TOPOLOGY, Lane.PLATFORM_SDK, ()),
    # Device-level reads/writes go through the device lanes.
    OperationKind.DEVICE_INFO: LanePlan(OperationKind.DEVICE_INFO, Lane.DEVICE_SDK, (Lane.XML_API, Lane.WEB_API)),
    OperationKind.SCAN_CHANNELS: LanePlan(OperationKind.SCAN_CHANNELS, Lane.XML_API, (Lane.DEVICE_SDK,)),
    OperationKind.PTZ: LanePlan(OperationKind.PTZ, Lane.DEVICE_SDK, (Lane.WEB_API,)),
    OperationKind.ALARM: LanePlan(OperationKind.ALARM, Lane.PLATFORM_SDK, (Lane.XML_API,)),
    # Account / security
    OperationKind.ROTATE_PASSWORD: LanePlan(OperationKind.ROTATE_PASSWORD, Lane.WORKFLOWS, ()),
    OperationKind.LIST_USERS: LanePlan(OperationKind.LIST_USERS, Lane.XML_API, (Lane.PLATFORM_SDK,)),
    # Network / config
    OperationKind.CHANGE_SUBNET: LanePlan(OperationKind.CHANGE_SUBNET, Lane.WORKFLOWS, ()),
    OperationKind.PLATFORM_ACCESS: LanePlan(OperationKind.PLATFORM_ACCESS, Lane.XML_API, ()),
    # Verification
    OperationKind.VALIDATE: LanePlan(OperationKind.VALIDATE, Lane.WORKFLOWS, ()),
}


def plan_for(operation: OperationKind) -> LanePlan:
    """Return the default :class:`LanePlan` for ``operation``.

    Raises
    ------
    KeyError
        If ``operation`` is not in the default routing table.
    """
    return _DEFAULT_ROUTES[operation]
