"""Canonical constants for backend families, integration modes, and CLI aliases.

Terminology
-----------
**Backend family** — *what* technology scans the NVR:

- ``protocol`` — pure-Python binary protocol client (port 6036).
- ``sdk`` — the vendor's native ``libdvrnetsdk`` shared library.

**Integration mode** — *how* an SDK-family scan reaches the native library:

- ``compat_bridge`` — HTTP POST to a compatible SDK bridge service.
- ``direct_sdk`` — direct Python ``ctypes`` calls into ``libdvrnetsdk.so``.

The protocol family has no integration modes; it connects directly to the NVR.

**Composite strategy** — a retry strategy that tries one family then falls back:

- ``both`` — protocol first, SDK (compat_bridge) fallback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any

# ── Backend family ───────────────────────────────────────────────────


@unique
class BackendFamily(str, Enum):
    """Technology family used to scan an NVR."""

    PROTOCOL = "protocol"
    SDK = "sdk"

    def __str__(self) -> str:  # pragma: no cover — cosmetic
        return self.value


# ── Integration mode ─────────────────────────────────────────────────


@unique
class IntegrationMode(str, Enum):
    """How an SDK-family scan reaches the native library."""

    COMPAT_BRIDGE = "compat_bridge"
    DIRECT_SDK = "direct_sdk"

    def __str__(self) -> str:  # pragma: no cover — cosmetic
        return self.value


# ── Composite strategy ───────────────────────────────────────────────


@unique
class CompositeStrategy(str, Enum):
    """Multi-backend retry strategies."""

    BOTH = "both"

    def __str__(self) -> str:  # pragma: no cover — cosmetic
        return self.value


# ── Execution plan ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionPlan:
    """A fully-resolved, inspectable description of what *will* be executed.

    Built by :func:`pytvt.registry.resolve_execution_plan` from a raw CLI
    backend string.  The scanner calls ``plan.execute(device, config)``
    rather than reaching into the registry directly.
    """

    backend_family: BackendFamily
    integration_mode: IntegrationMode | None
    strategy: CompositeStrategy | None
    executor: Callable[..., Any]
    label: str
    steps: list[str] = field(default_factory=list)

    # ── convenience ──────────────────────────────────────────────

    @property
    def is_composite(self) -> bool:
        """True when this plan uses a multi-backend strategy."""
        return self.strategy is not None

    def execute(self, device: Any, config: Any) -> Any:
        """Run the plan's executor.  Thin wrapper for readability."""
        return self.executor(device, config)


def _make_label(
    family: BackendFamily,
    mode: IntegrationMode | None,
    strategy: CompositeStrategy | None,
) -> str:
    """Build a human-readable label for an execution plan."""
    if strategy is CompositeStrategy.BOTH:
        return "both (protocol → sdk/compat_bridge)"
    if mode is not None:
        return f"{family.value}/{mode.value}"
    return family.value


# ── CLI alias mapping ────────────────────────────────────────────────

# Maps every accepted --backend string to (BackendFamily | CompositeStrategy, IntegrationMode | None).
# This is the single source of truth for backward-compatible CLI name resolution.

BackendSpec = tuple[BackendFamily | CompositeStrategy, IntegrationMode | None]

CLI_BACKEND_ALIASES: dict[str, BackendSpec] = {
    # Protocol family — no integration mode
    "protocol": (BackendFamily.PROTOCOL, None),
    # SDK family — explicit integration modes
    "sdk": (BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE),
    "sdk-local": (BackendFamily.SDK, IntegrationMode.DIRECT_SDK),
    # Backward-compatible aliases (underscore variants)
    "sdk_local": (BackendFamily.SDK, IntegrationMode.DIRECT_SDK),
    "compat_bridge": (BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE),
    "direct_sdk": (BackendFamily.SDK, IntegrationMode.DIRECT_SDK),
    # Composite strategy
    "both": (CompositeStrategy.BOTH, None),
}

# The canonical set of choices exposed in --help (backward-compatible).
CLI_BACKEND_CHOICES: list[str] = ["protocol", "sdk", "sdk-local", "both"]


def resolve_backend(raw: str) -> BackendSpec:
    """Normalize a raw backend string into a ``(family_or_strategy, mode)`` pair.

    Accepts any key in :data:`CLI_BACKEND_ALIASES` (case-insensitive).

    Raises:
        ValueError: If *raw* is not a recognised backend name.
    """
    key = raw.strip().lower().replace("-", "_")
    # Try exact match first (handles both hyphen and underscore forms)
    spec = CLI_BACKEND_ALIASES.get(key) or CLI_BACKEND_ALIASES.get(raw.strip().lower())
    if spec is None:
        valid = ", ".join(sorted(CLI_BACKEND_ALIASES))
        raise ValueError(f"Unknown backend {raw!r}; valid names: {valid}")
    return spec
