"""Composite inventory snapshot API for the PlatformSDK backend.

Stitches together topology, health, classification and alarm
normalization into one JSON-safe dict suitable for export into UniqueOS
or for CLI display.  Gracefully tolerates missing capabilities.
"""

from __future__ import annotations

from typing import Any

from .management.exceptions import CapabilityNotAvailable
from .management.platform_constants import redact_sensitive
from .management.platform_models import PlatformAlarmZone
from .management.platform_models import PlatformResource
from .management.platform_models import PlatformServer
from .platform_alarms import normalize_alarm_events
from .platform_capabilities import detect_capabilities
from .platform_classification import classify_resource
from .platform_health import compute_device_health
from .platform_topology import build_site_topology

__all__ = ["get_platform_inventory_snapshot"]


def _safe_call(fn: Any, default: Any) -> Any:
    try:
        return fn()
    except CapabilityNotAvailable:
        return default
    except Exception:  # pragma: no cover - defensive
        return default


def _resource_payload(resource: PlatformResource) -> dict[str, Any]:
    payload = resource.as_dict()
    payload["classification"] = classify_resource(resource)
    return payload


def _server_payload(server: PlatformServer) -> dict[str, Any]:
    payload = server.as_dict()
    # Defense-in-depth: scrub any credentials that might have been stuffed
    # into the raw row by a future backend change.
    payload = redact_sensitive(payload)
    return payload


def get_platform_inventory_snapshot(client: Any) -> dict[str, Any]:
    """Return a consolidated, JSON-safe inventory snapshot from ``client``.

    The function never raises for missing capabilities; unavailable
    sections are simply returned as empty lists and recorded in
    ``snapshot["capabilities"]``.
    """

    capabilities = detect_capabilities(client)

    resources: list[PlatformResource] = _safe_call(
        getattr(client, "list_resources_normalized", lambda: []), []
    ) or []
    servers: list[PlatformServer] = _safe_call(
        getattr(client, "list_servers", lambda: []), []
    ) or []
    alarm_zones: list[PlatformAlarmZone] = _safe_call(
        getattr(client, "list_alarm_zones", lambda: []), []
    ) or []
    alarm_events_raw: list[Any] = _safe_call(
        getattr(client, "list_alarm_events", lambda: []), []
    ) or []

    sites = build_site_topology(resources, alarm_zones)

    # Build device_guid -> site_id lookup for alarm normalization.
    site_lookup: dict[str, str] = {}
    for site in sites:
        for dev in site.devices:
            guid = str(dev.raw_data.get("guidNodeID", "") or "").strip() or str(dev.node_id)
            if guid:
                site_lookup[guid.lower()] = site.id

    alarm_events = normalize_alarm_events(alarm_events_raw, site_lookup=site_lookup)

    health = compute_device_health(resources, servers, alarm_events)

    devices = [
        _resource_payload(r)
        for r in resources
        if r.node_type == 2  # NODETYPE_DEVICE
    ]
    channels = [
        _resource_payload(r)
        for r in resources
        if r.node_type == 3  # NODETYPE_CHANNEL
    ]

    status_counts = {"ONLINE": 0, "DEGRADED": 0, "OFFLINE": 0}
    for h in health:
        if h.status in status_counts:
            status_counts[h.status] += 1

    summary = {
        "site_count": sum(1 for s in sites if s.id != "orphans"),
        "device_count": len(devices),
        "channel_count": len(channels),
        "offline_devices": status_counts["OFFLINE"],
        "degraded_devices": status_counts["DEGRADED"],
        "online_devices": status_counts["ONLINE"],
    }

    return {
        "capabilities": capabilities,
        "sites": [s.as_dict() for s in sites],
        "devices": devices,
        "channels": channels,
        "servers": [_server_payload(s) for s in servers],
        "alarm_zones": [z.as_dict() for z in alarm_zones],
        "alarm_events": [e.as_dict() for e in alarm_events],
        "health": [h.as_dict() for h in health],
        "summary": summary,
    }
