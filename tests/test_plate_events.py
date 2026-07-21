"""Fixture-first tests for typed TVT license-plate callback ingestion."""

from __future__ import annotations

import ctypes as ct
import queue
from datetime import datetime, timezone

import pytest

from pytvt.device_sdk.constants import SmartEventType
from pytvt.device_sdk.plate_events import (
    DropPolicy,
    EdgePlateMatch,
    ImageFormat,
    PlateEventStream,
    PlatePayloadError,
    PlateSource,
    VehicleDirection,
    parse_ipc_plate_payload,
    parse_nvr_plate_payload,
)
from pytvt.device_sdk.types import (
    NET_DVR_SUBSCRIBE_REPLY,
    NET_SDK_IVE_PICTURE_INFO,
    NET_SDK_IVE_VEHICE_HEAD_INFO,
    NET_SDK_IVE_VEHICE_ITEM_INFO,
    VEHICE_PLATE_INFO,
)


def _bytes_of(value: ct.Structure) -> bytes:
    return ct.string_at(ct.byref(value), ct.sizeof(value))


def _ipc_payload(*, plate: bytes = b"ABC123", plate_image: bytes = b"plate-jpeg") -> bytes:
    full_image = b"full-jpeg"
    head = NET_SDK_IVE_VEHICE_HEAD_INFO()
    head.begin_flag = 0x5A5A5A5A
    head.item_cnt = 2
    head.plate_cnt = 1
    head.absoluteTime = 1_721_234_567_890
    head.end_flag = 0xA5A5A5A5

    full = NET_SDK_IVE_VEHICE_ITEM_INFO()
    full.image_type = 0
    full.data_type = 0
    full.jpeg_len = len(full_image)
    full.jpeg_vir_len = 12

    crop = NET_SDK_IVE_VEHICE_ITEM_INFO()
    crop.image_type = 1
    crop.data_type = 0
    crop.plateId = 44
    crop.plateCharCount = len(plate)
    crop.plate = plate
    crop.plateCharConfid[0 : len(plate)] = [97, 96, 95, 94, 93, 92][: len(plate)]
    crop.ptLeftTop.X = 100
    crop.ptLeftTop.Y = 200
    crop.ptRightBottom.X = 300
    crop.ptRightBottom.Y = 260
    crop.plateWidth = 200
    crop.plateHeight = 60
    crop.plateConfidence = 98
    crop.plateColor = 3
    crop.plateStyle = 1
    crop.vehicleColor = 4
    crop.iVehicleDirect = 2
    crop.listType = 3
    crop.jpeg_len = len(plate_image)
    crop.jpeg_vir_len = len(plate_image)

    return b"".join(
        (
            _bytes_of(head),
            _bytes_of(full),
            full_image,
            b"\0" * (12 - len(full_image)),
            _bytes_of(crop),
            plate_image,
        )
    )


def _picture(image: bytes, *, width: int, height: int) -> bytes:
    info = NET_SDK_IVE_PICTURE_INFO()
    info.iWidth = width
    info.iHeight = height
    info.iPicFormat = 0
    info.iPicSize = len(image)
    return _bytes_of(info) + image


def _nvr_payload(*, full_image: bytes = b"full", plate_image: bytes = b"crop") -> bytes:
    info = VEHICE_PLATE_INFO()
    info.dwPlateID = 501
    info.plateCharCount = 6
    info.plate = b"XYZ789"
    info.Rect16.left = 10
    info.Rect16.top = 20
    info.Rect16.right = 110
    info.Rect16.bottom = 60
    info.plateConfidence = 96
    info.plateColor = 2
    info.plateStyle = 1
    info.vehicleColor = 5
    info.dwBrand = 88
    info.listType = 2
    info.dwStartTime = 1_721_234_567
    info.chlId.Data1 = 7
    info.chlId.Data2 = 0x1234
    info.chlId.Data3 = 0x5678
    info.chlId.Data4[:] = bytes.fromhex("90ABCDEF12345678")
    return _bytes_of(info) + _picture(full_image, width=2688, height=1520) + _picture(plate_image, width=320, height=96)


def test_plate_callback_struct_sizes_match_vendor_pack4_abi():
    assert ct.sizeof(NET_SDK_IVE_VEHICE_HEAD_INFO) == 48
    assert ct.sizeof(NET_SDK_IVE_VEHICE_ITEM_INFO) == 736
    assert ct.sizeof(VEHICE_PLATE_INFO) == 160
    assert ct.sizeof(NET_SDK_IVE_PICTURE_INFO) == 16
    assert ct.sizeof(NET_DVR_SUBSCRIBE_REPLY) == 336


def test_normalized_plate_surface_is_exported_from_device_sdk():
    import pytvt.device_sdk as package

    for name in (
        "PlateEvent",
        "PlateEventStream",
        "PlateSubscriptionInfo",
        "PlateStreamStats",
        "PlateSource",
        "VehicleDirection",
        "EdgePlateMatch",
        "ImageFormat",
        "parse_ipc_plate_payload",
        "parse_nvr_plate_payload",
    ):
        assert name in package.__all__
        assert hasattr(package, name)


