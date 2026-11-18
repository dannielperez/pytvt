import base64
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from pytvt.device_sdk.plate_events import (
    EdgePlateMatch,
    ImageFormat,
    PlateColor,
    PlatePayloadError,
    PlateSource,
    PlateSourceDevice,
    VehicleColor,
    VehicleDirection,
    VehicleType,
)
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


def test_jpeg_transport_trailer_is_removed_without_retaining_extra_bytes():
    jpeg = b"\xff\xd8\xff\xd9"
    encoded = base64.b64encode(jpeg + b"\x00\xa1\x7f").decode()
    assert decode(payload(image=encoded))[0].plate_image == jpeg


def test_jpeg_without_end_marker_is_rejected():
    with pytest.raises(PlatePayloadError, match="JPEG framing"):
        decode(payload(image="/9gAAA=="))


@pytest.mark.parametrize("value", ["true", "false"])
def test_vehicle_status_is_not_a_plate_arrival(value):
    body = f'<config version="1.7"><alarmStatusInfo><vehiceAlarm>{value}</vehiceAlarm></alarmStatusInfo><dataTime>time</dataTime><deviceInfo/></config>'.encode()
    assert decode(body) == ()


def test_mixed_status_and_untyped_detection_is_not_silently_ignored():
    body = (
        b'<config version="1.7"><alarmStatusInfo><vehiceAlarm>true</vehiceAlarm></alarmStatusInfo><listInfo/></config>'
    )
    with pytest.raises(PlatePayloadError):
        decode(body)


# The fields below follow the OEM-documented LPR post shape (CCTV Camera Pros
# "IPC Event Format (v1.x)", LPR-IP4 firmware 5.3.1). They are DOC/UNIT-validated
# only: the synthetic fixtures here are not live captures, and the live capture
# behind docs/ipc-vehicle-http-push.md was not retained as a test fixture.
METADATA = """<plateCharCount type="uint32">7</plateCharCount>
    <vehicleListType type="vehileMatchAlarmList">whiteList</vehicleListType>
    <PlateWidth type="uint32">176</PlateWidth><PlateHeight type="uint32">100</PlateHeight>
    <SourceImageWidth type="uint32">2688</SourceImageWidth><SourceImageHeight type="uint32">1776</SourceImageHeight>
    <leftTop><x type="uint32">7559</x><y type="uint32">2072</y></leftTop>
    <rightTop><x type="uint32">8214</x><y type="uint32">2072</y></rightTop>
    <leftBottom><x type="uint32">7559</x><y type="uint32">2635</y></leftBottom>
    <rightBottom><x type="uint32">8214</x><y type="uint32">2635</y></rightBottom>
    <plateColor type="uint32" count="3"/><plateColorRate type="uint32" count="0"/>
    <plateStyle type="uint32" count="1"/><vehiceColor type="uint32" count="0"/>
    <plateAngleH type="uint32" count="0"/><plateAngleV type="uint32" count="0"/>
    <province type="string"></province><vehiceId type="uint32">1</vehiceId>
    <carAttr><color type="vsdCarAttrColorType">white</color><type type="vsdCarAttrTypeType">saloon car</type>
    <brand type="vsdCarAttrBrandType">Toyota</brand><model type="vsdCarAttrModelType">Toyota_Camry</model></carAttr>
    <carRect><x type="uint32">1242</x><y type="uint32"/><width type="uint32">586</width><height type="uint32">446</height></carRect>"""
DEVICE = """<mac type="string"><![CDATA[00:00:00:00:00:00]]></mac><sn type="string"><![CDATA[SERIALNUMBER]]></sn>
    <deviceName type="string"><![CDATA[LPR Camera]]></deviceName>"""


def rich(*, metadata=METADATA, device=DEVICE):
    return (
        payload()
        .replace(b"<listInfo>", device.encode() + b"<listInfo>")
        .replace(b"<PlateConfidence", metadata.encode() + b"<PlateConfidence")
    )


