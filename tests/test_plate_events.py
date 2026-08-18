"""Fixture-first tests for typed TVT license-plate callback ingestion."""

from __future__ import annotations

import contextlib
import ctypes as ct
import queue
import threading
from datetime import datetime, timezone

import pytest

from pytvt.device_sdk.constants import SmartEventType
from pytvt.device_sdk.plate_events import (
    DropPolicy,
    EdgePlateMatch,
    ImageFormat,
    PlateColor,
    PlateEventStream,
    PlatePayloadError,
    PlateSource,
    VehicleColor,
    VehicleDirection,
    VehicleType,
    parse_ipc_plate_payload,
    parse_nvr_plate_payload,
    parse_vsd_vehicle_payload,
)
from pytvt.device_sdk.types import (
    NET_DVR_SUBSCRIBE_REPLY,
    NET_SDK_IVE_PICTURE_INFO,
    NET_SDK_IVE_VEHICE_HEAD_INFO,
    NET_SDK_IVE_VEHICE_ITEM_INFO,
    NET_SDK_IVE_VSD_HEAD_INFO,
    NET_SDK_IVE_VSD_TARGET_INFO,
    VEHICE_PLATE_INFO,
)


def _bytes_of(value: ct.Structure) -> bytes:
    return ct.string_at(ct.byref(value), ct.sizeof(value))


def _ipc_payload(
    *,
    plate: bytes = b"ABC123",
    plate_image: bytes = b"plate-jpeg",
    confidence: int = 98,
) -> bytes:
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
    crop.plateConfidence = confidence
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


def _nvr_payload(
    *,
    full_image: bytes = b"full",
    plate_image: bytes = b"crop",
    confidence: int = 96,
) -> bytes:
    info = VEHICE_PLATE_INFO()
    info.dwPlateID = 501
    info.dwEncryptVer = 3
    info.plateCharCount = 6
    info.plate = b"XYZ789"
    info.Rect16.left = 10
    info.Rect16.top = 20
    info.Rect16.right = 110
    info.Rect16.bottom = 60
    info.plateConfidence = confidence
    info.plateIntensity = 73
    info.plateColor = 2
    info.plateStyle = 1
    info.PlateColorRate = 91
    info.vehicleColor = 5
    info.dwBrand = 88
    info.listType = 2
    info.dwStartTime = 1_721_234_567
    info.dwEndTime = 1_721_234_569
    info.chlId.Data1 = 7
    info.chlId.Data2 = 0x1234
    info.chlId.Data3 = 0x5678
    info.chlId.Data4[:] = bytes.fromhex("90ABCDEF12345678")
    return _bytes_of(info) + _picture(full_image, width=2688, height=1520) + _picture(plate_image, width=320, height=96)


def _vsd_payload() -> bytes:
    full_image = b"vsd-full"
    target_image = b"vsd-car"
    head = NET_SDK_IVE_VSD_HEAD_INFO()
    head.dwTargetCount = 1
    head.dwChannel = 7
    head.dwRelativeTick = 4567
    target = NET_SDK_IVE_VSD_TARGET_INFO()
    target.dwTargetId = 99
    target.dwTargetType = 2
    target.dwDataLen = len(target_image)
    target.rect.X1 = 10
    target.rect.Y1 = 20
    target.rect.X2 = 300
    target.rect.Y2 = 220
    target.iWidth = 290
    target.iHeight = 200
    target.iPicFormat = 0
    target.attributes.car.byColor = 9
    target.attributes.car.byYear = 11
    target.attributes.car.byType = 2
    target.attributes.car.szBrand = b"Volkswagen"
    target.attributes.car.szModel = b"Tiguan"
    target.attributes.car.dwBrandType = 88
    target.attributes.car.dwModelType = 901
    return b"".join(
        (
            _bytes_of(head),
            _picture(full_image, width=2688, height=1520),
            _bytes_of(target),
            target_image,
        )
    )


