"""Typed contracts for the process-isolated Unix runtime client."""

from __future__ import annotations

import base64
import json
import queue
from datetime import datetime, timezone

import pytest

from pytvt.device_sdk import PlateSource
from pytvt.runtime_client import (
    RUNTIME_PROTOCOL_VERSION,
    RuntimeClientError,
    RuntimeRemoteError,
    SyncRuntimeClient,
    _parse_response,
    _request_payload,
)


def test_request_envelope_carries_protocol_version() -> None:
    payload = _request_payload("request-1", "health", None)

    assert json.loads(payload) == {
        "protocol": RUNTIME_PROTOCOL_VERSION,
        "id": "request-1",
        "method": "health",
    }


def test_request_envelope_carries_explicit_operation_deadline() -> None:
    payload = _request_payload(
        "request-1",
        "execute",
        {"operation": "snapshot"},
        timeout_ms=3_000,
    )

    assert json.loads(payload)["timeoutMilliseconds"] == 3_000


def test_response_rejects_server_without_matching_protocol() -> None:
    response = b'{"id":"request-1","ok":true,"result":{}}\n'

    with pytest.raises(RuntimeClientError, match="invalid response envelope"):
        _parse_response(response, "request-1")


def test_remote_error_preserves_typed_cooldown() -> None:
    response = json.dumps(
        {
            "protocol": RUNTIME_PROTOCOL_VERSION,
            "id": "request-1",
            "ok": False,
            "error": {"kind": "login_credential", "message": "rejected"},
            "retry_after_seconds": 900,
        }
    ).encode()

    with pytest.raises(RuntimeRemoteError) as exc_info:
        _parse_response(response, "request-1")

    assert exc_info.value.kind == "login_credential"
    assert exc_info.value.retry_after_seconds == 900


def test_typed_device_health_reuses_device_info_operation() -> None:
    captured: dict = {}

    class Client(SyncRuntimeClient):
        def execute(self, job, *, timeout_ms=None):
            captured.update(job)
            captured["timeout_ms"] = timeout_ms
            return {
                "success": True,
                "device_name": "Gate NVR",
                "device_model": "TD-3332H2",
                "serial_number": "serial-1",
                "firmware": "1.2.3",
                "hardware_version": "A1",
                "kernel_version": "4.9",
                "mcu_version": "",
                "video_inputs": 16,
                "audio_inputs": 1,
                "sensor_inputs": 4,
                "sensor_outputs": 2,
                "device_type": 7,
                "error": None,
            }

    result = Client().probe_device_health(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        timeout_ms=1_500,
    )

    assert captured == {
        "operation": "deviceInfo",
        "credentials": {
            "ip": "192.0.2.10",
            "port": 6036,
            "username": "operator",
            "password": "secret",
        },
        "timeout_ms": 1_500,
    }
    assert result.device_name == "Gate NVR"
    assert result.video_inputs == 16


def test_typed_channel_scan_maps_bounded_status() -> None:
    captured: dict = {}

    class Client(SyncRuntimeClient):
        def execute(self, job, *, timeout_ms=None):
            captured.update(job)
            captured["timeout_ms"] = timeout_ms
            return {
                "success": True,
                "device_name": "Gate NVR",
                "device_model": "TD-3332H2",
                "serial_number": "serial-1",
                "firmware": "1.2.3",
                "total_channels": 2,
                "cameras": [
                    {
                        "channel": 0,
                        "name": "Gate 1",
                        "address": "192.0.2.20",
                        "port": 6036,
                        "httpPort": 80,
                        "status": "Online",
                        "protocol": "TVT",
                        "model": "TD-9422S4",
                        "deviceId": 0,
                    },
                    {
                        "channel": 1,
                        "name": "Gate 2",
                        "address": "192.0.2.21",
                        "port": 6036,
                        "httpPort": 80,
                        "status": "Offline",
                        "protocol": "TVT",
                        "model": "TD-9422S4",
                        "deviceId": 0,
                    },
                ],
                "error": None,
            }

    result = Client().scan_channels(
        "192.0.2.10",
        "operator",
        "secret",
        max_channels=16,
        timeout_ms=2_000,
    )

    assert captured["operation"] == "scan"
    assert captured["maxCameras"] == 16
    assert captured["timeout_ms"] == 2_000
    assert [channel.online for channel in result.channels] == [True, False]


