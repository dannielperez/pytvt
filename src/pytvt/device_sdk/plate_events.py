"""Typed, bounded ingestion for TVT license-plate smart-event callbacks.

The native callback owns its input buffer only for the duration of the call. The
low-level client copies that buffer before this module sees it; these parsers then
validate the vendor ABI and return immutable, vendor-normalized events.
"""

from __future__ import annotations

import ctypes as ct
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from .constants import SmartEventType
from .types import (
    NET_SDK_IVE_PICTURE_INFO,
    NET_SDK_IVE_VEHICE_HEAD_INFO,
    NET_SDK_IVE_VEHICE_ITEM_INFO,
    NET_SDK_IVE_VSD_HEAD_INFO,
    NET_SDK_IVE_VSD_TARGET_INFO,
    VEHICE_PLATE_INFO,
)

DEFAULT_MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_EVENTS = 256
DEFAULT_MAX_BUFFER_BYTES = 64 * 1024 * 1024
MAX_PLATES_PER_CALLBACK = 32
MAX_VSD_TARGETS_PER_CALLBACK = 64
VSD_TARGET_CAR = 2


class PlatePayloadError(ValueError):
    """A plate callback payload is malformed, truncated, or exceeds bounds."""


class PlateSource(str, Enum):
    IPC = "ipc"
    NVR = "nvr"


class VehicleDirection(str, Enum):
    UNKNOWN = "unknown"
    APPROACHING = "approaching"
    LEAVING = "leaving"


class ImageFormat(str, Enum):
    JPEG = "jpeg"
    YUV = "yuv"
    UNKNOWN = "unknown"


class EdgePlateMatch(str, Enum):
    """Camera-local comparison result; evidence only, never authorization."""

    UNMATCHED = "unmatched"
    STRANGER = "stranger"
    ALLOWLIST = "allowlist"
    DENYLIST = "denylist"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class PlateColor(str, Enum):
    UNKNOWN = "unknown"
    BLUE = "blue"
    BLACK = "black"
    YELLOW = "yellow"
    WHITE = "white"
    GREEN = "green"
    GREEN_BLACK = "green_black"
    RED = "red"
    ORANGE = "orange"
    CYAN = "cyan"
    PURPLE = "purple"
    GRAY = "gray"


class VehicleColor(str, Enum):
    UNKNOWN = "unknown"
    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    CYAN = "cyan"
    PURPLE = "purple"
    BLACK = "black"
    WHITE = "white"
    SILVER = "silver"
    GRAY = "gray"
    GOLD = "gold"
    BROWN = "brown"


class VehicleType(str, Enum):
    UNKNOWN = "unknown"
    SEDAN = "sedan"
    SUV = "suv"
    MPV = "mpv"
    SPORTS_CAR = "sports_car"
    VAN = "van"
    BUS = "bus"
    SCHOOL_BUS = "school_bus"
    PUBLIC_BUS = "public_bus"
    LIGHT_PASSENGER = "light_passenger"
    PICKUP = "pickup"
    TRUCK = "truck"
    SPECIAL = "special"


class DropPolicy(str, Enum):
    """Backpressure policy used when the in-memory stream buffer is full."""

    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"


