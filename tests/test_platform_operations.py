"""Tests for Phase 2 platform_* operational-intelligence modules."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest

from pytvt.platform_sdk.platform_constants import (
    DEVTYPE_IPC,
    DEVTYPE_NONE,
    DEVTYPE_NVR,
    NODETYPE_AREA,
    NODETYPE_CHANNEL,
    NODETYPE_DEVICE,
)
from pytvt.platform_sdk.platform_models import (
    PlatformAlarmZone,
    PlatformResource,
    PlatformServer,
)
from pytvt.platform_sdk.alarms import normalize_alarm_events
from pytvt.platform_sdk.capabilities import detect_capabilities
from pytvt.platform_sdk.classification import classify_resource
from pytvt.platform_sdk.health import compute_device_health
from pytvt.platform_sdk.inventory import get_platform_inventory_snapshot
from pytvt.platform_sdk.topology import ORPHAN_SITE_ID, build_site_topology
from pytvt.platform_sdk.exceptions import CapabilityNotAvailable


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _area(
    node_id: int, parent_id: int, name: str, *, guid: str | None = None
) -> PlatformResource:
    guid = guid or f"area-{node_id:08x}"
    return PlatformResource(
        node_id=node_id,
        parent_id=parent_id,
        name=name,
        node_type=NODETYPE_AREA,
        node_type_name="area",
        device_type=DEVTYPE_NONE,
        device_type_name="none",
        online=None,
        ip="",
        channel_count=0,
        channel_number=-1,
        supports_face_match=False,
        raw_data={"guidNodeID": guid, "guidParentID": ""},
    )


def _device(
    node_id: int,
    parent_id: int,
    name: str,
    *,
    online: bool | None = True,
    dev_type: int = DEVTYPE_NVR,
    channel_count: int = 4,
    guid: str | None = None,
) -> PlatformResource:
    guid = guid or f"dev-{node_id:08x}"
    return PlatformResource(
        node_id=node_id,
        parent_id=parent_id,
        name=name,
        node_type=NODETYPE_DEVICE,
        node_type_name="device",
        device_type=dev_type,
        device_type_name="nvr" if dev_type == DEVTYPE_NVR else "ipc",
        online=online,
        ip="10.0.0.1",
        channel_count=channel_count,
        channel_number=-1,
        supports_face_match=False,
        raw_data={"guidNodeID": guid},
    )


def _channel(
    node_id: int,
    parent_id: int,
    name: str,
    *,
    online: bool | None = True,
) -> PlatformResource:
    return PlatformResource(
        node_id=node_id,
        parent_id=parent_id,
        name=name,
        node_type=NODETYPE_CHANNEL,
        node_type_name="channel",
        device_type=DEVTYPE_IPC,
        device_type_name="ipc",
        online=online,
        ip="",
        channel_count=0,
        channel_number=node_id % 10,
        supports_face_match=False,
        raw_data={"guidNodeID": f"chan-{node_id:08x}"},
    )


@pytest.fixture
def small_tree() -> list[PlatformResource]:
    # Site A: area(1) -> device(10) -> channels(100, 101)
    # Site B: area(2) -> sub-area(3) -> device(20) -> channel(200)
    # Orphan device(30) with no parent in tree.
    return [
        _area(1, 0, "Site A"),
        _area(2, 0, "Site B"),
        _area(3, 2, "B Sub"),
        _device(10, 1, "NVR-A1"),
        _device(20, 3, "NVR-B1"),
        _device(30, 999, "Lost NVR"),
        _channel(100, 10, "Cam A1-1"),
        _channel(101, 10, "Cam A1-2", online=False),
        _channel(200, 20, "Cam B1-1"),
    ]


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


def test_topology_groups_sites_and_orphans(small_tree: list[PlatformResource]) -> None:
    sites = build_site_topology(small_tree)
    by_id = {s.id: s for s in sites}

    assert "1" in by_id and "2" in by_id
    assert ORPHAN_SITE_ID in by_id

    site_a = by_id["1"]
    assert site_a.name == "Site A"
    assert [d.node_id for d in site_a.devices] == [10]
    assert [c.node_id for c in site_a.channels] == [100, 101]

    site_b = by_id["2"]
    assert [d.node_id for d in site_b.devices] == [20]
    assert [c.node_id for c in site_b.channels] == [200]

    orphans = by_id[ORPHAN_SITE_ID]
    assert [d.node_id for d in orphans.devices] == [30]


def test_topology_is_deterministic(small_tree: list[PlatformResource]) -> None:
    sites1 = build_site_topology(small_tree)
    sites2 = build_site_topology(list(reversed(small_tree)))
    assert [s.id for s in sites1] == [s.id for s in sites2]
    assert [
        [d.node_id for d in s.devices] for s in sites1
    ] == [[d.node_id for d in s.devices] for s in sites2]


def test_topology_alarm_zone_mapping() -> None:
    resources = [
        _area(1, 0, "Site A"),
        _device(10, 1, "NVR-A1", guid="aaaa"),
    ]
    zones = [
        PlatformAlarmZone(guid="z1", host_guid="aaaa", name="Door", zone_type=0),
        PlatformAlarmZone(guid="z2", host_guid="unknown", name="Ghost", zone_type=0),
    ]
    sites = build_site_topology(resources, zones)
    by_id = {s.id: s for s in sites}
    assert [z.guid for z in by_id["1"].alarm_zones] == ["z1"]
    assert [z.guid for z in by_id[ORPHAN_SITE_ID].alarm_zones] == ["z2"]


def test_topology_handles_empty_input() -> None:
    assert build_site_topology([]) == []


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_online_degraded_offline(small_tree: list[PlatformResource]) -> None:
    health = compute_device_health(small_tree)
    by_name = {h.device_name: h for h in health}

    # NVR-A1: 1 online / 2 total => DEGRADED
    a1 = by_name["NVR-A1"]
    assert a1.status == "DEGRADED"
    assert a1.total_channels == 2 and a1.online_channels == 1
    assert any("channels_offline" in i for i in a1.issues)

    # NVR-B1: 1 online / 1 total => ONLINE
    b1 = by_name["NVR-B1"]
    assert b1.status == "ONLINE"
    assert b1.issues == []

    # Lost NVR: orphan but has online=True and no channels => ONLINE
    lost = by_name["Lost NVR"]
    assert lost.site_id == ORPHAN_SITE_ID


def test_health_offline_device_marked_offline() -> None:
    resources = [
        _area(1, 0, "Site"),
        _device(10, 1, "Dead", online=False, channel_count=0),
    ]
    [h] = compute_device_health(resources)
    assert h.status == "OFFLINE"
    assert "device_offline" in h.issues


def test_health_all_channels_offline_is_offline() -> None:
    resources = [
        _area(1, 0, "Site"),
        _device(10, 1, "Dev"),
        _channel(100, 10, "c1", online=False),
        _channel(101, 10, "c2", online=False),
    ]
    [h] = compute_device_health(resources)
    assert h.status == "OFFLINE"


def test_health_alarm_event_degrades_online_device() -> None:
    resources = [
        _area(1, 0, "Site"),
        _device(10, 1, "Dev", guid="abc"),
    ]
    events = [{"device_guid": "abc", "type": "Motion", "timestamp": "2026-01-01T00:00:00Z"}]
    [h] = compute_device_health(resources, servers=[], alarm_events=events)
    assert h.status == "DEGRADED"
    assert h.last_event is not None


# ---------------------------------------------------------------------------
# Alarms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_type,expected_sev",
    [
        ("Intrusion Detection", "intrusion", "high"),
        ("Line Crossing Detection", "line_crossing", "medium"),
        ("Offline", "offline", "high"),
        ("Motion", "motion", "low"),
        ("something weird", "unknown", "low"),
    ],
)
def test_alarm_normalization_mapping(
    raw: str, expected_type: str, expected_sev: str
) -> None:
    events = normalize_alarm_events([{"device_guid": "x", "type": raw}])
    assert len(events) == 1
    assert events[0].type == expected_type
    assert events[0].severity == expected_sev
    assert events[0].raw_type == raw


def test_alarm_timestamp_parsing_formats() -> None:
    events = normalize_alarm_events(
        [
            {"device_guid": "a", "type": "Offline", "timestamp": "2026-04-23T10:20:30Z"},
            {"device_guid": "b", "type": "Offline", "timestamp": 1700000000},
            {"device_guid": "c", "type": "Offline", "timestamp": None},
            {"device_guid": "d", "type": "Offline", "timestamp": "junk"},
        ]
    )
    assert events[0].timestamp == datetime(2026, 4, 23, 10, 20, 30, tzinfo=timezone.utc)
    assert events[1].timestamp is not None
    assert events[2].timestamp is None
    assert events[3].timestamp is None


def test_alarm_site_lookup_fills_site_id() -> None:
    events = normalize_alarm_events(
        [{"device_guid": "ABC", "type": "Intrusion"}],
        site_lookup={"abc": "site-7"},
    )
    assert events[0].site_id == "site-7"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_nvr_from_dev_type_and_channels() -> None:
    dev = _device(10, 1, "Main NVR", dev_type=DEVTYPE_NVR, channel_count=16)
    result = classify_resource(dev)
    assert result["type"] == "nvr"
    assert result["confidence"] > 0.3
    assert any(s.startswith("dev_type:") for s in result["signals"])


def test_classify_camera_from_ipc() -> None:
    ipc = _device(10, 1, "Front IPC", dev_type=DEVTYPE_IPC, channel_count=1)
    result = classify_resource(ipc)
    assert result["type"] == "camera"


def test_classify_channel_is_camera() -> None:
    chan = _channel(100, 10, "Lobby Cam")
    result = classify_resource(chan)
    assert result["type"] == "camera"


def test_classify_unknown_area() -> None:
    area = _area(1, 0, "Some Area")
    result = classify_resource(area)
    assert result["type"] == "unknown"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, enabled: set[str]) -> None:
        self._enabled = enabled

    def _guarded(self, name: str) -> list:
        if name not in self._enabled:
            raise CapabilityNotAvailable(f"{name} unavailable")
        return []

    def list_resources_normalized(self) -> list:
        return self._guarded("resources")

    def list_servers(self) -> list:
        return self._guarded("servers")

    def list_alarm_zones(self) -> list:
        return self._guarded("alarms")

    def list_users(self) -> list:
        return self._guarded("users")

    def list_logs(self) -> list:
        return self._guarded("logs")

    def list_tv_walls(self) -> list:
        return self._guarded("tv_wall")


def test_detect_capabilities_reflects_enabled_set() -> None:
    client = _FakeClient({"resources", "servers", "alarms"})
    caps = detect_capabilities(client)
    assert caps == {
        "resources": True,
        "servers": True,
        "alarms": True,
        "users": False,
        "logs": False,
        "tv_wall": False,
    }


# ---------------------------------------------------------------------------
# Inventory snapshot
# ---------------------------------------------------------------------------


class _SnapshotClient:
    def __init__(self, resources, servers=None, zones=None, events=None) -> None:
        self._resources = resources
        self._servers = servers or []
        self._zones = zones or []
        self._events = events or []

    def list_resources_normalized(self):
        return list(self._resources)

    def list_servers(self):
        return list(self._servers)

    def list_alarm_zones(self):
        return list(self._zones)

    def list_alarm_events(self):
        if self._events is None:
            raise CapabilityNotAvailable("no events")
        return list(self._events)

    def list_users(self):
        raise CapabilityNotAvailable("users")

    def list_logs(self):
        raise CapabilityNotAvailable("logs")

    def list_tv_walls(self):
        raise CapabilityNotAvailable("tv_wall")


def test_inventory_snapshot_structure(small_tree: list[PlatformResource]) -> None:
    servers = [
        PlatformServer(
            guid="srv-1",
            name="Storage-1",
            kind="storage",
            server_type=14,
            server_type_name="storage",
            connect_state=8,
            connect_state_name="login_success",
            online=True,
        )
    ]
    zones = [PlatformAlarmZone(guid="z1", host_guid="dev-0000000a", name="D1", zone_type=0)]
    events = [{"device_guid": "dev-0000000a", "type": "Intrusion Detection"}]
    client = _SnapshotClient(small_tree, servers=servers, zones=zones, events=events)

    snap = get_platform_inventory_snapshot(client)

    assert set(snap.keys()) >= {
        "capabilities",
        "sites",
        "devices",
        "channels",
        "servers",
        "alarm_zones",
        "alarm_events",
        "health",
        "summary",
    }
    s = snap["summary"]
    assert s["device_count"] == 3
    assert s["channel_count"] == 3
    assert s["site_count"] == 2  # orphans not counted
    assert s["online_devices"] + s["degraded_devices"] + s["offline_devices"] == 3
    # device classification attached
    assert all("classification" in d for d in snap["devices"])
    # intrusion event mapped to device and has site_id
    intrusion = [e for e in snap["alarm_events"] if e["type"] == "intrusion"]
    assert intrusion and intrusion[0]["site_id"] != "orphans"


def test_inventory_snapshot_handles_missing_capabilities() -> None:
    class _Minimal:
        def list_resources_normalized(self):
            return []

    snap = get_platform_inventory_snapshot(_Minimal())
    assert snap["summary"]["device_count"] == 0
    assert snap["capabilities"]["servers"] is False
    assert snap["sites"] == []


def test_inventory_snapshot_fetch_status_ok_failed_unavailable() -> None:
    """#512 durable signal: per-section fetch_status distinguishes ok / failed /
    unavailable so the consumer can tell a confirmed-empty section from a fetch
    failure (the SDK-boundary half of the fix; see tvt_platform/sync.py)."""

    class _Client:
        def list_resources_normalized(self):
            return []  # genuinely empty, fetched OK

        def list_servers(self):
            raise RuntimeError("transport boom")  # -> "failed"

        def list_alarm_zones(self):
            raise CapabilityNotAvailable("alarm zones not supported")  # -> "unavailable"

        # list_alarm_events absent -> getattr default lambda returns [] -> "ok"

    snap = get_platform_inventory_snapshot(_Client())
    fs = snap["fetch_status"]

    assert fs["resources"] == "ok"
    assert fs["servers"] == "failed"
    assert fs["alarm_zones"] == "unavailable"
    assert fs["alarm_events"] == "ok"
    # devices/channels/areas derive from resources and share its status
    assert fs["devices"] == fs["channels"] == fs["areas"] == "ok"
    # a "failed" fetch must NOT masquerade as a genuine empty
    assert snap["servers"] == []
    assert fs["servers"] != "ok"


def test_inventory_snapshot_fetch_status_keys_match_consumer_contract() -> None:
    """fetch_status must carry every section uniqueos/tvt_platform/sync.py asks
    _section_fetched_ok() about: resources, devices, channels, areas, servers,
    alarm_zones (alarm_events provided too)."""

    class _Minimal:
        def list_resources_normalized(self):
            return []

    fs = get_platform_inventory_snapshot(_Minimal())["fetch_status"]
    assert {
        "resources",
        "devices",
        "channels",
        "areas",
        "servers",
        "alarm_zones",
    } <= set(fs)
    # every value is one of the three documented states
    assert set(fs.values()) <= {"ok", "failed", "unavailable"}