@pytest.mark.parametrize(
    "mutation",
    [
        {"total_channels": 2},
        {"cameras": [{"channel": 0}]},
    ],
)
def test_typed_channel_scan_rejects_malformed_results(mutation) -> None:
    class Client(SyncRuntimeClient):
        def execute(self, _job):
            result = {
                "success": True,
                "device_name": "Gate NVR",
                "device_model": "TD-3332H2",
                "serial_number": "serial-1",
                "firmware": "1.2.3",
                "total_channels": 0,
                "cameras": [],
            }
            result.update(mutation)
            return result

    with pytest.raises(RuntimeClientError, match="invalid channel scan"):
        Client().scan_channels("192.0.2.10", "operator", "secret")


def test_typed_runtime_snapshot_validates_and_decodes_jpeg() -> None:
    captured: dict = {}
    image = b"\xff\xd8snapshot\xff\xd9"

    class Client(SyncRuntimeClient):
        def execute(self, job):
            captured.update(job)
            return base64.b64encode(image).decode()

    result = Client().capture_snapshot(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        channel=6,
    )

    assert captured["operation"] == "snapshot"
    assert captured["channel"] == 6
    assert result.image == image
    assert result.channel == 6
    assert result.method == "runtime"


@pytest.mark.parametrize(
    "result",
    ["not-base64", base64.b64encode(b"not-jpeg").decode()],
)
def test_typed_runtime_snapshot_rejects_invalid_payload(result: str) -> None:
    class Client(SyncRuntimeClient):
        def execute(self, _job):
            return result

    with pytest.raises(RuntimeClientError, match="invalid snapshot"):
        Client().capture_snapshot(
            "192.0.2.10",
            "operator",
            "secret",
            channel=0,
        )


def test_typed_runtime_rtsp_url_owns_schema_and_validates_result() -> None:
    captured: dict = {}

    class Client(SyncRuntimeClient):
        def execute(self, job, *, timeout_ms=None):
            captured.update(job)
            captured["timeout_ms"] = timeout_ms
            return "rtsp://operator:secret@192.0.2.10:554/live/ch00_0"

    result = Client().resolve_rtsp_url(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        channel=6,
        stream_type=1,
        timeout_ms=2_000,
    )

    assert captured == {
        "operation": "rtspUrl",
        "credentials": {
            "ip": "192.0.2.10",
            "port": 6036,
            "username": "operator",
            "password": "secret",
        },
        "channel": 6,
        "streamType": 1,
        "timeout_ms": 2_000,
    }
    assert result.url == "rtsp://operator:secret@192.0.2.10:554/live/ch00_0"
    assert result.channel == 6
    assert result.stream_type == 1
    assert result.method == "runtime"


@pytest.mark.parametrize(
    "result",
    [
        None,
        "",
        "http://192.0.2.10/live",
        "rtsp:///missing-host",
        "rtsp://192.0.2.10/live\nforged",
        "rtsp://192.0.2.10/" + "x" * 4096,
    ],
)
def test_typed_runtime_rtsp_url_rejects_invalid_payload(result) -> None:
    class Client(SyncRuntimeClient):
        def execute(self, _job):
            return result

    with pytest.raises(RuntimeClientError, match="invalid RTSP URL"):
        Client().resolve_rtsp_url(
            "192.0.2.10",
            "operator",
            "secret",
            channel=0,
        )