@dataclass(frozen=True, slots=True)
class PlateEvent:
    """Normalized license-plate observation copied from a TVT smart event."""

    user_id: int
    channel_id: int
    source: PlateSource
    received_at: datetime
    occurred_at: datetime | None
    source_event_id: str
    plate: str
    declared_plate_char_count: int | None = None
    source_encryption_version: int | None = None
    confidence: int | None = None
    char_confidences: tuple[int, ...] = ()
    direction: VehicleDirection = VehicleDirection.UNKNOWN
    plate_rect: tuple[int, int, int, int] | None = None
    plate_size: tuple[int, int] | None = None
    channel_guid: str | None = None
    edge_match: EdgePlateMatch = EdgePlateMatch.UNKNOWN
    edge_match_code: int | None = None
    plate_color: PlateColor = PlateColor.UNKNOWN
    plate_color_code: int | None = None
    plate_brightness: int | None = None
    plate_color_confidence: int | None = None
    vehicle_type: VehicleType = VehicleType.UNKNOWN
    vehicle_type_code: int | None = None
    vehicle_color: VehicleColor = VehicleColor.UNKNOWN
    vehicle_color_code: int | None = None
    vehicle_brand_code: int | None = None
    source_end_at: datetime | None = None
    full_image: bytes | None = None
    plate_image: bytes | None = None
    full_image_format: ImageFormat | None = None
    plate_image_format: ImageFormat | None = None
    full_image_size: tuple[int, int] | None = None
    is_partial: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VehicleMetadataEvent:
    """One car target decoded from TVT video-structured-data event 26."""

    user_id: int
    channel_id: int
    received_at: datetime
    relative_tick: int
    target_id: int
    target_rect: tuple[int, int, int, int]
    vehicle_color: VehicleColor
    vehicle_color_code: int
    vehicle_year: int | None
    vehicle_year_code: int
    vehicle_type: VehicleType
    vehicle_type_code: int
    vehicle_brand: str
    vehicle_model: str
    vehicle_brand_code: int
    vehicle_model_code: int
    full_image: bytes | None
    target_image: bytes | None
    full_image_format: ImageFormat | None
    target_image_format: ImageFormat | None
    full_image_size: tuple[int, int] | None
    target_image_size: tuple[int, int] | None
    is_partial: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlateSubscriptionInfo:
    """Public lifecycle metadata for one plate source/channel subscription."""

    source: PlateSource
    channel_id: int


@dataclass(frozen=True, slots=True)
class _SmartSubscriptionToken:
    """Internal vendor token; never exposed to downstream consumers."""

    command: SmartEventType
    info: PlateSubscriptionInfo
    server_address: bytes
    current_time: int
    termination_time: int


@dataclass(frozen=True, slots=True)
class PlateStreamStats:
    callbacks_received: int
    events_parsed: int
    events_dropped: int
    malformed_payloads: int
    rejected_callbacks: int
    ignored_commands: int
    buffered_events: int
    buffered_image_bytes: int
    last_error: str | None


def _read_structure(payload: bytes, structure_type: type[ct.Structure], offset: int) -> tuple[ct.Structure, int]:
    size = ct.sizeof(structure_type)
    end = offset + size
    if end > len(payload):
        raise PlatePayloadError(
            f"truncated {structure_type.__name__}: need {size} bytes at offset {offset}, have {len(payload) - offset}"
        )
    return structure_type.from_buffer_copy(payload, offset), end


def _decode_plate(raw: bytes, declared_count: int) -> str:
    if declared_count < 0 or declared_count > len(raw):
        raise PlatePayloadError(f"invalid plateCharCount {declared_count} for {len(raw)}-byte plate buffer")
    bounded = raw[:declared_count] if declared_count else raw.split(b"\0", 1)[0]
    bounded = bounded.split(b"\0", 1)[0]
    try:
        return bounded.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise PlatePayloadError("plate text is not valid UTF-8") from exc


def _take_image(
    payload: bytes,
    offset: int,
    *,
    data_length: int,
    storage_length: int,
    max_image_bytes: int,
) -> tuple[bytes, int]:
    if data_length < 0 or storage_length < data_length:
        raise PlatePayloadError(f"invalid image lengths: data={data_length}, storage={storage_length}")
    if data_length > max_image_bytes:
        raise PlatePayloadError(f"image length {data_length} exceeds limit {max_image_bytes}")
    end = offset + storage_length
    if end > len(payload):
        raise PlatePayloadError(
            f"truncated image: need {storage_length} bytes at offset {offset}, have {len(payload) - offset}"
        )
    return bytes(payload[offset : offset + data_length]), end


def _direction(value: int) -> VehicleDirection:
    if value == 2:
        return VehicleDirection.APPROACHING
    if value == 3:
        return VehicleDirection.LEAVING
    return VehicleDirection.UNKNOWN


def _picture_format(code: int | None, image: bytes | None) -> ImageFormat | None:
    """Map a smart-event picture format, trusting JPEG magic over the code.

    Recorders were observed tagging real JPEG buffers with ``iPicFormat=1``
    (documented as YUV); the bytes are authoritative for the typed format.
    """
    if code is None:
        return None
    if image is not None and image.startswith(b"\xff\xd8"):
        return ImageFormat.JPEG
    return _image_format(code)


def _has_trailing_payload_bytes(payload: bytes, offset: int) -> bool:
    """Report trailing bytes only when they are not recorder NUL padding."""
    if offset >= len(payload):
        return False
    return any(payload[offset:])