def test_documented_metadata_decodes_into_existing_slots_doc_validated():
    """DOC/UNIT-validated: OEM 5.3.1 example values, not a live capture."""
    event = decode(rich())[0]
    assert event.declared_plate_char_count == 7
    assert event.plate_rect == (7559, 2072, 8214, 2635)
    assert event.plate_size == (176, 100)
    assert event.full_image_size == (2688, 1776)
    assert event.edge_match is EdgePlateMatch.ALLOWLIST
    assert event.edge_match_code is None
    assert event.plate_color_code == 3
    # The plate colour code-to-name table is undocumented for HTTP push; the
    # NetSDK colour table is not assumed to apply.
    assert event.plate_color is PlateColor.UNKNOWN
    assert event.vehicle_color is VehicleColor.UNKNOWN
    assert event.vehicle_color_code is None
    assert event.vehicle_type is VehicleType.UNKNOWN
    assert event.vehicle_type_code is None
    assert event.vehicle_brand_code is None
    assert event.plate_color_confidence is None


def test_undocumented_vendor_values_stay_raw_doc_validated():
    """DOC/UNIT-validated: raw vendor strings are kept, never mapped to enums."""
    attributes = dict(decode(rich())[0].vendor_attributes)
    assert attributes == {
        "vehiceId": "1",
        "vehicleListType": "whiteList",
        "plateColorRate": "0",
        "plateStyle": "1",
        "vehiceColor": "0",
        "plateAngleH": "0",
        "plateAngleV": "0",
        "carAttr/color": "white",
        "carAttr/type": "saloon car",
        "carAttr/brand": "Toyota",
        "carAttr/model": "Toyota_Camry",
        "carRect/x": "1242",
        "carRect/width": "586",
        "carRect/height": "446",
    }


def test_source_device_identity_is_reported_not_trusted_doc_validated():
    """DOC/UNIT-validated: root mac/sn/deviceName as in the OEM LPR example."""
    event = decode(rich())[0]
    assert event.source_device == PlateSourceDevice(name="LPR Camera", mac="00:00:00:00:00:00", serial="SERIALNUMBER")


def test_device_info_block_is_accepted_and_must_agree_doc_validated():
    """DOC/UNIT-validated: deviceInfo naming from the documented status/keepalive frames."""
    block = "<deviceInfo><deviceName>LPR Camera</deviceName><ipAddress>192.0.2.100</ipAddress><macAddress>00:00:00:00:00:00</macAddress></deviceInfo>"
    event = decode(rich(device=DEVICE + block))[0]
    assert event.source_device == PlateSourceDevice(
        name="LPR Camera", mac="00:00:00:00:00:00", serial="SERIALNUMBER", ip="192.0.2.100"
    )
    conflicting = decode(rich(device=DEVICE + block.replace("LPR Camera", "Other Camera")))[0]
    assert conflicting.source_device == PlateSourceDevice(
        mac="00:00:00:00:00:00", serial="SERIALNUMBER", ip="192.0.2.100"
    )
    assert ("_dropped", "source_device/name") in conflicting.vendor_attributes
    assert conflicting.warnings == ()


def test_absent_metadata_keeps_previous_defaults():
    event = decode(payload())[0]
    assert event.declared_plate_char_count is None
    assert event.plate_rect is None
    assert event.plate_size is None
    assert event.full_image_size is None
    assert event.edge_match is EdgePlateMatch.UNKNOWN
    assert event.plate_color_code is None
    assert event.vendor_attributes == ()
    assert event.source_device is None
    assert event.is_partial is False


@pytest.mark.parametrize("value", ["temporaryList", "greyList", ""])
def test_unconfirmed_or_unknown_list_type_stays_unknown(value):
    """DOC/UNIT-validated: only whiteList/blackList are confirmed in captured posts."""
    metadata = f"<vehicleListType>{value}</vehicleListType>"
    event = decode(rich(metadata=metadata))[0]
    assert event.edge_match is EdgePlateMatch.UNKNOWN
    assert dict(event.vendor_attributes).get("vehicleListType") == (value or None)


