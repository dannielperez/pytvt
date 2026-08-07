"""Typed client for the local process-isolated pytvt Unix-socket runtime."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import socket
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

RUNTIME_PROTOCOL_VERSION = 1
DEFAULT_RUNTIME_SOCKET_PATH = Path("/run/pytvt-runtime/runtime.sock")
DEFAULT_RUNTIME_TIMEOUT_MS = 30_000
DEFAULT_PLATFORM_RUNTIME_TIMEOUT_MS = 60_000
MAX_RUNTIME_REQUEST_BYTES = 64 * 1024
# The runtime caps a PlatformSDK worker/server response at 32 MiB. Leave a
# fixed envelope margin so a maximum-sized result still fits the framed reply.
MAX_RUNTIME_RESPONSE_BYTES = 34 * 1024 * 1024
MAX_FACE_BATCH_ITEMS = 100
MAX_FACE_BATCH_BYTES = 12 * 1024 * 1024
PLATFORM_FETCH_SECTIONS = frozenset(
    {"resources", "devices", "channels", "areas", "servers", "alarm_zones", "alarm_events"}
)
PLATFORM_SNAPSHOT_LISTS = (
    "sites",
    "devices",
    "channels",
    "servers",
    "alarm_zones",
    "alarm_events",
    "health",
)


class RuntimeClientError(RuntimeError):
    """The local runtime exchange or protocol validation failed."""


class RuntimeRemoteError(RuntimeClientError):
    """The runtime returned a typed operation failure."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.kind = kind
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


@dataclass(frozen=True)
class RuntimeFaceCapture:
    channel_index: int
    channel_deleted: bool
    captured_at_device: datetime
    device_time_ticks: int
    snapshot_image_id: int
    target_image_id: int


@dataclass(frozen=True)
class RuntimeFaceBatchItem:
    capture: RuntimeFaceCapture
    image: bytes
    error: str | None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.image)


@dataclass(frozen=True)
class RuntimeFaceBatchResult:
    items: tuple[RuntimeFaceBatchItem, ...]
    complete: bool
    page: int

    @property
    def success(self) -> bool:
        return True

    @property
    def error(self) -> None:
        return None


@dataclass(frozen=True)
class RuntimePlatformInventoryResult:
    """Validated JSON-safe inventory returned by the persistent Server SDK session."""

    capabilities: dict[str, bool]
    fetch_status: dict[str, str]
    sites: tuple[dict[str, Any], ...]
    devices: tuple[dict[str, Any], ...]
    channels: tuple[dict[str, Any], ...]
    servers: tuple[dict[str, Any], ...]
    alarm_zones: tuple[dict[str, Any], ...]
    alarm_events: tuple[dict[str, Any], ...]
    health: tuple[dict[str, Any], ...]
    summary: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        """Return the established JSON snapshot shape for existing consumers."""
        return {
            "capabilities": dict(self.capabilities),
            "fetch_status": dict(self.fetch_status),
            "sites": list(self.sites),
            "devices": list(self.devices),
            "channels": list(self.channels),
            "servers": list(self.servers),
            "alarm_zones": list(self.alarm_zones),
            "alarm_events": list(self.alarm_events),
            "health": list(self.health),
            "summary": dict(self.summary),
        }