def _image_format(value: int) -> ImageFormat:
    if value == 0:
        return ImageFormat.JPEG
    if value == 1:
        return ImageFormat.YUV
    return ImageFormat.UNKNOWN


def _edge_match(value: int) -> EdgePlateMatch:
    if value == 0:
        return EdgePlateMatch.UNMATCHED
    if value == 1:
        return EdgePlateMatch.STRANGER
    if value == 2:
        return EdgePlateMatch.ALLOWLIST
    if value == 3:
        return EdgePlateMatch.DENYLIST
    if 4 <= value <= 24:
        return EdgePlateMatch.CUSTOM
    return EdgePlateMatch.UNKNOWN


def _vehicle_color(value: int) -> VehicleColor:
    colors = (
        VehicleColor.UNKNOWN,
        VehicleColor.RED,
        VehicleColor.ORANGE,
        VehicleColor.YELLOW,
        VehicleColor.GREEN,
        VehicleColor.BLUE,
        VehicleColor.CYAN,
        VehicleColor.PURPLE,
        VehicleColor.BLACK,
        VehicleColor.WHITE,
        VehicleColor.SILVER,
        VehicleColor.GRAY,
        VehicleColor.GOLD,
        VehicleColor.BROWN,
    )
    return colors[value] if 0 <= value < len(colors) else VehicleColor.UNKNOWN


def _ipc_plate_color(value: int) -> PlateColor:
    colors = {
        1: PlateColor.BLUE,
        2: PlateColor.YELLOW,
        3: PlateColor.WHITE,
        4: PlateColor.BLACK,
        5: PlateColor.GREEN,
        6: PlateColor.GREEN_BLACK,
        7: PlateColor.RED,
        8: PlateColor.ORANGE,
        9: PlateColor.CYAN,
        10: PlateColor.PURPLE,
        11: PlateColor.GRAY,
    }
    return colors.get(value, PlateColor.UNKNOWN)


def _nvr_plate_color(value: int) -> PlateColor:
    colors = {
        1: PlateColor.BLUE,
        2: PlateColor.BLACK,
        3: PlateColor.YELLOW,
        4: PlateColor.WHITE,
        5: PlateColor.GREEN,
        6: PlateColor.RED,
        7: PlateColor.GRAY,
        8: PlateColor.PURPLE,
    }
    return colors.get(value, PlateColor.UNKNOWN)


def _vehicle_type(value: int) -> VehicleType:
    types = (
        VehicleType.UNKNOWN,
        VehicleType.SEDAN,
        VehicleType.SUV,
        VehicleType.MPV,
        VehicleType.SPORTS_CAR,
        VehicleType.VAN,
        VehicleType.BUS,
        VehicleType.SCHOOL_BUS,
        VehicleType.PUBLIC_BUS,
        VehicleType.LIGHT_PASSENGER,
        VehicleType.PICKUP,
        VehicleType.TRUCK,
        VehicleType.SPECIAL,
    )
    return types[value] if 0 <= value < len(types) else VehicleType.UNKNOWN


def _vehicle_year(value: int) -> int | None:
    return value + 2007 if 1 <= value <= 11 else None


def _decode_vsd_text(raw: bytes, *, field_name: str, warnings: list[str]) -> str:
    bounded = raw.split(b"\0", 1)[0]
    try:
        return bounded.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        warnings.append(f"{field_name}_invalid_utf8")
        return ""


def _occurred_at_from_epoch_seconds(value: int) -> datetime | None:
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _percent_confidence(
    value: int,
    *,
    warnings: list[str],
    allow_basis_points: bool = False,
) -> int | None:
    """Normalize TVT's unsigned confidence field to its percentage contract.

    NVR recorder firmware was observed reporting event-29 ``plateConfidence``
    in basis points (``9900`` for a 99 % read); with ``allow_basis_points``
    values in 101-10000 fold onto the 0-100 contract. Anything else outside
    0-100 is the unsigned sentinel/garbage and stays typed as missing.
    """
    if 0 <= value <= 100:
        return value
    if allow_basis_points and 100 < value <= 10_000:
        return round(value / 100)
    warnings.append("plate_confidence_out_of_range")
    return None