def test_plate_callback_struct_sizes_match_vendor_pack4_abi():
    assert ct.sizeof(NET_SDK_IVE_VEHICE_HEAD_INFO) == 48
    assert ct.sizeof(NET_SDK_IVE_VEHICE_ITEM_INFO) == 736
    assert ct.sizeof(VEHICE_PLATE_INFO) == 160
    assert ct.sizeof(NET_SDK_IVE_PICTURE_INFO) == 16
    assert ct.sizeof(NET_SDK_IVE_VSD_HEAD_INFO) == 12
    assert ct.sizeof(NET_SDK_IVE_VSD_TARGET_INFO) == 1128
    assert ct.sizeof(NET_DVR_SUBSCRIBE_REPLY) == 336


def test_normalized_plate_surface_is_exported_from_device_sdk():
    import pytvt.device_sdk as package

    for name in (
        "PlateEvent",
        "PlateColor",
        "PlateEventStream",
        "PlateSubscriptionInfo",
        "PlateStreamStats",
        "PlateSource",
        "VehicleDirection",
        "VehicleColor",
        "VehicleType",
        "VehicleMetadataEvent",
        "EdgePlateMatch",
        "ImageFormat",
        "parse_ipc_plate_payload",
        "parse_nvr_plate_payload",
        "parse_vsd_vehicle_payload",
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


def test_parse_ipc_plate_payload_marks_out_of_range_confidence_partial():
    event = parse_ipc_plate_payload(
        _ipc_payload(confidence=101),
        user_id=9,
        channel_id=2,
    )[0]

    assert event.confidence is None
    assert event.warnings == ("plate_confidence_out_of_range",)
    assert event.is_partial is True


def test_parse_nvr_plate_payload_copies_guid_metadata_and_images():
    received_at = datetime(2026, 8, 4, tzinfo=timezone.utc)
    event = parse_nvr_plate_payload(
        _nvr_payload(),
        user_id=5,
        channel_id=6,
        received_at=received_at,
    )

    assert event.source is PlateSource.NVR
    assert event.user_id == 5
    assert event.channel_id == 6
    assert event.received_at == received_at
    assert event.source_event_id == "501"
    assert event.plate == "XYZ789"
    assert event.declared_plate_char_count == 6
    assert event.source_encryption_version == 3
    assert event.confidence == 96
    assert event.plate_rect == (10, 20, 110, 60)
    assert event.channel_guid == "{00000007-1234-5678-90AB-CDEF12345678}"
    assert event.full_image == b"full"
    assert event.plate_image == b"crop"
    assert event.occurred_at == datetime.fromtimestamp(1_721_234_567, tz=timezone.utc)
    assert event.edge_match is EdgePlateMatch.ALLOWLIST
    assert event.edge_match_code == 2
    assert event.plate_image_format is ImageFormat.JPEG
    assert event.full_image_size == (2688, 1520)
    assert event.plate_size == (320, 96)
    assert event.plate_color_code == 2
    assert event.plate_color is PlateColor.BLACK
    assert event.plate_brightness == 73
    assert event.plate_color_confidence == 91
    assert event.vehicle_type is VehicleType.SEDAN
    assert event.vehicle_type_code == 1
    assert event.vehicle_color is VehicleColor.BLUE
    assert event.vehicle_color_code == 5
    assert event.vehicle_brand_code == 88
    assert event.source_end_at == datetime.fromtimestamp(1_721_234_569, tz=timezone.utc)
    assert event.full_image_format is ImageFormat.JPEG
    assert event.warnings == ()
    assert event.is_partial is False


def test_parse_nvr_plate_payload_marks_unsigned_confidence_sentinel_partial():
    event = parse_nvr_plate_payload(
        _nvr_payload(confidence=(1 << 32) - 1),
        user_id=5,
        channel_id=6,
    )

    assert event.confidence is None
    assert event.warnings == ("plate_confidence_out_of_range",)
    assert event.is_partial is True


def _picture_with_format(image: bytes, *, fmt: int, width: int = 0, height: int = 0) -> bytes:
    info = NET_SDK_IVE_PICTURE_INFO()
    info.iWidth = width
    info.iHeight = height
    info.iPicFormat = fmt
    info.iPicSize = len(image)
    return _bytes_of(info) + image


_JPEG_STUB = b"\xff\xd8\xff\xe0" + b"j" * 8 + b"\xff\xd9"


def _uat_nvr_payload(*, confidence: int = 9900, trailing: bytes = b"\x00" * 288) -> bytes:
    """Shape observed live on UAT recorders (2026-08-18, NVR 10.40.20.250)."""
    info = VEHICE_PLATE_INFO()
    info.dwPlateID = 7350
    info.dwEncryptVer = 1
    info.plateCharCount = 6
    info.plate = b"IWX354"
    info.Rect16.left = 1985
    info.Rect16.top = 470
    info.Rect16.right = 2113
    info.Rect16.bottom = 574
    info.plateConfidence = confidence
    info.plateColor = 3
    info.plateStyle = 1
    info.chlId.Data1 = 2
    return (
        _bytes_of(info) + _picture_with_format(_JPEG_STUB, fmt=1) + _picture_with_format(_JPEG_STUB, fmt=1) + trailing
    )


def test_parse_nvr_plate_payload_normalizes_basis_point_confidence():
    # UAT recorders report plateConfidence in basis points (9900 == 99.00 %).
    event = parse_nvr_plate_payload(_uat_nvr_payload(confidence=9900), user_id=1, channel_id=2)
    assert event.confidence == 99
    assert "plate_confidence_out_of_range" not in event.warnings


def test_parse_nvr_plate_payload_keeps_percent_confidence_and_rejects_sentinel():
    assert parse_nvr_plate_payload(_uat_nvr_payload(confidence=87), user_id=1, channel_id=2).confidence == 87
    assert parse_nvr_plate_payload(_uat_nvr_payload(confidence=10_000), user_id=1, channel_id=2).confidence == 100
    event = parse_nvr_plate_payload(_uat_nvr_payload(confidence=10_001), user_id=1, channel_id=2)
    assert event.confidence is None
    assert "plate_confidence_out_of_range" in event.warnings


def test_parse_nvr_plate_payload_sniffs_jpeg_despite_yuv_format_code():
    # The recorder tags iPicFormat=1 (documented YUV) on real JPEG bytes.
    event = parse_nvr_plate_payload(_uat_nvr_payload(), user_id=1, channel_id=2)
    assert event.full_image == _JPEG_STUB
    assert event.plate_image == _JPEG_STUB
    assert event.full_image_format is ImageFormat.JPEG
    assert event.plate_image_format is ImageFormat.JPEG


def test_parse_nvr_plate_payload_ignores_trailing_nul_padding():
    event = parse_nvr_plate_payload(_uat_nvr_payload(trailing=b"\x00" * 288), user_id=1, channel_id=2)
    assert event.warnings == ()
    assert event.is_partial is False


def test_parse_nvr_plate_payload_still_flags_non_nul_trailing_bytes():
    event = parse_nvr_plate_payload(_uat_nvr_payload(trailing=b"\x00" * 10 + b"\x01"), user_id=1, channel_id=2)
    assert "trailing_payload_bytes" in event.warnings
    assert event.is_partial is True


def test_parse_vsd_vehicle_payload_copies_readable_vehicle_attributes_and_images():
    received_at = datetime(2026, 8, 4, tzinfo=timezone.utc)
    events = parse_vsd_vehicle_payload(
        _vsd_payload(),
        user_id=5,
        channel_id=6,
        received_at=received_at,
    )

    assert len(events) == 1
    event = events[0]
    assert event.user_id == 5
    assert event.channel_id == 7
    assert event.received_at == received_at
    assert event.relative_tick == 4567
    assert event.target_id == 99
    assert event.target_rect == (10, 20, 300, 220)
    assert event.vehicle_color is VehicleColor.WHITE
    assert event.vehicle_year == 2018
    assert event.vehicle_type is VehicleType.SUV
    assert event.vehicle_brand == "Volkswagen"
    assert event.vehicle_model == "Tiguan"
    assert event.vehicle_brand_code == 88
    assert event.vehicle_model_code == 901
    assert event.full_image == b"vsd-full"
    assert event.target_image == b"vsd-car"
    assert event.full_image_size == (2688, 1520)
    assert event.target_image_size == (290, 200)
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
    ("full_image", "plate_image", "missing_field"),
    [
        (b"", b"crop", "full_image"),
        (b"full", b"", "plate_image"),
    ],
)
def test_parse_nvr_zero_byte_picture_descriptor_is_partial(full_image, plate_image, missing_field):
    event = parse_nvr_plate_payload(
        _nvr_payload(full_image=full_image, plate_image=plate_image),
        user_id=1,
        channel_id=0,
    )

    assert getattr(event, missing_field) is None
    assert event.is_partial is True
    assert event.warnings == ("picture_data_missing",)


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