def test_blacklist_maps_to_denylist():
    event = decode(rich(metadata="<vehicleListType>blackList</vehicleListType>"))[0]
    assert event.edge_match is EdgePlateMatch.DENYLIST


@pytest.mark.parametrize(
    "metadata,dropped",
    [
        (
            "<plateCharCount>seven</plateCharCount><plateColor count='blue'/><PlateWidth>-1</PlateWidth><PlateHeight>9</PlateHeight>",
            {"plateCharCount", "plateColor", "PlateWidth"},
        ),
        (
            "<plateCharCount>99</plateCharCount><plateColor/><PlateWidth>0</PlateWidth><PlateHeight>0</PlateHeight>",
            {"plateCharCount"},
        ),
        ("<leftTop><x>1</x><y>2</y></leftTop><rightBottom><x>10001</x><y>3</y></rightBottom>", {"rightBottom/x"}),
        ("<leftTop><x>9</x><y>2</y></leftTop><rightBottom><x>1</x><y>3</y></rightBottom>", {"plate_rect"}),
        ("<leftTop><x>1</x><y>2</y></leftTop>", set()),
        ("<SourceImageWidth>2688</SourceImageWidth>", set()),
    ],
)
def test_malformed_metadata_is_dropped_not_upgraded(metadata, dropped):
    event = decode(rich(metadata=metadata))[0]
    assert event.declared_plate_char_count is None
    assert event.plate_color_code is None
    assert event.plate_size is None
    assert event.plate_rect is None
    assert event.full_image_size is None
    assert {value for key, value in event.vendor_attributes if key == "_dropped"} == dropped
    assert event.warnings == ()
    assert event.is_partial is False


@pytest.mark.parametrize(
    "metadata,field,dropped",
    [
        (
            "<plateCharCount>7</plateCharCount><plateCharCount>7</plateCharCount>",
            "declared_plate_char_count",
            "plateCharCount",
        ),
        ("<plateColor count='3'/><plateColor count='3'/>", "plate_color_code", "plateColor"),
        (
            "<leftTop><x>1</x><y>2</y></leftTop><leftTop><x>1</x><y>2</y></leftTop><rightBottom><x>5</x><y>5</y></rightBottom>",
            "plate_rect",
            "leftTop/x",
        ),
        (
            "<PlateWidth>1</PlateWidth><PlateWidth>1</PlateWidth><PlateHeight>1</PlateHeight>",
            "plate_size",
            "PlateWidth",
        ),
        ("<carAttr><color>white</color></carAttr><carAttr><color>red</color></carAttr>", None, "carAttr/color"),
        (
            "<vehicleListType>whiteList</vehicleListType><vehicleListType>blackList</vehicleListType>",
            None,
            "vehicleListType",
        ),
        ("<plateStyle count='1'/><plateStyle count='2'/>", None, "plateStyle"),
        (f"<carAttr><brand>{'x' * 129}</brand></carAttr>", None, "carAttr/brand"),
        (f"<plateCharCount>{'7' * 129}</plateCharCount>", "declared_plate_char_count", "plateCharCount"),
    ],
)
def test_ambiguous_or_oversized_new_field_is_dropped_and_event_still_decodes(metadata, field, dropped):
    """New metadata is best-effort: dropped and named, never raised or warned."""
    event = decode(rich(metadata=metadata))[0]
    assert event.plate == "TEST123"
    if field:
        assert getattr(event, field) is None
    assert event.edge_match is EdgePlateMatch.UNKNOWN
    assert ("_dropped", dropped) in event.vendor_attributes
    assert dropped not in dict(event.vendor_attributes)
    assert event.warnings == ()
    assert event.is_partial is False