@pytest.mark.parametrize("channel", [-1, 256, True])
def test_typed_runtime_rtsp_url_rejects_invalid_channel(channel) -> None:
    with pytest.raises(ValueError, match="channel"):
        SyncRuntimeClient().resolve_rtsp_url(
            "192.0.2.10",
            "operator",
            "secret",
            channel=channel,
        )


@pytest.mark.parametrize("stream_type", [-1, 3, True])
def test_typed_runtime_rtsp_url_rejects_invalid_stream_type(stream_type) -> None:
    with pytest.raises(ValueError, match="stream_type"):
        SyncRuntimeClient().resolve_rtsp_url(
            "192.0.2.10",
            "operator",
            "secret",
            stream_type=stream_type,
        )


def test_typed_runtime_device_time_owns_read_only_schema() -> None:
    captured: dict = {}

    class Client(SyncRuntimeClient):
        def execute(self, job, *, timeout_ms=None):
            captured.update(job)
            captured["timeout_ms"] = timeout_ms
            return {
                "success": True,
                "action": "get",
                "device_time": "2026-08-11 10:15:30",
                "timestamp": None,
                "error": None,
            }

    result = Client().read_device_time(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        timeout_ms=2_000,
    )

    assert captured == {
        "operation": "deviceTime",
        "credentials": {
            "ip": "192.0.2.10",
            "port": 6036,
            "username": "operator",
            "password": "secret",
        },
        "timeout_ms": 2_000,
    }
    assert result.local_time == datetime(2026, 8, 11, 10, 15, 30)
    assert result.timezone_known is False


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {
            "success": True,
            "action": "set",
            "device_time": "2026-08-11 10:15:30",
            "timestamp": 1_786_443_330,
            "error": None,
        },
        {
            "success": True,
            "action": "get",
            "device_time": "not-a-time",
            "timestamp": None,
            "error": None,
        },
        {
            "success": True,
            "action": "get",
            "device_time": "2026-08-11T10:15:30+00:00",
            "timestamp": None,
            "error": None,
        },
    ],
)
def test_typed_runtime_device_time_rejects_invalid_payload(result) -> None:
    class Client(SyncRuntimeClient):
        def execute(self, _job):
            return result

    with pytest.raises(RuntimeClientError, match="invalid device time"):
        Client().read_device_time(
            "192.0.2.10",
            "operator",
            "secret",
        )


def test_typed_face_batch_owns_job_schema_and_result_validation() -> None:
    captured: dict = {}
    image = b"\xff\xd8face\xff\xd9"

    class Client(SyncRuntimeClient):
        def execute(self, job):
            captured.update(job)
            return {
                "success": True,
                "captures": [
                    {
                        "channel_index": 6,
                        "channel_deleted": False,
                        "captured_at_device": "2026-08-07T10:00:00.123456",
                        "device_time_ticks": 123,
                        "snapshot_image_id": 41,
                        "target_image_id": 7,
                        "image_base64": base64.b64encode(image).decode(),
                        "image_error": None,
                    }
                ],
                "complete": True,
                "page": 2,
            }

    result = Client().search_face_capture_images(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        channel=6,
        start=datetime(2026, 8, 7, 9),
        end=datetime(2026, 8, 7, 10),
        page=2,
        page_size=12,
    )

    assert captured["operation"] == "faceCaptureBatch"
    assert captured["credentials"] == {
        "ip": "192.0.2.10",
        "port": 6036,
        "username": "operator",
        "password": "secret",
    }
    assert result.complete is True
    assert result.page == 2
    assert result.items[0].image == image
    assert result.items[0].capture.captured_at_device == datetime(2026, 8, 7, 10, 0, 0, 123456)