def test_blocked_reader_exits_when_stream_closes():
    stream = PlateEventStream()
    started = threading.Event()
    finished = threading.Event()
    errors = []

    def read_event():
        started.set()
        try:
            stream.get()
        except RuntimeError as exc:
            errors.append(str(exc))
        finally:
            finished.set()

    reader = threading.Thread(target=read_event, daemon=True)
    reader.start()
    assert started.wait(timeout=1.0)

    stream.close()
    exited_after_close = finished.wait(timeout=0.2)
    if not exited_after_close:
        stream._queue.put_nowait(
            parse_nvr_plate_payload(
                _nvr_payload(),
                user_id=1,
                channel_id=0,
            )
        )
        assert finished.wait(timeout=1.0)
    reader.join(timeout=1.0)

    assert exited_after_close is True
    assert errors == ["plate-event stream is closed"]


def test_closed_stream_preserves_buffered_events_without_counting_wake_signal():
    stream = PlateEventStream()
    stream.ingest(1, 2, int(SmartEventType.NVR_VEHICLE), _nvr_payload())

    stream.close()

    assert stream.stats().buffered_events == 1
    assert stream.get_nowait().plate == "XYZ789"
    assert stream.stats().buffered_events == 0
    with pytest.raises(queue.Empty):
        stream.get_nowait()