class RuntimeClient:
    """Asynchronous client with one absolute deadline per socket exchange."""

    def __init__(
        self,
        *,
        socket_path: Path = DEFAULT_RUNTIME_SOCKET_PATH,
        timeout_ms: int = DEFAULT_RUNTIME_TIMEOUT_MS,
    ) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_ms / 1000

    async def health(self) -> dict[str, Any]:
        result = await self._request("health")
        if not isinstance(result, dict):
            raise RuntimeClientError("runtime returned an invalid health result")
        return result

    async def execute(self, job: dict[str, Any], *, timeout_ms: int | None = None) -> Any:
        return await self._request("execute", job=job, timeout_ms=timeout_ms)

    async def get_platform_inventory(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 6003,
        timeout_ms: int = DEFAULT_PLATFORM_RUNTIME_TIMEOUT_MS,
    ) -> RuntimePlatformInventoryResult:
        """Read one NVMS inventory snapshot through a persistent PlatformSDK session."""
        result = await self.execute(
            _platform_inventory_job(host, port, username, password),
            timeout_ms=timeout_ms,
        )
        return _parse_platform_inventory(result)

    async def _request(
        self,
        method: str,
        *,
        job: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> Any:
        request_id = uuid.uuid4().hex
        payload = _request_payload(request_id, method, job)
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(_request_timeout_seconds(timeout_ms, self.timeout_seconds)):
                reader, writer = await asyncio.open_unix_connection(
                    self.socket_path,
                    limit=MAX_RUNTIME_RESPONSE_BYTES,
                )
                writer.write(payload)
                await writer.drain()
                response_bytes = await reader.readuntil(b"\n")
        except TimeoutError as exc:
            raise RuntimeClientError("runtime request timed out") from exc
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            raise RuntimeClientError("runtime socket exchange failed") from exc
        finally:
            if writer is not None:
                writer.close()
                with suppress(BrokenPipeError, ConnectionResetError):
                    await writer.wait_closed()
        return _parse_response(response_bytes, request_id)


class SyncRuntimeClient:
    """Blocking client for ordinary application and worker call sites."""

    def __init__(
        self,
        *,
        socket_path: Path = DEFAULT_RUNTIME_SOCKET_PATH,
        timeout_ms: int = DEFAULT_RUNTIME_TIMEOUT_MS,
    ) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_ms / 1000

    def health(self) -> dict[str, Any]:
        result = self._request("health")
        if not isinstance(result, dict):
            raise RuntimeClientError("runtime returned an invalid health result")
        return result

    def execute(self, job: dict[str, Any], *, timeout_ms: int | None = None) -> Any:
        return self._request("execute", job=job, timeout_ms=timeout_ms)

    def get_platform_inventory(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 6003,
        timeout_ms: int = DEFAULT_PLATFORM_RUNTIME_TIMEOUT_MS,
    ) -> RuntimePlatformInventoryResult:
        """Read one NVMS inventory snapshot through a persistent PlatformSDK session."""
        result = self.execute(
            _platform_inventory_job(host, port, username, password),
            timeout_ms=timeout_ms,
        )
        return _parse_platform_inventory(result)

    def search_face_capture_images(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int,
        channel: int,
        start: datetime,
        end: datetime,
        page: int = 1,
        page_size: int = 100,
    ) -> RuntimeFaceBatchResult:
        """Search and copy one bounded face page in one persistent session."""
        result = self.execute(
            {
                "operation": "faceCaptureBatch",
                "credentials": {
                    "ip": host,
                    "port": port,
                    "username": username,
                    "password": password,
                },
                "channel": channel,
                "start": start.isoformat(timespec="microseconds"),
                "end": end.isoformat(timespec="microseconds"),
                "page": page,
                "pageSize": page_size,
            }
        )
        return _parse_face_batch(result, expected_page=page, max_items=page_size)

    def _request(
        self,
        method: str,
        *,
        job: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> Any:
        request_id = uuid.uuid4().hex
        payload = _request_payload(request_id, method, job)
        deadline = time.monotonic() + _request_timeout_seconds(timeout_ms, self.timeout_seconds)

        def remaining() -> float:
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError("runtime request deadline expired")
            return seconds

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(remaining())
                connection.connect(str(self.socket_path))
                connection.settimeout(remaining())
                connection.sendall(payload)
                chunks: list[bytes] = []
                received = 0
                while True:
                    connection.settimeout(remaining())
                    chunk = connection.recv(min(64 * 1024, MAX_RUNTIME_RESPONSE_BYTES + 1 - received))
                    if not chunk:
                        raise RuntimeClientError("runtime response is incomplete")
                    newline = chunk.find(b"\n")
                    if newline >= 0:
                        chunks.append(chunk[: newline + 1])
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                    if received > MAX_RUNTIME_RESPONSE_BYTES:
                        raise RuntimeClientError("runtime response is too large")
        except (OSError, TimeoutError) as exc:
            raise RuntimeClientError("runtime socket exchange failed") from exc
        return _parse_response(b"".join(chunks), request_id)


def _request_payload(
    request_id: str,
    method: str,
    job: dict[str, Any] | None,
) -> bytes:
    request: dict[str, Any] = {
        "protocol": RUNTIME_PROTOCOL_VERSION,
        "id": request_id,
        "method": method,
    }
    if job is not None:
        request["job"] = job
    payload = json.dumps(request, separators=(",", ":")).encode()
    if len(payload) > MAX_RUNTIME_REQUEST_BYTES:
        raise RuntimeClientError("runtime request exceeds configured byte limit")
    return payload + b"\n"


def _request_timeout_seconds(timeout_ms: int | None, fallback: float) -> float:
    if timeout_ms is None:
        return fallback
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1_000 <= timeout_ms <= 60_000:
        raise ValueError("runtime request timeout must be between 1000 and 60000 milliseconds")
    return timeout_ms / 1000


def _platform_inventory_job(host: str, port: int, username: str, password: str) -> dict[str, Any]:
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host is required")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
        raise ValueError("port is invalid")
    if not isinstance(username, str) or not username:
        raise ValueError("username is required")
    if not isinstance(password, str) or not password:
        raise ValueError("password is required")
    return {
        "sdkFamily": "platform",
        "operation": "inventorySnapshot",
        "credentials": {
            "host": host.strip(),
            "port": port,
            "username": username,
            "password": password,
        },
    }


def _parse_response(response_bytes: bytes, request_id: str) -> Any:
    try:
        response = json.loads(response_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeClientError("runtime returned invalid JSON") from exc
    if (
        not isinstance(response, dict)
        or response.get("protocol") != RUNTIME_PROTOCOL_VERSION
        or response.get("id") != request_id
        or not isinstance(response.get("ok"), bool)
    ):
        raise RuntimeClientError("runtime returned an invalid response envelope")
    if response["ok"]:
        return response.get("result")
    error = response.get("error")
    if not isinstance(error, dict):
        raise RuntimeClientError("runtime returned an invalid error envelope")
    kind = error.get("kind")
    message = error.get("message")
    if not isinstance(kind, str) or not isinstance(message, str):
        raise RuntimeClientError("runtime returned an invalid error envelope")
    retry_after = response.get("retry_after_seconds")
    raise RuntimeRemoteError(
        kind,
        message,
        retry_after_seconds=retry_after if isinstance(retry_after, int) else None,
    )


def _parse_face_batch(
    result: Any,
    *,
    expected_page: int,
    max_items: int,
) -> RuntimeFaceBatchResult:
    try:
        if not isinstance(result, dict) or result.get("success") is not True:
            raise TypeError
        captures = result["captures"]
        complete = result["complete"]
        page = result["page"]
        if (
            not isinstance(captures, list)
            or not isinstance(complete, bool)
            or isinstance(page, bool)
            or page != expected_page
            or not 1 <= max_items <= MAX_FACE_BATCH_ITEMS
            or len(captures) > max_items
        ):
            raise TypeError
        items = tuple(_parse_face_item(item) for item in captures)
        if sum(len(item.image) for item in items) > MAX_FACE_BATCH_BYTES:
            raise ValueError
    except (binascii.Error, KeyError, TypeError, ValueError):
        raise RuntimeClientError("runtime returned an invalid face batch") from None
    return RuntimeFaceBatchResult(items=items, complete=complete, page=page)


def _parse_face_item(raw: Any) -> RuntimeFaceBatchItem:
    if not isinstance(raw, dict):
        raise TypeError
    encoded = raw.get("image_base64")
    image = base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else b""
    if image and not image.startswith(b"\xff\xd8"):
        raise ValueError
    captured_at = datetime.fromisoformat(raw["captured_at_device"])
    if captured_at.utcoffset() is not None:
        raise ValueError
    integers = (
        raw["channel_index"],
        raw["device_time_ticks"],
        raw["snapshot_image_id"],
        raw["target_image_id"],
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in integers):
        raise TypeError
    if not isinstance(raw["channel_deleted"], bool):
        raise TypeError
    error = raw.get("image_error")
    if error is not None and (not isinstance(error, str) or len(error) > 500):
        raise TypeError
    return RuntimeFaceBatchItem(
        capture=RuntimeFaceCapture(
            channel_index=raw["channel_index"],
            channel_deleted=raw["channel_deleted"],
            captured_at_device=captured_at,
            device_time_ticks=raw["device_time_ticks"],
            snapshot_image_id=raw["snapshot_image_id"],
            target_image_id=raw["target_image_id"],
        ),
        image=image,
        error=error,
    )


def _parse_platform_inventory(result: Any) -> RuntimePlatformInventoryResult:
    try:
        if not isinstance(result, dict):
            raise TypeError
        capabilities = result["capabilities"]
        fetch_status = result["fetch_status"]
        summary = result["summary"]
        if not isinstance(capabilities, dict) or any(
            not isinstance(key, str) or not isinstance(value, bool) for key, value in capabilities.items()
        ):
            raise TypeError
        if (
            not isinstance(fetch_status, dict)
            or set(fetch_status) != PLATFORM_FETCH_SECTIONS
            or any(value not in {"ok", "unavailable", "failed"} for value in fetch_status.values())
        ):
            raise TypeError
        if not isinstance(summary, dict) or any(
            not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int) or value < 0
            for key, value in summary.items()
        ):
            raise TypeError
        lists: dict[str, tuple[dict[str, Any], ...]] = {}
        for name in PLATFORM_SNAPSHOT_LISTS:
            value = result[name]
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise TypeError
            lists[name] = tuple(value)
    except (KeyError, TypeError, ValueError):
        raise RuntimeClientError("runtime returned an invalid platform inventory snapshot") from None
    return RuntimePlatformInventoryResult(
        capabilities=dict(capabilities),
        fetch_status=dict(fetch_status),
        sites=lists["sites"],
        devices=lists["devices"],
        channels=lists["channels"],
        servers=lists["servers"],
        alarm_zones=lists["alarm_zones"],
        alarm_events=lists["alarm_events"],
        health=lists["health"],
        summary=dict(summary),
    )
