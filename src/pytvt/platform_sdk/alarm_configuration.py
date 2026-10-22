"""Read-only NVMS alarm linkage projection; not an event log or lossless backup.

The shipped AlarmMgr perimeter/tripwire pages query this shape. Audio is a
scalar, so duplicate stored audio rows and unexposed SOP actions cannot be
recovered from it. Never use this projection as a write/restore payload.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

ALARM_CONFIGURATION_TYPES = frozenset({"motion", "perimeter", "tripwire"})
ACTIONS = {"triggerSchedule", "buzzerSwitch", "tvWall", "preset", "popVideo", "snapshot", "sysRec", "alarmOut"}
TAGS = ACTIONS | {
    "switch",
    "schedule",
    "item",
    "chl",
    "channel",
    "id",
    "name",
    "index",
    "list",
    "tvWallId",
    "tvWallName",
}
ATTRS = {"id", "name", "index"}
LABELS = {
    "triggerSchedule": "Schedule",
    "buzzerSwitch": "Audio",
    "tvWall": "TV wall",
    "preset": "PTZ preset",
    "popVideo": "Alarm preview",
    "snapshot": "Snapshot",
    "sysRec": "Recording",
    "alarmOut": "Alarm output",
    "switch": "Enabled",
    "schedule": "Schedule",
    "item": "Destination",
    "chl": "Channel",
    "channel": "Channel",
    "id": "Identifier",
    "name": "Name",
    "index": "Index",
    "list": "Destinations",
    "tvWallId": "TV wall identifier",
    "tvWallName": "TV wall name",
}


def _projection(node: ET.Element, unknown: set[str], budget: list[int], depth: int = 0) -> dict:
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("alarm configuration projection exceeds node limit")
    if depth > 8:
        raise ValueError("alarm configuration nesting exceeds limit")
    children = []
    for child in node:
        if child.tag not in TAGS:
            unknown.add(child.tag[:80])
            continue
        children.append(_projection(child, unknown, budget, depth + 1))
    return {
        "field": node.tag,
        "label": LABELS[node.tag],
        "value": (node.text or "").strip()[:512],
        "attributes": {k: v[:512] for k, v in node.attrib.items() if k in ATTRS},
        "children": children,
    }


def configuration_items(content: ET.Element | None) -> list[dict]:
    if content is None:
        return []
    rows = []
    budget = [4096]
    for item in content.findall("item"):
        if len(rows) >= 25:
            raise ValueError("alarm configuration page exceeds limit")
        trigger = item.find("trigger")
        if trigger is None:
            continue
        unknown = set()
        actions = []
        for node in trigger:
            if node.tag not in ACTIONS:
                unknown.add(node.tag[:80])
                continue
            actions.append(_projection(node, unknown, budget))
        rows.append(
            {
                "channel_id": item.get("id", "")[:128],
                "name": (item.findtext("name") or item.get("name", ""))[:512],
                "actions": actions,
                "unexposed_fields": sorted(unknown),
            }
        )
    return rows