@pytest.mark.parametrize(
    "device,dropped",
    [
        ("<sn>A</sn><sn>A</sn>", "sn"),
        ("<deviceInfo><sn>A</sn></deviceInfo><deviceInfo><sn>A</sn></deviceInfo>", "deviceInfo/sn"),
        (
            "<deviceInfo><mac>00:00:00:00:00:01</mac><macAddress>00:00:00:00:00:02</macAddress></deviceInfo>",
            "source_device/mac",
        ),
        (f"<deviceName>{'x' * 129}</deviceName>", "deviceName"),
    ],
)
def test_ambiguous_device_identity_is_dropped_not_raised(device, dropped):
    event = decode(rich(device=device))[0]
    assert event.source_device is None
    assert ("_dropped", dropped) in event.vendor_attributes
    assert event.warnings == ()
    assert event.is_partial is False


OLD_FIELDS = (
    "user_id",
    "channel_id",
    "source",
    "received_at",
    "occurred_at",
    "plate",
    "confidence",
    "direction",
    "full_image",
    "plate_image",
    "full_image_format",
    "plate_image_format",
    "warnings",
    "is_partial",
)
JPEG = b"\xff\xd8\xff\xd9"
NO_CROP = payload().replace(b"<targetImageData><targetBase64Data>/9j/2Q==", b"<targetImageData><targetBase64Data>")
NO_OVERVIEW = payload().replace(b"<image>0</image>", b"<image>2</image>")
UNVERIFIED = payload(stamp="", direction="", confidence="garbage")
BASE = (
    0,
    0,
    PlateSource.IPC,
    NOW,
    NOW,
    "TEST123",
    99,
    VehicleDirection.APPROACHING,
    JPEG,
    JPEG,
    ImageFormat.JPEG,
    ImageFormat.JPEG,
    (),
    False,
)
UNVERIFIED_EXPECTED = (
    *BASE[:4],
    None,
    "TEST123",
    None,
    VehicleDirection.UNKNOWN,
    *BASE[8:12],
    ("capture_time_unverified", "direction_unverified", "confidence_unverified"),
    False,
)
HOSTILE_METADATA = (
    "<plateCharCount>7</plateCharCount><plateCharCount>8</plateCharCount><plateColor count='x'/>"
    "<leftTop><x>9</x><y>9</y></leftTop><rightBottom><x>1</x><y>1</y></rightBottom>"
    f"<carAttr><brand>{'x' * 500}</brand></carAttr><vehicleListType>a</vehicleListType><vehicleListType>b</vehicleListType>"
)
HOSTILE_DEVICE = "<sn>A</sn><sn>B</sn><deviceInfo><mac>1</mac><macAddress>2</macAddress></deviceInfo>"


def hostile(body):
    return body.replace(b"<listInfo>", HOSTILE_DEVICE.encode() + b"<listInfo>").replace(
        b"<PlateConfidence", HOSTILE_METADATA.encode() + b"<PlateConfidence"
    )


@pytest.mark.parametrize(
    "body,expected",
    [
        (payload(), BASE),
        (UNVERIFIED, UNVERIFIED_EXPECTED),
        (NO_CROP, (*BASE[:9], None, ImageFormat.JPEG, None, (), False)),
        (NO_OVERVIEW, (*BASE[:8], None, JPEG, None, ImageFormat.JPEG, (), False)),
        (rich(), BASE),
        (hostile(payload()), BASE),
        (hostile(UNVERIFIED), UNVERIFIED_EXPECTED),
        (hostile(NO_CROP), (*BASE[:9], None, ImageFormat.JPEG, None, (), False)),
        (hostile(NO_OVERVIEW), (*BASE[:8], None, JPEG, None, ImageFormat.JPEG, (), False)),
    ],
)
def test_previously_accepted_payloads_decode_identically(body, expected):
    """Regression: every field the original decoder set keeps its value, warnings and is_partial=False.

    Expected tuples were cross-checked against the pre-change decoder (6f40ab5).
    """
    (event,) = decode(body)
    assert tuple(getattr(event, name) for name in OLD_FIELDS) == expected
    assert event.source_event_id == f"http:{hashlib.sha256(body).hexdigest()}:1"
