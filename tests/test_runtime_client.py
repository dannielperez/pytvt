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
