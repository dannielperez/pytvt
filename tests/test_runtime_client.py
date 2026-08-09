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
    ) as stream:
        event = stream.get(timeout=0.25)
        assert event.plate == "ABC123"
        assert event.received_at == datetime(2026, 8, 9, 12, tzinfo=timezone.utc).replace(microsecond=123456)
        assert event.full_image == b"full-jpeg"
        assert event.plate_image == b"plate-jpeg"
        assert stream.stats().events_parsed == 1
        assert stream.subscriptions[0].channel_id == 6

    assert [job["operation"] for job in jobs] == [
        "plateStreamStart",
        "plateStreamPoll",
        "plateStreamStop",
    ]
    assert jobs[0]["source"] == "nvr"
    assert jobs[0]["channels"] == [6]
    assert jobs[1]["waitMilliseconds"] == 250


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
    )
    with pytest.raises(RuntimeClientError, match="invalid plate stream response"):
        stream.get(timeout=0)