def test_typed_face_batch_rejects_mismatched_page() -> None:
    class Client(SyncRuntimeClient):
        def execute(self, _job):
            return {
                "success": True,
                "captures": [],
                "complete": False,
                "page": 2,
            }

    with pytest.raises(RuntimeClientError, match="invalid face batch"):
        Client().search_face_capture_images(
            "192.0.2.10",
            "operator",
            "secret",
            port=6036,
            channel=6,
            start=datetime(2026, 8, 7, 9),
            end=datetime(2026, 8, 7, 10),
            page=1,
        )


def _platform_snapshot() -> dict:
    return {
        "capabilities": {"resources": True, "servers": True},
        "fetch_status": {
            "resources": "ok",
            "devices": "ok",
            "channels": "ok",
            "areas": "ok",
            "servers": "ok",
            "alarm_zones": "unavailable",
            "alarm_events": "failed",
        },
        "sites": [{"id": "site-1"}],
        "devices": [{"id": "nvr-1"}],
        "channels": [{"id": "camera-1"}],
        "servers": [{"id": "server-1"}],
        "alarm_zones": [],
        "alarm_events": [],
        "health": [{"id": "nvr-1", "status": "ONLINE"}],
        "summary": {"site_count": 1, "device_count": 1},
    }


def test_typed_platform_inventory_owns_job_schema_and_validates_result() -> None:
    captured: dict = {}

    class Client(SyncRuntimeClient):
        def execute(self, job, **kwargs):
            captured.update(job)
            captured["timeout_ms"] = kwargs.get("timeout_ms")
            return _platform_snapshot()

    result = Client().get_platform_inventory("nvms.example", "operator", "secret")

    assert captured == {
        "sdkFamily": "platform",
        "operation": "inventorySnapshot",
        "credentials": {
            "host": "nvms.example",
            "port": 6003,
            "username": "operator",
            "password": "secret",
        },
        "timeout_ms": 60_000,
    }
    assert result.summary == {"site_count": 1, "device_count": 1}
    assert result.devices == ({"id": "nvr-1"},)
    assert result.fetch_status["alarm_events"] == "failed"
    assert result.as_dict() == _platform_snapshot()


def test_typed_platform_inventory_rejects_missing_fetch_status() -> None:
    class Client(SyncRuntimeClient):
        def execute(self, _job, **_kwargs):
            snapshot = _platform_snapshot()
            del snapshot["fetch_status"]["servers"]
            return snapshot

    with pytest.raises(RuntimeClientError, match="invalid platform inventory snapshot"):
        Client().get_platform_inventory("nvms.example", "operator", "secret")


def test_typed_platform_authority_owns_job_schema_and_validates_result() -> None:
    captured: dict = {}
    snapshot = {
        "fetch_status": {
            "permission_groups": "ok",
            "users": "ok",
            "permission_entries": "ok",
            "area_permissions": "unavailable",
            "user_sessions": "ok",
        },
        "permission_groups": [{"guid": "group-1"}],
        "users": [{"guid": "user-1"}],
        "permission_entries": [],
        "area_permissions": [],
        "user_sessions": [],
    }

    class Client(SyncRuntimeClient):
        def execute(self, job, **kwargs):
            captured.update(job)
            captured["timeout_ms"] = kwargs.get("timeout_ms")
            return snapshot

    result = Client().get_platform_authority("nvms.example", "operator", "secret")

    assert captured["sdkFamily"] == "platform"
    assert captured["operation"] == "authoritySnapshot"
    assert captured["timeout_ms"] == 60_000
    assert result.users == ({"guid": "user-1"},)
    assert result.as_dict() == snapshot


def test_typed_platform_authority_rejects_partial_result() -> None:
    class Client(SyncRuntimeClient):
        def execute(self, _job, **_kwargs):
            return {"users": []}

    with pytest.raises(RuntimeClientError, match="invalid platform authority snapshot"):
        Client().get_platform_authority("nvms.example", "operator", "secret")


def test_runtime_request_rejects_out_of_range_timeout_before_socket_io() -> None:
    with pytest.raises(ValueError, match="between 1000 and 60000"):
        SyncRuntimeClient().execute({}, timeout_ms=999)