def _ipc_event(
    *,
    user_id: int,
    channel_id: int,
    received_at: datetime,
    item: NET_SDK_IVE_VEHICE_ITEM_INFO,
    full_image: bytes | None,
    plate_image: bytes | None,
    full_image_format: ImageFormat | None,
    shared_warnings: list[str],
) -> PlateEvent:
    plate = _decode_plate(bytes(item.plate), int(item.plateCharCount))
    warnings = list(shared_warnings)
    if not plate:
        warnings.append("plate_text_missing")
    if full_image is None:
        warnings.append("full_image_missing")
    if plate_image is None:
        warnings.append("plate_image_missing")
    char_count = min(int(item.plateCharCount), len(item.plateCharConfid))
    return PlateEvent(
        user_id=user_id,
        channel_id=channel_id,
        source=PlateSource.IPC,
        received_at=received_at,
        occurred_at=None,
        source_event_id=str(int(item.plateId)),
        plate=plate,
        declared_plate_char_count=int(item.plateCharCount),
        confidence=_percent_confidence(int(item.plateConfidence), warnings=warnings),
        char_confidences=tuple(int(value) for value in item.plateCharConfid[:char_count]),
        direction=_direction(int(item.iVehicleDirect)),
        plate_rect=(
            int(item.ptLeftTop.X),
            int(item.ptLeftTop.Y),
            int(item.ptRightBottom.X),
            int(item.ptRightBottom.Y),
        ),
        plate_size=(int(item.plateWidth), int(item.plateHeight)),
        edge_match=_edge_match(int(item.listType)),
        edge_match_code=int(item.listType),
        plate_color=_ipc_plate_color(int(item.plateColor)),
        plate_color_code=int(item.plateColor),
        plate_brightness=int(item.plateIntensity),
        plate_color_confidence=int(item.PlateColorRate),
        vehicle_type=_vehicle_type(int(item.plateStyle)),
        vehicle_type_code=int(item.plateStyle),
        vehicle_color=_vehicle_color(int(item.vehicleColor)),
        vehicle_color_code=int(item.vehicleColor),
        full_image=full_image,
        plate_image=plate_image,
        full_image_format=full_image_format,
        plate_image_format=_image_format(int(item.data_type)),
        full_image_size=(
            (int(item.ptWidth), int(item.ptHeight)) if int(item.ptWidth) > 0 and int(item.ptHeight) > 0 else None
        ),
        is_partial=bool(warnings),
        warnings=tuple(warnings),
    )