def test_close_wakes_all_readers_when_queue_was_full(monkeypatch):
    stream = PlateEventStream(max_events=1)
    stream.ingest(1, 2, int(SmartEventType.NVR_VEHICLE), _nvr_payload())
    stream.close()
    dequeue_barrier = threading.Barrier(2)
    original_get = stream._queue.get

    def synchronized_get(*args, **kwargs):
        dequeue_barrier.wait(timeout=1.0)
        return original_get(*args, **kwargs)

    monkeypatch.setattr(stream._queue, "get", synchronized_get)
    finished = [threading.Event(), threading.Event()]
    events = []
    errors = []

    def read_event(reader_index):
        try:
            events.append(stream.get())
        except RuntimeError as exc:
            errors.append(str(exc))
        finally:
            finished[reader_index].set()

    readers = [threading.Thread(target=read_event, args=(index,), daemon=True) for index in range(2)]
    for reader in readers:
        reader.start()

    exited_after_close = [done.wait(timeout=0.5) for done in finished]
    if not all(exited_after_close):
        with contextlib.suppress(queue.Full):
            stream._queue.put_nowait(None)
    for reader in readers:
        reader.join(timeout=1.0)

    assert exited_after_close == [True, True]
    assert [event.plate for event in events] == ["XYZ789"]
    assert errors == ["plate-event stream is closed"]