def test_parse_ipc_plate_payload_copies_metadata_and_images():
    received_at = datetime(2026, 7, 21, tzinfo=timezone.utc)
    events = parse_ipc_plate_payload(
        _ipc_payload(),
        user_id=9,
        channel_id=2,
        received_at=received_at,
    )

    assert len(events) == 1
    event = events[0]
    assert event.source is PlateSource.IPC
    assert event.user_id == 9
    assert event.channel_id == 2
    assert event.received_at == received_at
    assert event.source_event_id == "44"
    assert event.plate == "ABC123"
    assert event.confidence == 98
    assert event.char_confidences == (97, 96, 95, 94, 93, 92)
    assert event.direction is VehicleDirection.APPROACHING
    assert event.plate_rect == (100, 200, 300, 260)
    assert event.plate_size == (200, 60)
    assert event.full_image == b"full-jpeg"
    assert event.plate_image == b"plate-jpeg"
    assert event.occurred_at is None
    assert event.edge_match is EdgePlateMatch.DENYLIST
    assert event.full_image_format is ImageFormat.JPEG
    assert event.is_partial is False


def test_parse_nvr_plate_payload_copies_guid_metadata_and_images():
    event = parse_nvr_plate_payload(_nvr_payload(), user_id=5, channel_id=6)

    assert event.source is PlateSource.NVR
    assert event.source_event_id == "501"
    assert event.plate == "XYZ789"
    assert event.confidence == 96
    assert event.plate_rect == (10, 20, 110, 60)
    assert event.channel_guid == "{00000007-1234-5678-90AB-CDEF12345678}"
    assert event.full_image == b"full"
    assert event.plate_image == b"crop"
    assert event.occurred_at == datetime.fromtimestamp(1_721_234_567, tz=timezone.utc)
    assert event.edge_match is EdgePlateMatch.ALLOWLIST
    assert event.plate_image_format is ImageFormat.JPEG
    assert event.is_partial is False


def test_parse_nvr_payload_without_images_is_explicitly_partial():
    info = VEHICE_PLATE_INFO()
    info.dwPlateID = 3
    info.plateCharCount = 3
    info.plate = b"ABC"

    event = parse_nvr_plate_payload(_bytes_of(info), user_id=1, channel_id=0)

    assert event.full_image is None
    assert event.plate_image is None
    assert event.is_partial is True
    assert event.warnings == ("picture_descriptor_missing", "picture_descriptor_missing")


@pytest.mark.parametrize(
    "payload,error",
    [
        (b"short", "truncated"),
        (_ipc_payload(plate=b"\xff"), "valid UTF-8"),
    ],
)
def test_ipc_parser_rejects_malformed_payload(payload, error):
    with pytest.raises(PlatePayloadError, match=error):
        parse_ipc_plate_payload(payload, user_id=1, channel_id=0)


def test_ipc_parser_enforces_image_bound_before_copy():
    with pytest.raises(PlatePayloadError, match="image length"):
        parse_ipc_plate_payload(
            _ipc_payload(plate_image=b"12345"),
            user_id=1,
            channel_id=0,
            max_image_bytes=4,
        )


def test_stream_bounds_queue_and_counts_drop_oldest():
    stream = PlateEventStream(max_events=1, drop_policy=DropPolicy.DROP_OLDEST)
    stream.ingest(1, 2, int(SmartEventType.NVR_VEHICLE), _nvr_payload(plate_image=b"first"))
    second = _nvr_payload(plate_image=b"second")
    stream.ingest(1, 2, int(SmartEventType.NVR_VEHICLE), second)

    assert stream.get_nowait().plate_image == b"second"
    with pytest.raises(queue.Empty):
        stream.get_nowait()
    stats = stream.stats()
    assert stats.callbacks_received == 2
    assert stats.events_parsed == 2
    assert stats.events_dropped == 1
    assert stats.buffered_image_bytes == 0


def test_stream_enforces_aggregate_image_byte_budget():
    stream = PlateEventStream(max_events=10, max_buffer_bytes=10)
    stream.ingest(1, 2, int(SmartEventType.NVR_VEHICLE), _nvr_payload(plate_image=b"first"))
    stream.ingest(1, 2, int(SmartEventType.NVR_VEHICLE), _nvr_payload(plate_image=b"second"))

    event = stream.get_nowait()
    assert event.plate_image == b"second"
    stats = stream.stats()
    assert stats.events_dropped == 1
    assert stats.buffered_image_bytes == 0


def test_stream_records_malformed_and_ignored_callbacks_without_raising():
    stream = PlateEventStream()
    stream.ingest(1, 2, int(SmartEventType.NVR_VEHICLE), b"bad")
    stream.ingest(1, 2, 999, b"ignored")
    stream.reject_callback("native payload too large")

    stats = stream.stats()
    assert stats.callbacks_received == 2
    assert stats.malformed_payloads == 1
    assert stats.ignored_commands == 1
    assert stats.rejected_callbacks == 1
    assert stats.last_error == "native payload too large"
