"""Bounded IPC HTTPPOSTv2 VEHICLE decoder; authentication belongs to the receiver.

Uses the captured IPC 1.7 listInfo shape. Does not accept NVR v2 messages,
perform network I/O, authorize vehicles, or substitute receipt time for capture
time. Camera-local list membership is deliberately not authorization.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import xml.etree.ElementTree as ET
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from .device_sdk.plate_events import ImageFormat, PlateEvent, PlatePayloadError, PlateSource, VehicleDirection

MAX_PUSH_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_ITEMS = 16


def _one(parent: ET.Element, path: str) -> ET.Element | None:
    matches = parent.findall(path)
    if len(matches) > 1:
        raise PlatePayloadError("ambiguous vehicle push field")
    return matches[0] if matches else None


def _text(parent: ET.Element, path: str) -> str:
    node = _one(parent, path)
    return (node.text or "").strip() if node is not None else ""


def _jpeg(item: ET.Element) -> bytes | None:
    text = _text(item, "targetImageData/targetBase64Data")
    if not text:
        return None
    encoded = "".join(text.split())
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise PlatePayloadError("vehicle push image exceeds limit")
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PlatePayloadError("invalid vehicle push base64") from exc
    end = image.rfind(b"\xff\xd9")
    if len(image) > MAX_IMAGE_BYTES or not image.startswith(b"\xff\xd8") or end < 2:
        raise PlatePayloadError("invalid vehicle push JPEG framing")
    # Installed IPC firmware can append transport-buffer bytes after JPEG EOI.
    # Discard that trailer rather than rejecting an otherwise valid crop. This
    # remains a framing check: consumers must still validate the complete JPEG.
    return image[: end + 2]


def parse_ipc_vehicle_push(body: bytes, *, received_at: datetime, channel_id: int) -> tuple[PlateEvent, ...]:
    """Decode camera evidence only; callers must verify source identity and freshness.

    currentTime is Unix microseconds in this IPC dialect. Unknown/missing
    direction stays UNKNOWN, and missing/invalid capture time stays None.
    Image decoding checks base64/JPEG framing, not complete image integrity.
    Consumers must use their normal image validation/retention pipeline.
    """
    if received_at.tzinfo is None or isinstance(channel_id, bool) or not isinstance(channel_id, int) or channel_id < 0:
        raise PlatePayloadError("invalid receive context")
    if not body or len(body) > MAX_PUSH_BYTES:
        raise PlatePayloadError("vehicle push size outside limits")
    try:
        text = body.decode("utf-8")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise PlatePayloadError("vehicle push declarations prohibited")
        root = ET.fromstring(text)
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise PlatePayloadError("invalid vehicle push XML") from exc
    nodes = list(root.iter())
    if len(nodes) > 4096:
        raise PlatePayloadError("vehicle push XML element limit exceeded")
    for node in nodes:
        if node.tag.startswith("{") and not node.tag.startswith("{http://www.ipc.com/ver10}"):
            raise PlatePayloadError("unsupported vehicle push namespace")
        node.tag = node.tag.split("}")[-1]
    if root.tag != "config" or root.get("version") != "1.7":
        raise PlatePayloadError("unsupported vehicle push dialect")
    status = _one(root, "alarmStatusInfo")
    if (status is not None and {node.tag for node in root} <= {"alarmStatusInfo", "dataTime", "deviceInfo"}
            and len(status) == 1 and _text(status, "vehiceAlarm") in {"true", "false"}):
        # The same VEHICLE subscription emits status transitions, not plate
        # detections. Acknowledge only the observed status shape, never invent
        # an arrival from it or silently swallow a mixed plate/status message.
        return ()
    if _text(root, "smartType") not in {"VEHICLE", "VEHICE"}:
        raise PlatePayloadError("unsupported vehicle push dialect")
    container = _one(root, "listInfo")
    if container is None:
        raise PlatePayloadError("missing vehicle push items")
    items = container.findall("item")
    if not 1 <= len(items) <= MAX_ITEMS:
        raise PlatePayloadError("vehicle push item count outside limits")
    occurred_at = None
    stamp = _text(root, "currentTime")
    if stamp.isascii() and stamp.isdigit() and len(stamp) == 16:
        with suppress(OverflowError, ValueError):
            occurred_at = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(stamp))
    overview = None
    for item in items:
        if not _text(item, "plateNumber") and _text(item, "image") == "0":
            image = _jpeg(item)
            if overview is not None and image is not None:
                raise PlatePayloadError("ambiguous vehicle push overview")
            overview = image
    result = []
    digest = hashlib.sha256(body).hexdigest()
    for index, item in enumerate(items):
        plate = _text(item, "plateNumber")
        if not plate:
            continue
        if len(plate) > 32:
            raise PlatePayloadError("vehicle push plate exceeds limit")
        raw_confidence = _one(item, "PlateConfidence")
        confidence = None
        if raw_confidence is not None:
            count = raw_confidence.get("count", "")
            if count.isascii() and count.isdigit() and len(count) <= 5 and 0 <= int(count) <= 10000:
                confidence = int(count) // 100
        direction = {"approach": VehicleDirection.APPROACHING, "away": VehicleDirection.LEAVING}.get(
            _text(item, "vehicleDirect"), VehicleDirection.UNKNOWN
        )
        image = _jpeg(item)
        warnings = tuple(
            name
            for name, missing in [
                ("capture_time_unverified", occurred_at is None),
                ("direction_unverified", direction is VehicleDirection.UNKNOWN),
                ("confidence_unverified", confidence is None),
            ]
            if missing
        )
        result.append(
            PlateEvent(
                user_id=0,
                channel_id=channel_id,
                source=PlateSource.IPC,
                received_at=received_at,
                occurred_at=occurred_at,
                source_event_id=f"http:{digest}:{index}",
                plate=plate,
                confidence=confidence,
                direction=direction,
                full_image=overview,
                plate_image=image,
                full_image_format=ImageFormat.JPEG if overview else None,
                plate_image_format=ImageFormat.JPEG if image else None,
                warnings=warnings,
            )
        )
    return tuple(result)