def parse_ipc_plate_payload(
    payload: bytes,
    *,
    user_id: int,
    channel_id: int,
    received_at: datetime | None = None,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> list[PlateEvent]:
    """Parse event 20 (IPC vehicle recognition) into one event per plate."""
    if len(payload) > max_payload_bytes:
        raise PlatePayloadError(f"payload length {len(payload)} exceeds limit {max_payload_bytes}")
    received_at = received_at or datetime.now(timezone.utc)
    raw_head, offset = _read_structure(payload, NET_SDK_IVE_VEHICE_HEAD_INFO, 0)
    head = raw_head
    item_count = int(head.item_cnt)  # type: ignore[attr-defined]
    plate_count = int(head.plate_cnt)  # type: ignore[attr-defined]
    if item_count < 1 or item_count > MAX_PLATES_PER_CALLBACK + 1:
        raise PlatePayloadError(f"invalid IPC vehicle item count {item_count}")
    if plate_count < 0 or plate_count > MAX_PLATES_PER_CALLBACK:
        raise PlatePayloadError(f"invalid IPC plate count {plate_count}")

    parsed: list[tuple[NET_SDK_IVE_VEHICE_ITEM_INFO, bytes]] = []
    for _ in range(item_count):
        raw_item, offset = _read_structure(payload, NET_SDK_IVE_VEHICE_ITEM_INFO, offset)
        item = raw_item
        data_length = int(item.jpeg_len)  # type: ignore[attr-defined]
        virtual_length = int(item.jpeg_vir_len)  # type: ignore[attr-defined]
        storage_length = virtual_length or data_length
        image, offset = _take_image(
            payload,
            offset,
            data_length=data_length,
            storage_length=storage_length,
            max_image_bytes=max_image_bytes,
        )
        parsed.append((item, image))  # type: ignore[arg-type]

    warnings: list[str] = []
    if offset != len(payload):
        warnings.append("trailing_payload_bytes")
    full_items = [(item, image) for item, image in parsed if int(item.image_type) == 0]
    plate_items = [(item, image) for item, image in parsed if int(item.image_type) == 1]
    full_image = full_items[0][1] if full_items else None
    full_image_format = _image_format(int(full_items[0][0].data_type)) if full_items else None
    if len(full_items) > 1:
        warnings.append("multiple_full_images")
    if plate_count != len(plate_items):
        warnings.append("plate_count_mismatch")

    event_items: list[tuple[NET_SDK_IVE_VEHICE_ITEM_INFO, bytes | None]] = list(plate_items)
    if not event_items and full_items:
        event_items = [(full_items[0][0], None)]
    if not event_items:
        raise PlatePayloadError("IPC vehicle callback contains no plate metadata item")

    return [
        _ipc_event(
            user_id=user_id,
            channel_id=channel_id,
            received_at=received_at,
            item=item,
            full_image=full_image,
            plate_image=plate_image,
            full_image_format=full_image_format,
            shared_warnings=warnings,
        )
        for item, plate_image in event_items
    ]


def _optional_picture(
    payload: bytes,
    offset: int,
    *,
    max_image_bytes: int,
) -> tuple[bytes | None, int | None, tuple[int, int] | None, int, str | None]:
    if offset == len(payload):
        return None, None, None, offset, "picture_descriptor_missing"
    raw_info, data_offset = _read_structure(payload, NET_SDK_IVE_PICTURE_INFO, offset)
    info = raw_info
    size = int(info.iPicSize)  # type: ignore[attr-defined]
    dimensions = (int(info.iWidth), int(info.iHeight))  # type: ignore[attr-defined]
    if size < 0:
        raise PlatePayloadError(f"negative picture size {size}")
    if size == 0:
        return None, int(info.iPicFormat), dimensions, data_offset, "picture_data_missing"  # type: ignore[attr-defined]
    image, end = _take_image(
        payload,
        data_offset,
        data_length=size,
        storage_length=size,
        max_image_bytes=max_image_bytes,
    )
    return image, int(info.iPicFormat), dimensions, end, None  # type: ignore[attr-defined]


def parse_nvr_plate_payload(
    payload: bytes,
    *,
    user_id: int,
    channel_id: int,
    received_at: datetime | None = None,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> PlateEvent:
    """Parse event 29 (NVR vehicle recognition with images)."""
    if len(payload) > max_payload_bytes:
        raise PlatePayloadError(f"payload length {len(payload)} exceeds limit {max_payload_bytes}")
    received_at = received_at or datetime.now(timezone.utc)
    raw_info, offset = _read_structure(payload, VEHICE_PLATE_INFO, 0)
    info = raw_info
    full_image, full_format, full_size, offset, full_warning = _optional_picture(
        payload,
        offset,
        max_image_bytes=max_image_bytes,
    )
    plate_image, plate_format, plate_size, offset, plate_warning = _optional_picture(
        payload,
        offset,
        max_image_bytes=max_image_bytes,
    )
    warnings = [warning for warning in (full_warning, plate_warning) if warning]
    if _has_trailing_payload_bytes(payload, offset):
        warnings.append("trailing_payload_bytes")
    plate = _decode_plate(bytes(info.plate), int(info.plateCharCount))  # type: ignore[attr-defined]
    if not plate:
        warnings.append("plate_text_missing")
    return PlateEvent(
        user_id=user_id,
        channel_id=channel_id,
        source=PlateSource.NVR,
        received_at=received_at,
        occurred_at=_occurred_at_from_epoch_seconds(int(info.dwStartTime)),  # type: ignore[attr-defined]
        source_event_id=str(int(info.dwPlateID)),  # type: ignore[attr-defined]
        plate=plate,
        declared_plate_char_count=int(info.plateCharCount),  # type: ignore[attr-defined]
        source_encryption_version=int(info.dwEncryptVer),  # type: ignore[attr-defined]
        confidence=_percent_confidence(
            int(info.plateConfidence),  # type: ignore[attr-defined]
            warnings=warnings,
            allow_basis_points=True,
        ),
        plate_rect=(
            int(info.Rect16.left),  # type: ignore[attr-defined]
            int(info.Rect16.top),  # type: ignore[attr-defined]
            int(info.Rect16.right),  # type: ignore[attr-defined]
            int(info.Rect16.bottom),  # type: ignore[attr-defined]
        ),
        channel_guid=info.chlId.as_string,  # type: ignore[attr-defined]
        edge_match=_edge_match(int(info.listType)),  # type: ignore[attr-defined]
        edge_match_code=int(info.listType),  # type: ignore[attr-defined]
        plate_color=_nvr_plate_color(int(info.plateColor)),  # type: ignore[attr-defined]
        plate_color_code=int(info.plateColor),  # type: ignore[attr-defined]
        plate_brightness=int(info.plateIntensity),  # type: ignore[attr-defined]
        plate_color_confidence=int(info.PlateColorRate),  # type: ignore[attr-defined]
        vehicle_type=_vehicle_type(int(info.plateStyle)),  # type: ignore[attr-defined]
        vehicle_type_code=int(info.plateStyle),  # type: ignore[attr-defined]
        vehicle_color=_vehicle_color(int(info.vehicleColor)),  # type: ignore[attr-defined]
        vehicle_color_code=int(info.vehicleColor),  # type: ignore[attr-defined]
        vehicle_brand_code=int(info.dwBrand),  # type: ignore[attr-defined]
        source_end_at=_occurred_at_from_epoch_seconds(int(info.dwEndTime)),  # type: ignore[attr-defined]
        full_image=full_image,
        plate_image=plate_image,
        full_image_format=_picture_format(full_format, full_image),
        plate_image_format=_picture_format(plate_format, plate_image),
        full_image_size=full_size,
        plate_size=plate_size,
        is_partial=bool(warnings),
        warnings=tuple(warnings),
    )


def parse_vsd_vehicle_payload(
    payload: bytes,
    *,
    user_id: int,
    channel_id: int,
    received_at: datetime | None = None,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> list[VehicleMetadataEvent]:
    """Parse car targets from event 26 (video structured metadata).

    Person and bike targets are consumed and skipped. This function is kept
    separate from ``PlateEventStream`` because VSD records do not contain a
    plate identity and must be correlated by application policy.
    """
    if len(payload) > max_payload_bytes:
        raise PlatePayloadError(f"payload length {len(payload)} exceeds limit {max_payload_bytes}")
    received_at = received_at or datetime.now(timezone.utc)
    raw_head, offset = _read_structure(payload, NET_SDK_IVE_VSD_HEAD_INFO, 0)
    head = raw_head
    target_count = int(head.dwTargetCount)  # type: ignore[attr-defined]
    if target_count < 0 or target_count > MAX_VSD_TARGETS_PER_CALLBACK:
        raise PlatePayloadError(f"invalid VSD target count {target_count}")

    full_image, full_format, full_size, offset, full_warning = _optional_picture(
        payload,
        offset,
        max_image_bytes=max_image_bytes,
    )
    events: list[VehicleMetadataEvent] = []
    for _ in range(target_count):
        raw_target, offset = _read_structure(payload, NET_SDK_IVE_VSD_TARGET_INFO, offset)
        target = raw_target
        data_length = int(target.dwDataLen)  # type: ignore[attr-defined]
        target_image, offset = _take_image(
            payload,
            offset,
            data_length=data_length,
            storage_length=data_length,
            max_image_bytes=max_image_bytes,
        )
        if int(target.dwTargetType) != VSD_TARGET_CAR:  # type: ignore[attr-defined]
            continue

        warnings = [full_warning] if full_warning else []
        if not target_image:
            warnings.append("target_image_missing")
        car = target.attributes.car  # type: ignore[attr-defined]
        brand = _decode_vsd_text(bytes(car.szBrand), field_name="vehicle_brand", warnings=warnings)
        model = _decode_vsd_text(bytes(car.szModel), field_name="vehicle_model", warnings=warnings)
        color_code = int(car.byColor)
        year_code = int(car.byYear)
        type_code = int(car.byType)
        events.append(
            VehicleMetadataEvent(
                user_id=user_id,
                channel_id=int(head.dwChannel) if int(head.dwChannel) >= 0 else channel_id,  # type: ignore[attr-defined]
                received_at=received_at,
                relative_tick=int(head.dwRelativeTick),  # type: ignore[attr-defined]
                target_id=int(target.dwTargetId),  # type: ignore[attr-defined]
                target_rect=(
                    int(target.rect.X1),  # type: ignore[attr-defined]
                    int(target.rect.Y1),  # type: ignore[attr-defined]
                    int(target.rect.X2),  # type: ignore[attr-defined]
                    int(target.rect.Y2),  # type: ignore[attr-defined]
                ),
                vehicle_color=_vehicle_color(color_code),
                vehicle_color_code=color_code,
                vehicle_year=_vehicle_year(year_code),
                vehicle_year_code=year_code,
                vehicle_type=_vehicle_type(type_code),
                vehicle_type_code=type_code,
                vehicle_brand=brand,
                vehicle_model=model,
                vehicle_brand_code=int(car.dwBrandType),
                vehicle_model_code=int(car.dwModelType),
                full_image=full_image,
                target_image=target_image or None,
                full_image_format=_image_format(full_format) if full_format is not None else None,
                target_image_format=_image_format(int(target.iPicFormat)),  # type: ignore[attr-defined]
                full_image_size=full_size,
                target_image_size=(int(target.iWidth), int(target.iHeight)),  # type: ignore[attr-defined]
                is_partial=bool(warnings),
                warnings=tuple(warnings),
            )
        )
    if offset != len(payload):
        raise PlatePayloadError(f"trailing VSD payload bytes: {len(payload) - offset}")
    return events


class PlateEventStream:
    """Thread-safe bounded queue fed directly by the native SDK callback."""

    def __init__(
        self,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
        max_buffer_bytes: int = DEFAULT_MAX_BUFFER_BYTES,
        drop_policy: DropPolicy = DropPolicy.DROP_OLDEST,
    ) -> None:
        if max_events < 1 or max_events > 10_000:
            raise ValueError("max_events must be between 1 and 10000")
        if max_payload_bytes < 1 or max_payload_bytes > 64 * 1024 * 1024:
            raise ValueError("max_payload_bytes must be between 1 byte and 64 MiB")
        if max_image_bytes < 1 or max_image_bytes > max_payload_bytes:
            raise ValueError("max_image_bytes must be positive and no larger than max_payload_bytes")
        if max_buffer_bytes < 1 or max_buffer_bytes > 1024 * 1024 * 1024:
            raise ValueError("max_buffer_bytes must be between 1 byte and 1 GiB")
        self.max_payload_bytes = max_payload_bytes
        self._max_image_bytes = max_image_bytes
        self._max_buffer_bytes = max_buffer_bytes
        self._drop_policy = drop_policy
        self._queue: queue.Queue[PlateEvent | None] = queue.Queue(maxsize=max_events)
        self._lock = threading.Lock()
        self._callbacks_received = 0
        self._events_parsed = 0
        self._events_dropped = 0
        self._malformed_payloads = 0
        self._rejected_callbacks = 0
        self._ignored_commands = 0
        self._last_error: str | None = None
        self._buffered_image_bytes = 0
        self._closed = False
        self._closing = False
        self._close_signal_queued = False
        self._closer: Callable[[], None] | None = None
        self._subscriptions: tuple[_SmartSubscriptionToken, ...] = ()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def subscriptions(self) -> tuple[PlateSubscriptionInfo, ...]:
        with self._lock:
            return tuple(token.info for token in self._subscriptions)

    def _configure(
        self,
        *,
        subscriptions: tuple[_SmartSubscriptionToken, ...],
        closer: Callable[[], None],
    ) -> None:
        with self._lock:
            self._subscriptions = subscriptions
            self._closer = closer

    def reject_callback(self, reason: str) -> None:
        with self._lock:
            self._rejected_callbacks += 1
            self._last_error = reason

    def ingest(self, user_id: int, channel_id: int, command: int, payload: bytes) -> None:
        """Parse and enqueue one copied native callback without raising across the ABI."""
        with self._lock:
            if self._closed or self._closing:
                return
            self._callbacks_received += 1
        try:
            if command == int(SmartEventType.VEHICLE):
                events = parse_ipc_plate_payload(
                    payload,
                    user_id=user_id,
                    channel_id=channel_id,
                    max_payload_bytes=self.max_payload_bytes,
                    max_image_bytes=self._max_image_bytes,
                )
            elif command == int(SmartEventType.NVR_VEHICLE):
                events = [
                    parse_nvr_plate_payload(
                        payload,
                        user_id=user_id,
                        channel_id=channel_id,
                        max_payload_bytes=self.max_payload_bytes,
                        max_image_bytes=self._max_image_bytes,
                    )
                ]
            else:
                with self._lock:
                    self._ignored_commands += 1
                return
        except PlatePayloadError as exc:
            with self._lock:
                self._malformed_payloads += 1
                self._last_error = str(exc)
            return

        for event in events:
            self._enqueue(event)
        with self._lock:
            self._events_parsed += len(events)

    def _enqueue(self, event: PlateEvent) -> None:
        event_bytes = len(event.full_image or b"") + len(event.plate_image or b"")
        with self._lock:
            if self._closed or self._closing:
                self._events_dropped += 1
                return
            if event_bytes > self._max_buffer_bytes:
                self._events_dropped += 1
                self._last_error = (
                    f"event image bytes {event_bytes} exceed stream buffer limit {self._max_buffer_bytes}"
                )
                return
            over_limit = self._queue.full() or self._buffered_image_bytes + event_bytes > self._max_buffer_bytes
            if over_limit and self._drop_policy is DropPolicy.DROP_NEWEST:
                self._events_dropped += 1
                return
            while self._queue.full() or self._buffered_image_bytes + event_bytes > self._max_buffer_bytes:
                try:
                    oldest = self._queue.get_nowait()
                except queue.Empty:
                    break
                if oldest is None:
                    self._queue.put_nowait(None)
                    self._close_signal_queued = True
                    return
                self._buffered_image_bytes -= len(oldest.full_image or b"") + len(oldest.plate_image or b"")
                self._events_dropped += 1
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                self._events_dropped += 1
                return
            self._buffered_image_bytes += event_bytes

    def _record_dequeue(self, event: PlateEvent) -> None:
        event_bytes = len(event.full_image or b"") + len(event.plate_image or b"")
        with self._lock:
            self._buffered_image_bytes = max(0, self._buffered_image_bytes - event_bytes)
            if self._closed and not self._close_signal_queued and self._queue.empty():
                self._queue.put_nowait(None)
                self._close_signal_queued = True

    def get(self, timeout: float | None = None) -> PlateEvent:
        """Return the next event.

        Raises ``queue.Empty`` on timeout and ``RuntimeError`` when a close
        wakes a blocked reader.
        """
        with self._lock:
            if self._closed and self._queue.empty():
                raise RuntimeError("plate-event stream is closed")
        event = self._queue.get(timeout=timeout)
        if event is None:
            self._requeue_close_signal()
            raise RuntimeError("plate-event stream is closed")
        self._record_dequeue(event)
        return event

    def get_nowait(self) -> PlateEvent:
        event = self._queue.get_nowait()
        if event is None:
            self._requeue_close_signal()
            raise queue.Empty
        self._record_dequeue(event)
        return event

    def _requeue_close_signal(self) -> None:
        with self._lock:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                self._close_signal_queued = False
            else:
                self._close_signal_queued = True

    def drain(self, *, limit: int = 100) -> list[PlateEvent]:
        if limit < 1:
            return []
        events: list[PlateEvent] = []
        for _ in range(limit):
            try:
                events.append(self.get_nowait())
            except queue.Empty:
                break
        return events

    def stats(self) -> PlateStreamStats:
        with self._lock:
            buffered_events = self._queue.qsize() - int(self._close_signal_queued)
            return PlateStreamStats(
                callbacks_received=self._callbacks_received,
                events_parsed=self._events_parsed,
                events_dropped=self._events_dropped,
                malformed_payloads=self._malformed_payloads,
                rejected_callbacks=self._rejected_callbacks,
                ignored_commands=self._ignored_commands,
                buffered_events=max(0, buffered_events),
                buffered_image_bytes=self._buffered_image_bytes,
                last_error=self._last_error,
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._closing:
                raise RuntimeError("plate-event stream close is already in progress")
            self._closing = True
            closer = self._closer
        try:
            if closer is not None:
                closer()
        except Exception:
            with self._lock:
                self._closing = False
            raise
        with self._lock:
            self._closing = False
            self._closed = True
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                return
            self._close_signal_queued = True

    def __enter__(self) -> PlateEventStream:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
