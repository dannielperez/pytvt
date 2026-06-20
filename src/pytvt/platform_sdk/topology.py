"""Site topology builder for PlatformSDK resources.

Takes the flat list of normalized :class:`PlatformResource` rows (plus any
alarm zones) produced by the PlatformSDK backend and groups them into
logical "sites".  A *site* is a top-level area in the management server's
resource tree; all devices under that area and all channels under those
devices belong to it.

The implementation is deterministic (stable ordering based on integer
``node_id``) and defensive (orphans are collected under a synthetic
``"orphans"`` site rather than dropped).

This module has **no** runtime dependency on the SDK — callers pass in the
already-normalized data — which makes it trivially unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .platform_constants import NODETYPE_AREA, NODETYPE_CHANNEL, NODETYPE_DEVICE
from .platform_models import PlatformAlarmZone, PlatformResource

__all__ = ["PlatformSite", "build_site_topology"]


ORPHAN_SITE_ID = "orphans"


@dataclass(frozen=True)
class PlatformSite:
    """A logical site: a top-level area plus the devices/channels beneath it."""

    id: str
    name: str
    root_resource_guid: str
    devices: list[PlatformResource] = field(default_factory=list)
    channels: list[PlatformResource] = field(default_factory=list)
    alarm_zones: list[PlatformAlarmZone] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "root_resource_guid": self.root_resource_guid,
            "devices": [d.as_dict() for d in self.devices],
            "channels": [c.as_dict() for c in self.channels],
            "alarm_zones": [z.as_dict() for z in self.alarm_zones],
        }


def _resource_guid(resource: PlatformResource) -> str:
    """Return a stable string id for a resource (GUID if available, else node_id)."""

    guid = str(resource.raw_data.get("guidNodeID", "") or "").strip()
    return guid or str(resource.node_id)


def _resource_parent_guid(resource: PlatformResource) -> str:
    guid = str(resource.raw_data.get("guidParentID", "") or "").strip()
    return guid or str(resource.parent_id)


def build_site_topology(
    resources: Iterable[PlatformResource],
    alarm_zones: Iterable[PlatformAlarmZone] | None = None,
) -> list[PlatformSite]:
    """Group ``resources`` into :class:`PlatformSite` records.

    A site root is any area whose ``parent_id`` does not itself refer to
    another area in ``resources`` (or is ``0``).  Devices are placed in the
    site corresponding to their nearest ancestor area.  Channels follow
    their parent device.  Anything we cannot place is collected under an
    :data:`ORPHAN_SITE_ID` synthetic site.

    The function is deterministic: sites are returned sorted by
    ``(name, id)`` and devices/channels inside each site by integer
    ``node_id``.
    """

    resources_list = list(resources)
    zones_list = list(alarm_zones or [])

    by_node_id: dict[int, PlatformResource] = {r.node_id: r for r in resources_list}
    areas = [r for r in resources_list if r.node_type == NODETYPE_AREA]
    devices = [r for r in resources_list if r.node_type == NODETYPE_DEVICE]
    channels = [r for r in resources_list if r.node_type == NODETYPE_CHANNEL]

    area_ids = {a.node_id for a in areas}

    # Determine site-root areas: areas whose parent is not another area.
    site_root_ids: set[int] = set()
    for area in areas:
        if area.parent_id == 0 or area.parent_id not in area_ids:
            site_root_ids.add(area.node_id)

    # Walk each area up through area-only parents to find its site root.
    def _find_site_root(area_id: int) -> int | None:
        seen: set[int] = set()
        current = area_id
        while current and current not in seen:
            seen.add(current)
            if current in site_root_ids:
                return current
            node = by_node_id.get(current)
            if node is None or node.node_type != NODETYPE_AREA:
                return None
            if node.parent_id == current:
                return None
            current = node.parent_id
        return None

    # Find the nearest ancestor area for any node (device or otherwise).
    def _nearest_area(node: PlatformResource) -> int | None:
        seen: set[int] = set()
        parent_id = node.parent_id
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = by_node_id.get(parent_id)
            if parent is None:
                return None
            if parent.node_type == NODETYPE_AREA:
                return parent.node_id
            if parent.parent_id == parent_id:
                return None
            parent_id = parent.parent_id
        return None

    device_site_root: dict[int, int] = {}
    site_devices: dict[int, list[PlatformResource]] = {rid: [] for rid in site_root_ids}
    orphan_devices: list[PlatformResource] = []

    for dev in devices:
        area_id = _nearest_area(dev)
        if area_id is None:
            orphan_devices.append(dev)
            continue
        root_id = _find_site_root(area_id)
        if root_id is None:
            orphan_devices.append(dev)
            continue
        device_site_root[dev.node_id] = root_id
        site_devices[root_id].append(dev)

    site_channels: dict[int, list[PlatformResource]] = {rid: [] for rid in site_root_ids}
    orphan_channels: list[PlatformResource] = []

    for chan in channels:
        root_id = device_site_root.get(chan.parent_id)
        if root_id is None:
            # Channel may be parented directly under an area in some server
            # configurations — try to find the nearest area.
            area_id = _nearest_area(chan)
            if area_id is not None:
                root_id = _find_site_root(area_id)
        if root_id is None:
            orphan_channels.append(chan)
            continue
        site_channels[root_id].append(chan)

    # Map alarm zones to sites by the host_guid matching a device's GUID.
    device_guid_to_root: dict[str, int] = {}
    for dev in devices:
        root_id = device_site_root.get(dev.node_id)
        if root_id is None:
            continue
        guid = _resource_guid(dev)
        if guid:
            device_guid_to_root[guid.lower()] = root_id

    site_zones: dict[int, list[PlatformAlarmZone]] = {rid: [] for rid in site_root_ids}
    orphan_zones: list[PlatformAlarmZone] = []
    for zone in zones_list:
        host_guid = (zone.host_guid or "").strip().lower()
        root_id = device_guid_to_root.get(host_guid) if host_guid else None
        if root_id is None:
            orphan_zones.append(zone)
        else:
            site_zones[root_id].append(zone)

    sites: list[PlatformSite] = []
    for root_id in site_root_ids:
        root = by_node_id[root_id]
        sites.append(
            PlatformSite(
                id=str(root_id),
                name=root.name or f"site_{root_id}",
                root_resource_guid=_resource_guid(root),
                devices=sorted(site_devices[root_id], key=lambda r: r.node_id),
                channels=sorted(site_channels[root_id], key=lambda r: r.node_id),
                alarm_zones=sorted(site_zones[root_id], key=lambda z: (z.name, z.guid)),
            )
        )

    if orphan_devices or orphan_channels or orphan_zones:
        sites.append(
            PlatformSite(
                id=ORPHAN_SITE_ID,
                name="Unassigned",
                root_resource_guid="",
                devices=sorted(orphan_devices, key=lambda r: r.node_id),
                channels=sorted(orphan_channels, key=lambda r: r.node_id),
                alarm_zones=sorted(orphan_zones, key=lambda z: (z.name, z.guid)),
            )
        )

    sites.sort(key=lambda s: (s.id == ORPHAN_SITE_ID, s.name.lower(), s.id))
    return sites
