"""Typed contracts for the process-isolated Unix runtime client."""

from __future__ import annotations

import base64
import json
from datetime import datetime

import pytest

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