def _plate_event_result() -> dict:
    return {
        "events": [
            {
                "user_id": 7,
                "channel_id": 6,
                "source": "nvr",
                "received_at": "2026-08-09T12:00:00.123456+00:00",
                "occurred_at": "2026-08-09T11:59:59+00:00",
                "source_event_id": "501",
                "plate": "ABC123",
                "declared_plate_char_count": 6,
                "source_encryption_version": 3,
                "confidence": 96,
                "char_confidences": [],
                "direction": "unknown",
                "plate_rect": [10, 20, 110, 60],
                "plate_size": [100, 40],
                "channel_guid": "channel-guid",
                "edge_match": "stranger",
                "edge_match_code": 2,
                "plate_color": "black",
                "plate_color_code": 2,
                "plate_brightness": 73,
                "plate_color_confidence": 91,
                "vehicle_type": "sedan",
                "vehicle_type_code": 1,
                "vehicle_color": "blue",
                "vehicle_color_code": 5,
                "vehicle_brand_code": 88,
                "source_end_at": "2026-08-09T12:00:01+00:00",
                "full_image_base64": base64.b64encode(b"full-jpeg").decode(),
                "plate_image_base64": base64.b64encode(b"plate-jpeg").decode(),
                "full_image_format": "jpeg",
                "plate_image_format": "jpeg",
                "full_image_size": [1920, 1080],
                "is_partial": False,
                "warnings": [],
            }
        ],
        "stats": {
            "callbacks_received": 1,
            "events_parsed": 1,
            "events_dropped": 0,
            "malformed_payloads": 0,
            "rejected_callbacks": 0,
            "ignored_commands": 0,
            "buffered_events": 0,
            "buffered_image_bytes": 0,
            "last_error": None,
        },
    }


def test_typed_plate_stream_owns_lifecycle_and_returns_plate_events() -> None:
    jobs: list[dict] = []

    class Client(SyncRuntimeClient):
        def execute(self, job, **_kwargs):
            jobs.append(job)
            if job["operation"] == "plateStreamStart":
                return {
                    "stream_id": "stream-1",
                    "subscriptions": [{"source": "nvr", "channel_id": 6}],
                    "stats": _plate_event_result()["stats"],
                }
            if job["operation"] == "plateStreamPoll":
                return _plate_event_result()
            if job["operation"] == "plateStreamStop":
                return {"stopped": True}
            raise AssertionError("unexpected operation")

    with Client().subscribe_plate_events(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        channels=(6,),
        source=PlateSource.NVR,
        max_events=32,
        max_payload_bytes=1024 * 1024,
        max_image_bytes=512 * 1024,
        max_buffer_bytes=4 * 1024 * 1024,
        stream_id="stream-1",
    ) as stream:
        event = stream.get(timeout=0.25)
        assert event.plate == "ABC123"
        assert event.received_at == datetime(2026, 8, 9, 12, tzinfo=timezone.utc).replace(microsecond=123456)
        assert event.full_image == b"full-jpeg"
        assert event.plate_image == b"plate-jpeg"
        assert stream.stats().events_parsed == 1
        assert stream.subscriptions[0].channel_id == 6
        assert stream.drain(limit=0) == []

    assert [job["operation"] for job in jobs] == [
        "plateStreamStart",
        "plateStreamPoll",
        "plateStreamStop",
    ]
    assert jobs[0]["source"] == "nvr"
    assert jobs[0]["streamId"] == "stream-1"
    assert jobs[0]["channels"] == [6]
    assert jobs[1]["waitMilliseconds"] == 250


