"""Alarm event normalization for PlatformSDK data.

Raw alarm data coming from the management server takes several shapes
(alarm-zone ``stPlat_AlarmZoneListMsg`` rows, alarm-log entries when
available, event callbacks).  This module maps all of them into a single
neutral :class:`PlatformAlarmEvent` shape that downstream consumers
(UniqueOS adapter, CLI) can treat uniformly.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

__all__ = ["PlatformAlarmEvent", "normalize_alarm_events"]


AlarmType = Literal[
    "intrusion",
    "line_crossing",
    "offline",
    "motion",
    "unknown",
]

AlarmSeverity = Literal["low", "medium", "high"]


_TYPE_MAP: dict[str, AlarmType] = {
    "intrusion detection": "intrusion",
    "intrusion": "intrusion",
    "line crossing detection": "line_crossing",
    "line crossing": "line_crossing",
    "line_crossing": "line_crossing",
    "offline": "offline",
    "device offline": "offline",
    "disconnected": "offline",
    "motion": "motion",
    "motion detection": "motion",
}

_SEVERITY_BY_TYPE: dict[AlarmType, AlarmSeverity] = {
    "offline": "high",
    "intrusion": "high",
    "line_crossing": "medium",
    "motion": "low",
    "unknown": "low",
}


@dataclass(frozen=True)
class PlatformAlarmEvent:
    device_guid: str
    site_id: str
    type: AlarmType
    raw_type: str
    timestamp: datetime | None
    severity: AlarmSeverity

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_guid": self.device_guid,
            "site_id": self.site_id,
            "type": self.type,
            "raw_type": self.raw_type,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "severity": self.severity,
        }


def _classify(raw_type: str) -> AlarmType:
    key = (raw_type or "").strip().lower()
    if not key:
        return "unknown"
    if key in _TYPE_MAP:
        return _TYPE_MAP[key]
    # fall-through: prefix/substring heuristics
    for pattern, value in _TYPE_MAP.items():
        if pattern in key:
            return value
    return "unknown"


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # seconds-since-epoch; reject nonsensical values
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(text, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _extract(row: Any, *keys: str) -> Any:
    if hasattr(row, "_asdict"):
        row = row._asdict()
    if isinstance(row, dict):
        for k in keys:
            if k in row and row[k] not in (None, ""):
                return row[k]
        return None
    for k in keys:
        if hasattr(row, k):
            value = getattr(row, k)
            if value not in (None, ""):
                return value
    return None


def normalize_alarm_events(
    raw_logs_or_zones: Iterable[Any],
    site_lookup: dict[str, str] | None = None,
) -> list[PlatformAlarmEvent]:
    """Map ``raw_logs_or_zones`` into :class:`PlatformAlarmEvent` rows.

    Each input item may be a dict, a dataclass, or any object exposing the
    expected attributes (``device_guid``/``host_guid``, ``type`` or
    ``alarm_type``, ``timestamp`` / ``time`` etc.).

    ``site_lookup`` is an optional mapping of device_guid (lowercased) to
    site_id used to fill in the ``site_id`` field; when absent the event's
    ``site_id`` attribute (if any) is used, otherwise ``"orphans"``.
    """

    site_lookup = {k.lower(): v for k, v in (site_lookup or {}).items()}
    events: list[PlatformAlarmEvent] = []

    for row in raw_logs_or_zones:
        device_guid = str(_extract(row, "device_guid", "deviceGuid", "host_guid", "guidNodeID", "guid") or "")
        raw_type = str(_extract(row, "raw_type", "type", "alarm_type", "alarmType", "name") or "")
        timestamp = _parse_ts(_extract(row, "timestamp", "time", "ts", "occurred_at"))
        explicit_site = _extract(row, "site_id", "siteId")
        site_id = str(explicit_site) if explicit_site else site_lookup.get(device_guid.lower(), "orphans")

        alarm_type = _classify(raw_type)
        severity: AlarmSeverity = _SEVERITY_BY_TYPE.get(alarm_type, "low")

        events.append(
            PlatformAlarmEvent(
                device_guid=device_guid,
                site_id=site_id,
                type=alarm_type,
                raw_type=raw_type,
                timestamp=timestamp,
                severity=severity,
            )
        )

    return events
