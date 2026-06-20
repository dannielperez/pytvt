"""Device-level health computation for PlatformSDK data.

Given the already-normalized resources and servers, produce a
:class:`PlatformDeviceHealth` row per device summarizing its operational
state.  The input is pure data — no SDK calls — so the logic is
deterministic and fully unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from .platform_constants import NODETYPE_CHANNEL
from .platform_models import PlatformResource, PlatformServer
from .topology import PlatformSite, build_site_topology

__all__ = ["PlatformDeviceHealth", "compute_device_health"]


HealthStatus = Literal["ONLINE", "DEGRADED", "OFFLINE"]


@dataclass(frozen=True)
class PlatformDeviceHealth:
    device_guid: str
    device_name: str
    site_id: str
    status: HealthStatus
    total_channels: int
    online_channels: int
    last_event: str | None = None
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_guid": self.device_guid,
            "device_name": self.device_name,
            "site_id": self.site_id,
            "status": self.status,
            "total_channels": self.total_channels,
            "online_channels": self.online_channels,
            "last_event": self.last_event,
            "issues": list(self.issues),
        }


def _device_guid(resource: PlatformResource) -> str:
    guid = str(resource.raw_data.get("guidNodeID", "") or "").strip()
    return guid or str(resource.node_id)


def _is_online(value: bool | None) -> bool:
    return value is True


def compute_device_health(
    resources: Iterable[PlatformResource],
    servers: Iterable[PlatformServer] | None = None,
    alarm_events: Iterable[Any] | None = None,
) -> list[PlatformDeviceHealth]:
    """Compute per-device health from a normalized resource list.

    Rules:
      * ``OFFLINE``  — device ``online`` is not True, or device has zero
        channels AND is marked offline.
      * ``DEGRADED`` — some channels offline (``0 < online < total``) or an
        alarm-event references the device.
      * ``ONLINE``   — device is online and either has no channels or all
        channels are online.
    """

    resources_list = list(resources)
    servers_list = list(servers or [])
    events_list = list(alarm_events or [])

    sites = build_site_topology(resources_list)
    site_of_device: dict[int, PlatformSite] = {}
    for site in sites:
        for dev in site.devices:
            site_of_device[dev.node_id] = site

    # Channels grouped by parent device node_id.
    channels_by_device: dict[int, list[PlatformResource]] = {}
    for r in resources_list:
        if r.node_type == NODETYPE_CHANNEL:
            channels_by_device.setdefault(r.parent_id, []).append(r)

    # Index alarm events by device guid (accept raw dicts or normalized events).
    events_by_device: dict[str, list[Any]] = {}
    for evt in events_list:
        guid = _event_device_guid(evt)
        if guid:
            events_by_device.setdefault(guid.lower(), []).append(evt)

    # Offline servers flag to add as an issue when detectable.
    offline_server_guids = {s.guid.lower() for s in servers_list if s.online is False and s.guid}

    devices = [r for r in resources_list if r.node_type == 2]  # NODETYPE_DEVICE
    health: list[PlatformDeviceHealth] = []
    for dev in devices:
        guid = _device_guid(dev)
        site = site_of_device.get(dev.node_id)
        site_id = site.id if site else "orphans"
        channels = channels_by_device.get(dev.node_id, [])
        total = len(channels)
        online_channels = sum(1 for c in channels if _is_online(c.online))
        dev_online = _is_online(dev.online)

        issues: list[str] = []
        if not dev_online:
            issues.append("device_offline")
        if total > 0 and online_channels < total:
            issues.append(f"channels_offline:{total - online_channels}/{total}")
        dev_events = events_by_device.get(guid.lower(), [])
        if dev_events:
            issues.append(f"alarm_events:{len(dev_events)}")
        if guid.lower() in offline_server_guids:
            issues.append("server_offline")

        if not dev_online:
            status: HealthStatus = "OFFLINE"
        elif total > 0 and online_channels == 0:
            status = "OFFLINE"
        elif (total > 0 and online_channels < total) or dev_events:
            status = "DEGRADED"
        else:
            status = "ONLINE"

        last_event = _event_timestamp_str(dev_events[-1]) if dev_events else None

        health.append(
            PlatformDeviceHealth(
                device_guid=guid,
                device_name=dev.name or f"device_{dev.node_id}",
                site_id=site_id,
                status=status,
                total_channels=total,
                online_channels=online_channels,
                last_event=last_event,
                issues=issues,
            )
        )

    health.sort(key=lambda h: (h.site_id, h.device_name.lower(), h.device_guid))
    return health


def _event_device_guid(event: Any) -> str:
    if hasattr(event, "device_guid"):
        return str(event.device_guid or "")
    if isinstance(event, dict):
        for key in ("device_guid", "deviceGuid", "guidNodeID", "guid"):
            value = event.get(key)
            if value:
                return str(value)
    return ""


def _event_timestamp_str(event: Any) -> str | None:
    if hasattr(event, "timestamp"):
        ts = event.timestamp
        return str(ts) if ts is not None else None
    if isinstance(event, dict):
        for key in ("timestamp", "time", "ts"):
            value = event.get(key)
            if value is not None:
                return str(value)
    return None
