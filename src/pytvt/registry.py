"""Backend and integration-mode registry.

The registry is the single dispatch table that maps
``(BackendFamily, IntegrationMode | None)`` to callable scan functions.

Default registrations are installed at import time so that
``scanner.scan_single_nvr`` works without manual setup.

Third-party code can register additional backends or integration modes
via :func:`register` if needed in the future.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from .constants import (
    BackendFamily,
    CompositeStrategy,
    ExecutionPlan,
    IntegrationMode,
    _make_label,
    resolve_backend,
)
from .exceptions import RegistryError

if TYPE_CHECKING:
    from .models import DeviceEntry, ScannerConfig, ScanResult

logger = logging.getLogger(__name__)

# Type alias for a scan function: (device, config) -> ScanResult
ScanFn = Callable[["DeviceEntry", "ScannerConfig"], "ScanResult"]

# ── Internal registry table ──────────────────────────────────────────

_registry: dict[tuple[BackendFamily, IntegrationMode | None], ScanFn] = {}


def register(
    family: BackendFamily,
    mode: IntegrationMode | None,
    fn: ScanFn,
) -> None:
    """Register a scan function for a ``(family, mode)`` pair.

    Raises:
        RegistryError: If the slot is already occupied.
    """
    key = (family, mode)
    if key in _registry:
        raise RegistryError(f"Backend already registered: {key}")
    _registry[key] = fn
    logger.debug("Registered backend: family=%s mode=%s → %s", family, mode, fn.__qualname__)


def get(
    family: BackendFamily,
    mode: IntegrationMode | None = None,
) -> ScanFn:
    """Look up the scan function for a ``(family, mode)`` pair.

    Raises:
        RegistryError: If no function is registered for that pair.
    """
    key = (family, mode)
    fn = _registry.get(key)
    if fn is None:
        raise RegistryError(f"No backend registered for family={family!r} mode={mode!r}")
    return fn


def registered_keys() -> list[tuple[BackendFamily, IntegrationMode | None]]:
    """Return all currently registered ``(family, mode)`` pairs."""
    return list(_registry.keys())


# ── Default registrations ────────────────────────────────────────────


def _install_defaults() -> None:
    """Wire up the built-in backends.  Called once at module import."""
    # Protocol family — no integration mode
    from .scanner import _protocol_scan

    register(BackendFamily.PROTOCOL, None, _protocol_scan)

    # SDK family — compat bridge (HTTP API → tvt-api Docker)
    from .sdk_http import sdk_scan

    register(BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE, sdk_scan)

    # SDK family — direct SDK (Node.js subprocess → libdvrnetsdk)
    from .sdk_local import sdk_scan_local

    register(BackendFamily.SDK, IntegrationMode.DIRECT_SDK, sdk_scan_local)

    logger.debug("Default backends installed: %s", registered_keys())


# ── Execution plan resolver ──────────────────────────────────────────


def resolve_execution_plan(backend_input: str) -> ExecutionPlan:
    """Build an :class:`ExecutionPlan` from a raw CLI backend string.

    The plan captures the resolved family, mode, strategy, and executor
    *without* running anything.  Call ``plan.execute(device, config)`` to
    actually scan.

    Args:
        backend_input: Any value accepted by :func:`resolve_backend`
            (e.g. ``"protocol"``, ``"sdk"``, ``"sdk-local"``, ``"both"``).

    Returns:
        A frozen :class:`ExecutionPlan` ready for execution.

    Raises:
        ValueError: If *backend_input* is not a recognised name.
        RegistryError: If no scan function is registered for the resolved pair.
    """
    spec_type, mode = resolve_backend(backend_input)

    if isinstance(spec_type, CompositeStrategy):
        return _plan_for_composite(spec_type)

    family: BackendFamily = spec_type
    fn = get(family, mode)
    return ExecutionPlan(
        backend_family=family,
        integration_mode=mode,
        strategy=None,
        executor=fn,
        label=_make_label(family, mode, None),
        steps=[_make_label(family, mode, None)],
    )


def _plan_for_composite(strategy: CompositeStrategy) -> ExecutionPlan:
    """Build an execution plan for a composite (multi-backend) strategy."""
    if strategy is CompositeStrategy.BOTH:
        primary_fn = get(BackendFamily.PROTOCOL, None)
        fallback_fn = get(BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE)

        def _both_executor(device: DeviceEntry, config: ScannerConfig) -> ScanResult:
            logger.info("Scanning %s — strategy=both (protocol → sdk/compat_bridge)", device.ip)
            result = primary_fn(device, config)
            if result.success:
                return result
            logger.info("Protocol failed for %s, falling back to sdk/compat_bridge", device.ip)
            sdk_result = fallback_fn(device, config)
            if sdk_result.success:
                return sdk_result
            result.error = f"protocol: {result.error} | sdk: {sdk_result.error}"
            return result

        return ExecutionPlan(
            backend_family=BackendFamily.PROTOCOL,
            integration_mode=None,
            strategy=strategy,
            executor=_both_executor,
            label=_make_label(BackendFamily.PROTOCOL, None, strategy),
            steps=["protocol", "sdk/compat_bridge"],
        )

    raise RegistryError(f"Unknown composite strategy: {strategy!r}")  # pragma: no cover


# ── Dispatch (uses execution plan internally) ────────────────────────


def dispatch(raw_backend: str, device: DeviceEntry, config: ScannerConfig) -> ScanResult:
    """Resolve a CLI backend string and execute the appropriate scan function.

    This is the primary entry point for scanner code.  It builds an
    :class:`ExecutionPlan` then executes it.

    Args:
        raw_backend: A CLI backend name (e.g. ``"protocol"``, ``"sdk"``,
            ``"sdk-local"``, ``"both"``).
        device: The NVR to scan.
        config: Scanner configuration.

    Returns:
        A :class:`~pytvt.models.ScanResult`.
    """
    plan = resolve_execution_plan(raw_backend)
    logger.info(
        "Scanning %s — plan=%s family=%s mode=%s strategy=%s",
        device.ip,
        plan.label,
        plan.backend_family.value,
        plan.integration_mode.value if plan.integration_mode else "n/a",
        plan.strategy.value if plan.strategy else "n/a",
    )
    return plan.execute(device, config)


# Install defaults on first import
_install_defaults()