def test_typed_plate_stream_acknowledges_only_after_explicit_consumer_ack() -> None:
    jobs: list[dict] = []

    class Client(SyncRuntimeClient):
        def execute(self, job, **_kwargs):
            jobs.append(job)
            if job["operation"] == "plateStreamStart":
                return {
                    "stream_id": "stream-1",
                    "delivery_mode": "acked",
                    "subscriptions": [{"source": "nvr", "channel_id": 6}],
                    "stats": _plate_event_result()["stats"],
                }
            if job["operation"] == "plateStreamPoll":
                result = _plate_event_result()
                result["delivery_sequence"] = 1
                return result
            if job["operation"] == "plateStreamAck":
                return {
                    "acked_sequence": job["ackSequence"],
                    "stats": _plate_event_result()["stats"],
                }
            if job["operation"] == "plateStreamStop":
                return {"stopped": True}
            raise AssertionError("unexpected operation")

    with Client().subscribe_plate_events(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        channels=(6,),
        source=PlateSource.NVR,
        stream_id="stream-1",
        acknowledged_delivery=True,
    ) as stream:
        first = stream.get(timeout=0)
        replay = stream.get(timeout=0)
        assert replay == first
        stream.ack()
        with pytest.raises(RuntimeError, match="no delivery"):
            stream.ack()

    assert jobs[0]["deliveryMode"] == "acked"
    assert jobs[3]["operation"] == "plateStreamAck"
    assert jobs[3]["ackSequence"] == 1


def test_typed_plate_stream_rejects_replacement_before_ack() -> None:
    poll_count = 0

    class Client(SyncRuntimeClient):
        def execute(self, job, **_kwargs):
            nonlocal poll_count
            if job["operation"] == "plateStreamStart":
                return {
                    "stream_id": "stream-1",
                    "delivery_mode": "acked",
                    "subscriptions": [{"source": "nvr", "channel_id": 6}],
                    "stats": _plate_event_result()["stats"],
                }
            if job["operation"] == "plateStreamPoll":
                poll_count += 1
                result = _plate_event_result()
                result["delivery_sequence"] = poll_count
                return result
            return {"stopped": True}

    stream = Client().subscribe_plate_events(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        channels=(6,),
        stream_id="stream-1",
        acknowledged_delivery=True,
    )
    try:
        stream.get(timeout=0)
        with pytest.raises(RuntimeClientError, match="replaced an unacknowledged"):
            stream.get(timeout=0)
    finally:
        stream.close()


def test_typed_plate_stream_requires_runtime_ack_mode_negotiation() -> None:
    jobs: list[dict] = []

    class Client(SyncRuntimeClient):
        def execute(self, job, **_kwargs):
            jobs.append(job)
            if job["operation"] == "plateStreamStop":
                return {"stopped": True}
            assert job["deliveryMode"] == "acked"
            return {
                "stream_id": "stream-1",
                "subscriptions": [{"source": "nvr", "channel_id": 6}],
                "stats": _plate_event_result()["stats"],
            }

    with pytest.raises(RuntimeClientError, match="invalid plate stream response"):
        Client().subscribe_plate_events(
            "192.0.2.10",
            "operator",
            "secret",
            port=6036,
            channels=(6,),
            stream_id="stream-1",
            acknowledged_delivery=True,
        )
    assert [job["operation"] for job in jobs] == ["plateStreamStart", "plateStreamStop"]


