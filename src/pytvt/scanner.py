"""Scan orchestration — dispatches to protocol, SDK HTTP, or SDK local backends.

Public API
----------
.. autofunction:: load_devices
.. autofunction:: filter_tvt_devices
.. autofunction:: scan_single_nvr
"""

from __future__ import annotations

import json
import logging

from .models import CameraInfo, DeviceEntry, ScannerConfig, ScanResult
from .protocol import scan_nvr as _raw_protocol_scan

logger = logging.getLogger(__name__)

# Manufacturer names and MAC OUI prefixes recognised as TVT/compatible.
_TVT_MANUFACTURERS: frozenset[str] = frozenset({"TVT", "UNIQUE"})
_TVT_MAC_PREFIXES: tuple[str, ...] = ("58:5B:69",)


def load_devices(json_path: str) -> list[DeviceEntry]:
    """Load a JSON array of device dicts from *json_path*.

    Raises:
        ValueError: If the file does not contain a JSON array.
    """
    with open(json_path) as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError(f"{json_path} must contain a JSON array")

    return [DeviceEntry.from_dict(d) for d in raw]


def filter_tvt_devices(devices: list[DeviceEntry]) -> list[DeviceEntry]:
    """Keep only devices whose manufacturer or MAC prefix indicates TVT."""
    return [d for d in devices if _is_tvt(d)]


def scan_single_nvr(
    device: DeviceEntry,
    config: ScannerConfig,
    backend: str = "protocol",
) -> ScanResult:
    """Scan a single NVR using the chosen *backend*.

    Args:
        device:  Inventory entry for the NVR.
        config:  Scanner configuration.
        backend: One of ``"protocol"``, ``"sdk"``, ``"sdk-local"``, ``"both"``.
                 Also accepts canonical names like ``"compat_bridge"`` /
                 ``"direct_sdk"`` and underscore variants.

    Returns:
        A :class:`ScanResult` populated by the backend.

    Raises:
        ValueError: If *backend* is not a recognised name.
    """
    from .registry import dispatch

    return dispatch(backend, device, config)


# ── Private helpers ──────────────────────────────────────────────────


def _is_tvt(device: DeviceEntry) -> bool:
    """Return *True* if *device* looks like a TVT product."""
    mfr = device.manufacturer.strip().upper()
    if mfr in _TVT_MANUFACTURERS:
        return True
    mac = device.mac.strip().upper()
    return any(mac.startswith(p) for p in _TVT_MAC_PREFIXES)


def _protocol_scan(device: DeviceEntry, config: ScannerConfig) -> ScanResult:
    """Scan one NVR using the pure-Python TVT binary protocol."""
    port = device.effective_port(config)
    raw = _raw_protocol_scan(
        device.ip,
        port=port,
        username=config.username,
        password=config.password,
        timeout=config.timeout,
    )

    cameras = [CameraInfo.from_dict(c) for c in raw.get("cameras", [])]

    return ScanResult(
        site=device.site,
        hostname=device.hostname,
        nvr_ip=device.ip,
        nvr_mac=device.mac,
        nvr_port=port,
        success=raw.get("success", False),
        device_name=raw.get("device_name", ""),
        device_model=raw.get("device_model", ""),
        serial_number=raw.get("serial_number", ""),
        firmware=raw.get("firmware", ""),
        total_channels=raw.get("total_channels", 0),
        cameras=cameras,
        error=raw.get("error"),
        backend="protocol",
        device_info=raw.get("device_info", {}),
    )
