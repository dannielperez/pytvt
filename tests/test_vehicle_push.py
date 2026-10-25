from datetime import datetime, timedelta, timezone

import pytest

from pytvt.device_sdk.plate_events import PlatePayloadError, VehicleDirection
from pytvt.vehicle_push import MAX_PUSH_BYTES, parse_ipc_vehicle_push

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
STAMP = str(int(NOW.timestamp()) * 1_000_000)


def payload(*, direction="approach", stamp=STAMP, confidence="9900", image="/9j/2Q=="):
    return f'''<config version="1.7" xmlns="http://www.ipc.com/ver10">
    <smartType>VEHICLE</smartType><currentTime>{stamp}</currentTime><listInfo>
    <item><image>0</image><targetImageData><targetBase64Data> /9j/2Q== \n</targetBase64Data></targetImageData></item>
    <item><image>1</image><plateNumber>TEST123</plateNumber><vehicleDirect>{direction}</vehicleDirect>
    <PlateConfidence count="{confidence}"/><targetImageData><targetBase64Data>{image}</targetBase64Data></targetImageData>
    </item></listInfo></config>'''.encode()


def decode(body, **kwargs):
    return parse_ipc_vehicle_push(body, received_at=NOW, channel_id=0, **kwargs)


def test_captured_shape_decodes_separate_overview_and_plate_without_overview_event():
    events = decode(payload())
    assert len(events) == 1
    event = events[0]
    assert event.plate == "TEST123"
    assert event.confidence == 99
    assert event.direction is VehicleDirection.APPROACHING
    assert event.occurred_at == NOW
    assert event.full_image == event.plate_image == b"\xff\xd8\xff\xd9"


def test_replay_identity_does_not_depend_on_receipt_time():
    first = decode(payload())[0]
    later = parse_ipc_vehicle_push(payload(), received_at=NOW + timedelta(seconds=2), channel_id=0)[0]
    assert first.source_event_id == later.source_event_id


@pytest.mark.parametrize("stamp", ["", "0", "garbage", "1792886400"])
def test_no_receipt_time_substitution(stamp):
    event = decode(payload(stamp=stamp))[0]
    assert event.occurred_at is None
    assert "capture_time_unverified" in event.warnings


@pytest.mark.parametrize(
    "direction,expected",
    [("away", VehicleDirection.LEAVING), ("unknown", VehicleDirection.UNKNOWN), ("", VehicleDirection.UNKNOWN)],
)
def test_direction_is_explicit(direction, expected):
    assert decode(payload(direction=direction))[0].direction is expected


@pytest.mark.parametrize("confidence", ["10001", "-1", "garbage", ""])
def test_invalid_confidence_is_not_upgraded(confidence):
    assert decode(payload(confidence=confidence))[0].confidence is None


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<config>",
        b"x" * (MAX_PUSH_BYTES + 1),
        b'<!DOCTYPE config [<!ENTITY x "unsafe">]><config/>',
        payload().replace(b"<currentTime>", b"<currentTime>1</currentTime><currentTime>"),
        payload().replace(b'version="1.7"', b'version="2.0"'),
        payload().replace(b"http://www.ipc.com/ver10", b"https://unknown.invalid"),
        payload(image="not-base64"),
        payload(image="dGVzdA=="),
    ],
)
def test_reject_unsupported_or_ambiguous_evidence(body):
    with pytest.raises(PlatePayloadError):
        decode(body)