def test_acknowledged_stream_exception_detaches_and_can_reattach() -> None:
    jobs: list[dict] = []

    class Client(SyncRuntimeClient):
        def execute(self, job, **_kwargs):
            jobs.append(job)
            if job["operation"] == "plateStreamStart":
                return {
                    "stream_id": "stream-1",
                    "delivery_mode": "acked",
                    "subscriptions": [{"source": "nvr", "channel_id": 6}],
                    "stats": _plate_event_result()["stats"],
                }
            if job["operation"] == "plateStreamPoll":
                result = _plate_event_result()
                result["delivery_sequence"] = 1
                return result
            if job["operation"] == "plateStreamAck":
                return {
                    "acked_sequence": 1,
                    "stats": _plate_event_result()["stats"],
                }
            return {"stopped": True}

    client = Client()
    with pytest.raises(ValueError, match="persistence failed"):
        with client.subscribe_plate_events(
            "192.0.2.10",
            "operator",
            "secret",
            port=6036,
            channels=(6,),
            stream_id="stream-1",
            acknowledged_delivery=True,
        ) as stream:
            stream.get(timeout=0)
            raise ValueError("persistence failed")

    assert "plateStreamStop" not in [job["operation"] for job in jobs]

    with client.subscribe_plate_events(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        channels=(6,),
        stream_id="stream-1",
        acknowledged_delivery=True,
    ) as reattached:
        assert reattached.get(timeout=0).plate == "ABC123"
        reattached.ack()

    assert [job["operation"] for job in jobs][-4:] == [
        "plateStreamStart",
        "plateStreamPoll",
        "plateStreamAck",
        "plateStreamStop",
    ]


def test_acknowledged_stream_malformed_poll_detaches_conservatively() -> None:
    jobs: list[dict] = []

    class Client(SyncRuntimeClient):
        def execute(self, job, **_kwargs):
            jobs.append(job)
            if job["operation"] == "plateStreamStart":
                return {
                    "stream_id": "stream-1",
                    "delivery_mode": "acked",
                    "subscriptions": [{"source": "nvr", "channel_id": 6}],
                    "stats": _plate_event_result()["stats"],
                }
            if job["operation"] == "plateStreamPoll":
                return {"invalid": True}
            return {"stopped": True}

    with pytest.raises(RuntimeClientError, match="invalid plate stream response"):
        with Client().subscribe_plate_events(
            "192.0.2.10",
            "operator",
            "secret",
            port=6036,
            channels=(6,),
            stream_id="stream-1",
            acknowledged_delivery=True,
        ) as stream:
            stream.get(timeout=0)

    assert [job["operation"] for job in jobs] == [
        "plateStreamStart",
        "plateStreamPoll",
    ]


def test_acknowledged_stream_requires_explicit_stable_id() -> None:
    with pytest.raises(ValueError, match="explicit stable stream_id"):
        SyncRuntimeClient().subscribe_plate_events(
            "192.0.2.10",
            "operator",
            "secret",
            port=6036,
            channels=(6,),
            acknowledged_delivery=True,
        )


def test_typed_plate_stream_empty_poll_raises_queue_empty() -> None:
    class Client(SyncRuntimeClient):
        def execute(self, job, **_kwargs):
            if job["operation"] == "plateStreamStart":
                return {
                    "stream_id": "stream-1",
                    "subscriptions": [{"source": "nvr", "channel_id": 6}],
                    "stats": _plate_event_result()["stats"],
                }
            if job["operation"] == "plateStreamPoll":
                result = _plate_event_result()
                result["events"] = []
                return result
            return {"stopped": True}

    stream = Client().subscribe_plate_events(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        channels=(6,),
        source=PlateSource.NVR,
        stream_id="stream-1",
    )
    try:
        with pytest.raises(queue.Empty):
            stream.get(timeout=0)
    finally:
        stream.close()


def test_typed_plate_stream_rejects_malformed_event() -> None:
    class Client(SyncRuntimeClient):
        def execute(self, job, **_kwargs):
            if job["operation"] == "plateStreamStart":
                return {
                    "stream_id": "stream-1",
                    "subscriptions": [{"source": "nvr", "channel_id": 6}],
                    "stats": _plate_event_result()["stats"],
                }
            result = _plate_event_result()
            result["events"][0]["source"] = "invalid"
            return result

    stream = Client().subscribe_plate_events(
        "192.0.2.10",
        "operator",
        "secret",
        port=6036,
        channels=(6,),
        source=PlateSource.NVR,
        stream_id="stream-1",
    )
    with pytest.raises(RuntimeClientError, match="invalid plate stream response"):
        stream.get(timeout=0)
