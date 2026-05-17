"""Heuristic classification for PlatformSDK resources.

Answers "is this a camera, an NVR, a server, or something else?" using a
combination of SDK-provided type codes and name-pattern signals.  Pure
data in, dict out — no SDK calls.
"""

from __future__ import annotations

import re
from typing import Any

from .platform_sdk.platform_constants import (
    DEVTYPE_DAHUA,
    DEVTYPE_DVR,
    DEVTYPE_HIK,
    DEVTYPE_IPC,
    DEVTYPE_MDVR,
    DEVTYPE_NVR,
    DEVTYPE_ONVIF,
    NODETYPE_AREA,
    NODETYPE_CHANNEL,
    NODETYPE_DEVICE,
    NODETYPE_SENSOR,
)
from .platform_sdk.platform_models import PlatformResource

__all__ = ["classify_resource"]


_NVR_NAME_PATTERN = re.compile(r"\b(nvr|dvr|recorder)\b", re.IGNORECASE)
_IPC_NAME_PATTERN = re.compile(r"\b(ipc|cam(?:era)?)\b", re.IGNORECASE)

_CAMERA_DEV_TYPES = {DEVTYPE_IPC, DEVTYPE_HIK, DEVTYPE_DAHUA, DEVTYPE_ONVIF}
_RECORDER_DEV_TYPES = {DEVTYPE_NVR, DEVTYPE_DVR, DEVTYPE_MDVR}


def classify_resource(resource: PlatformResource) -> dict[str, Any]:
    """Return a classification dict for a :class:`PlatformResource`.

    Output shape::

        {"type": "camera"|"nvr"|"server"|"unknown",
         "confidence": 0.0..1.0,
         "signals": ["dev_type:ipc", "name:matches_ipc", ...]}
    """

    signals: list[str] = []
    type_scores: dict[str, float] = {
        "camera": 0.0,
        "nvr": 0.0,
        "server": 0.0,
        "unknown": 0.0,
    }

    node_type = resource.node_type
    dev_type = resource.device_type
    name = resource.name or ""

    # Node-type signal (strongest for areas/channels/sensors).
    if node_type == NODETYPE_CHANNEL:
        type_scores["camera"] += 0.6
        signals.append("node_type:channel")
    elif node_type == NODETYPE_SENSOR:
        type_scores["unknown"] += 0.5
        signals.append("node_type:sensor")
    elif node_type == NODETYPE_AREA:
        type_scores["unknown"] += 0.4
        signals.append("node_type:area")
    elif node_type == NODETYPE_DEVICE:
        signals.append("node_type:device")

    # Device-type signal.
    if dev_type in _RECORDER_DEV_TYPES:
        type_scores["nvr"] += 0.8
        signals.append(f"dev_type:{resource.device_type_name}")
    elif dev_type in _CAMERA_DEV_TYPES:
        type_scores["camera"] += 0.7
        signals.append(f"dev_type:{resource.device_type_name}")

    # Channel-count signal: many children + device node => recorder.
    if node_type == NODETYPE_DEVICE:
        if resource.channel_count >= 2:
            type_scores["nvr"] += 0.5
            signals.append(f"channel_count:{resource.channel_count}")
        elif resource.channel_count == 1:
            type_scores["camera"] += 0.3
            signals.append("channel_count:1")
        elif resource.channel_count == 0:
            type_scores["camera"] += 0.2
            signals.append("channel_count:0")

    # Name-pattern signal.
    if _NVR_NAME_PATTERN.search(name):
        type_scores["nvr"] += 0.4
        signals.append("name:matches_nvr")
    if _IPC_NAME_PATTERN.search(name):
        type_scores["camera"] += 0.3
        signals.append("name:matches_ipc")

    # PlatformResource can never be a PlatformServer — server classification
    # is handled via the server list directly.  Kept in the type set for
    # API stability.

    if all(v == 0.0 for v in type_scores.values()):
        return {"type": "unknown", "confidence": 0.0, "signals": signals}

    best_type, best_score = max(type_scores.items(), key=lambda kv: kv[1])
    total = sum(type_scores.values()) or 1.0
    confidence = round(best_score / total, 3)
    if best_score == 0.0:
        best_type = "unknown"
        confidence = 0.0

    return {
        "type": best_type,
        "confidence": confidence,
        "signals": signals,
    }
