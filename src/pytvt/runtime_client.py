"""Typed client for the local process-isolated pytvt Unix-socket runtime."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import queue
import socket
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .device_sdk import (
    EdgePlateMatch,
    ImageFormat,
    PlateColor,
    PlateEvent,
    PlateSource,
    PlateStreamStats,
    PlateSubscriptionInfo,
    VehicleColor,
    VehicleDirection,
    VehicleType,
)

RUNTIME_PROTOCOL_VERSION = 1
DEFAULT_RUNTIME_SOCKET_PATH = Path("/run/pytvt-runtime/runtime.sock")
DEFAULT_RUNTIME_TIMEOUT_MS = 30_000
DEFAULT_PLATFORM_RUNTIME_TIMEOUT_MS = 60_000
MAX_RUNTIME_REQUEST_BYTES = 64 * 1024
# The runtime caps a PlatformSDK worker/server response at 32 MiB. Leave a
# fixed envelope margin so a maximum-sized result still fits the framed reply.
MAX_RUNTIME_RESPONSE_BYTES = 34 * 1024 * 1024
MAX_RUNTIME_SNAPSHOT_BYTES = 25 * 1024 * 1024
MAX_RUNTIME_RTSP_URL_BYTES = 4096
MAX_FACE_BATCH_ITEMS = 100
MAX_FACE_BATCH_BYTES = 12 * 1024 * 1024
MAX_RUNTIME_PLATE_CHANNELS = 32
MAX_RUNTIME_PLATE_POLL_ITEMS = 16
MAX_RUNTIME_DELIVERY_SEQUENCE = (1 << 63) - 1
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
PLATFORM_AUTHORITY_LISTS = (
    "permission_groups",
    "users",
    "permission_entries",
    "area_permissions",
    "user_sessions",
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
class RuntimeDeviceInfo:
    """Validated recorder identity returned through a persistent login."""

    device_name: str
    device_model: str
    serial_number: str
    firmware: str
    hardware_version: str
    kernel_version: str
    mcu_version: str
    video_inputs: int
    audio_inputs: int
    sensor_inputs: int
    sensor_outputs: int
    device_type: int


@dataclass(frozen=True)
class RuntimeChannel:
    """One validated NVR channel returned by the persistent runtime."""

    channel: int
    name: str
    address: str
    port: int
    http_port: int
    online: bool
    protocol: str
    model: str


@dataclass(frozen=True)
class RuntimeChannelScan:
    """One bounded channel-status snapshot from a logged-in recorder."""

    device_name: str
    device_model: str
    serial_number: str
    firmware: str
    channels: tuple[RuntimeChannel, ...]


@dataclass(frozen=True)
class RuntimeSnapshot:
    """One bounded JPEG captured through the persistent native runtime."""

    image: bytes
    channel: int
    method: str = "runtime"


@dataclass(frozen=True)
class RuntimeRtspUrl:
    """One validated credential-bearing RTSP URL from the native runtime."""

    url: str
    channel: int
    stream_type: int
    method: str = "runtime"


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


@dataclass(frozen=True)
class RuntimePlatformAuthorityResult:
    """Validated authority data returned by the persistent Server SDK session."""

    fetch_status: dict[str, str]
    permission_groups: tuple[dict[str, Any], ...]
    users: tuple[dict[str, Any], ...]
    permission_entries: tuple[dict[str, Any], ...]
    area_permissions: tuple[dict[str, Any], ...]
    user_sessions: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "fetch_status": dict(self.fetch_status),
            **{name: list(getattr(self, name)) for name in PLATFORM_AUTHORITY_LISTS},
        }


class RuntimePlateEventStream:
    """Typed proxy for one plate subscription owned by the persistent runtime."""

    def __init__(
        self,
        *,
        client: SyncRuntimeClient,
        credentials: dict[str, Any],
        stream_id: str,
        subscriptions: tuple[PlateSubscriptionInfo, ...],
        stats: PlateStreamStats,
        acknowledged_delivery: bool,
    ) -> None:
        self._client = client
        self._credentials = credentials
        self._stream_id = stream_id
        self._subscriptions = subscriptions
        self._stats = stats
        self._acknowledged_delivery = acknowledged_delivery
        self._pending_sequence: int | None = None
        self._delivery_state_uncertain = False
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def subscriptions(self) -> tuple[PlateSubscriptionInfo, ...]:
        return self._subscriptions

    @property
    def acknowledged_delivery(self) -> bool:
        """Whether the runtime retains each event until an explicit ack."""
        return self._acknowledged_delivery

    def get(self, timeout: float | None = None) -> PlateEvent:
        events = self._poll(limit=1, timeout=timeout)
        if not events:
            raise queue.Empty
        return events[0]

    def get_nowait(self) -> PlateEvent:
        return self.get(timeout=0)

    def drain(self, *, limit: int = 16) -> list[PlateEvent]:
        limit = max(0, min(limit, MAX_RUNTIME_PLATE_POLL_ITEMS))
        if limit == 0:
            return []
        if self._acknowledged_delivery:
            limit = 1
        return self._poll(limit=limit, timeout=0)

    def stats(self) -> PlateStreamStats:
        return self._stats

    def ack(self) -> None:
        """Acknowledge the current event after the consumer persists it.

        Acknowledgements are explicit rather than coupled to :meth:`get`, so a
        database failure or client disconnect leaves the runtime delivery
        available for replay.
        """
        if self._closed:
            raise RuntimeError("plate-event stream is closed")
        if not self._acknowledged_delivery:
            raise RuntimeError("plate-event stream does not use acknowledged delivery")
        sequence = self._pending_sequence
        if sequence is None:
            raise RuntimeError("plate-event stream has no delivery to acknowledge")
        result = self._client.execute(
            {
                "operation": "plateStreamAck",
                "credentials": dict(self._credentials),
                "streamId": self._stream_id,
                "ackSequence": sequence,
            }
        )
        self._stats = _parse_plate_ack(result, expected_sequence=sequence)
        self._pending_sequence = None
        self._delivery_state_uncertain = False

    def _poll(self, *, limit: int, timeout: float | None) -> list[PlateEvent]:
        if self._closed:
            raise RuntimeError("plate-event stream is closed")
        if timeout is None:
            wait_ms = 10_000
        elif isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
            raise ValueError("plate stream timeout must be a non-negative number or None")
        else:
            wait_ms = min(10_000, int(timeout * 1000))
        if self._acknowledged_delivery:
            # Once the poll is sent, a disconnect or malformed response cannot
            # prove whether the runtime retained a delivery. Fail toward detach.
            self._delivery_state_uncertain = True
        result = self._client.execute(
            {
                "operation": "plateStreamPoll",
                "credentials": dict(self._credentials),
                "streamId": self._stream_id,
                "limit": limit,
                "waitMilliseconds": wait_ms,
            },
            timeout_ms=max(1_000, wait_ms + 1_000),
        )
        events, self._stats, delivery_sequence = _parse_plate_poll(
            result,
            max_items=limit,
            acknowledged_delivery=self._acknowledged_delivery,
        )
        if self._acknowledged_delivery:
            if self._pending_sequence is not None and delivery_sequence != self._pending_sequence:
                raise RuntimeClientError("runtime replaced an unacknowledged plate delivery")
            self._pending_sequence = delivery_sequence
            self._delivery_state_uncertain = delivery_sequence is not None
        return list(events)

    def close(self, *, discard_unacked: bool = False) -> None:
        """Close the proxy, preserving an unacknowledged delivery by default.

        An acknowledged stream with a pending event detaches locally so a
        stable stream id can reattach and replay it. Callers must opt in to
        ``discard_unacked=True`` when intentionally abandoning that evidence.
        """
        if self._closed:
            return
        if (
            self._acknowledged_delivery
            and (self._pending_sequence is not None or self._delivery_state_uncertain)
            and not discard_unacked
        ):
            self._closed = True
            return
        self._client.execute(
            {
                "operation": "plateStreamStop",
                "credentials": dict(self._credentials),
                "streamId": self._stream_id,
            }
        )
        self._closed = True

    def __enter__(self) -> RuntimePlateEventStream:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


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

    async def get_platform_authority(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 6003,
        timeout_ms: int = DEFAULT_PLATFORM_RUNTIME_TIMEOUT_MS,
    ) -> RuntimePlatformAuthorityResult:
        """Read NVMS authority data through the same persistent server session."""
        result = await self.execute(
            _platform_job("authoritySnapshot", host, port, username, password),
            timeout_ms=timeout_ms,
        )
        return _parse_platform_authority(result)

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

    def get_device_info(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        timeout_ms: int | None = None,
    ) -> RuntimeDeviceInfo:
        """Read recorder identity through its reusable authenticated session."""
        result = self._execute_device_operation(
            "deviceInfo",
            host,
            port,
            username,
            password,
            timeout_ms=timeout_ms,
        )
        return _parse_device_info(result)

    def probe_device_health(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        timeout_ms: int | None = None,
    ) -> RuntimeDeviceInfo:
        """Prove the persistent recorder session with a lightweight info read."""
        return self.get_device_info(
            host,
            username,
            password,
            port=port,
            timeout_ms=timeout_ms,
        )

    def scan_channels(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        max_channels: int = 64,
        timeout_ms: int | None = None,
    ) -> RuntimeChannelScan:
        """Read bounded per-channel status through the reusable session."""
        if isinstance(max_channels, bool) or not isinstance(max_channels, int) or not 1 <= max_channels <= 128:
            raise ValueError("max_channels must be between 1 and 128")
        result = self._execute_device_operation(
            "scan",
            host,
            port,
            username,
            password,
            timeout_ms=timeout_ms,
            maxCameras=max_channels,
        )
        return _parse_channel_scan(result, max_channels=max_channels)

    def capture_snapshot(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        channel: int = 0,
        timeout_ms: int | None = None,
    ) -> RuntimeSnapshot:
        """Capture one bounded JPEG through the reusable recorder session."""
        if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255:
            raise ValueError("channel must be between 0 and 255")
        result = self._execute_device_operation(
            "snapshot",
            host,
            port,
            username,
            password,
            timeout_ms=timeout_ms,
            channel=channel,
        )
        return RuntimeSnapshot(image=_parse_snapshot(result), channel=channel)

    def resolve_rtsp_url(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        channel: int = 0,
        stream_type: int = 0,
        timeout_ms: int | None = None,
    ) -> RuntimeRtspUrl:
        """Resolve one bounded RTSP URL through the reusable recorder session."""
        if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255:
            raise ValueError("channel must be between 0 and 255")
        if isinstance(stream_type, bool) or not isinstance(stream_type, int) or not 0 <= stream_type <= 2:
            raise ValueError("stream_type must be between 0 and 2")
        result = self._execute_device_operation(
            "rtspUrl",
            host,
            port,
            username,
            password,
            timeout_ms=timeout_ms,
            channel=channel,
            streamType=stream_type,
        )
        return RuntimeRtspUrl(
            url=_parse_rtsp_url(result),
            channel=channel,
            stream_type=stream_type,
        )

    def _execute_device_operation(
        self,
        operation: str,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        timeout_ms: int | None,
        **options: Any,
    ) -> Any:
        job = {
            "operation": operation,
            "credentials": _device_credentials(host, port, username, password),
            **options,
        }
        if timeout_ms is None:
            return self.execute(job)
        return self.execute(job, timeout_ms=timeout_ms)

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

    def get_platform_authority(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 6003,
        timeout_ms: int = DEFAULT_PLATFORM_RUNTIME_TIMEOUT_MS,
    ) -> RuntimePlatformAuthorityResult:
        """Read NVMS authority data through the same persistent server session."""
        result = self.execute(
            _platform_job("authoritySnapshot", host, port, username, password),
            timeout_ms=timeout_ms,
        )
        return _parse_platform_authority(result)

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

    def subscribe_plate_events(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int,
        channels: tuple[int, ...],
        source: PlateSource = PlateSource.NVR,
        max_events: int = 256,
        max_payload_bytes: int = 16 * 1024 * 1024,
        max_image_bytes: int = 8 * 1024 * 1024,
        max_buffer_bytes: int = 64 * 1024 * 1024,
        stream_id: str | None = None,
        acknowledged_delivery: bool = False,
    ) -> RuntimePlateEventStream:
        """Open one bounded plate stream in the persistent native runtime."""
        credentials = _device_credentials(host, port, username, password)
        normalized_channels = _plate_channels(channels)
        if source not in (PlateSource.NVR, PlateSource.IPC):
            raise ValueError("plate stream source is invalid")
        if not isinstance(acknowledged_delivery, bool):
            raise ValueError("acknowledged_delivery must be a boolean")
        _validate_plate_bounds(
            max_events=max_events,
            max_payload_bytes=max_payload_bytes,
            max_image_bytes=max_image_bytes,
            max_buffer_bytes=max_buffer_bytes,
        )
        if acknowledged_delivery and max_events < 2:
            raise ValueError("acknowledged delivery requires at least two event slots")
        if acknowledged_delivery and max_buffer_bytes <= max_payload_bytes:
            raise ValueError("acknowledged delivery requires buffer capacity above one payload")
        if acknowledged_delivery and stream_id is None:
            raise ValueError("acknowledged delivery requires an explicit stable stream_id")
        stream_id = stream_id or uuid.uuid4().hex
        if not isinstance(stream_id, str) or not stream_id or len(stream_id) > 128:
            raise ValueError("plate stream id must contain between 1 and 128 characters")
        start_job = {
            "operation": "plateStreamStart",
            "credentials": credentials,
            "channels": list(normalized_channels),
            "source": source.value,
            "streamId": stream_id,
            "maxEvents": max_events,
            "maxPayloadBytes": max_payload_bytes,
            "maxImageBytes": max_image_bytes,
            "maxBufferBytes": max_buffer_bytes,
        }
        if acknowledged_delivery:
            start_job["deliveryMode"] = "acked"
        result = self.execute(start_job)
        try:
            stream_id, subscriptions, stats = _parse_plate_start(
                result,
                expected_stream_id=stream_id,
                expected_delivery_mode="acked" if acknowledged_delivery else None,
            )
        except RuntimeClientError:
            # A legacy runtime may accept the start fields but omit negotiation
            # support. Compensate the already-open stream before failing closed.
            with suppress(RuntimeClientError):
                self.execute(
                    {
                        "operation": "plateStreamStop",
                        "credentials": credentials,
                        "streamId": stream_id,
                    }
                )
            raise
        return RuntimePlateEventStream(
            client=self,
            credentials=credentials,
            stream_id=stream_id,
            subscriptions=subscriptions,
            stats=stats,
            acknowledged_delivery=acknowledged_delivery,
        )

    def _request(
        self,
        method: str,
        *,
        job: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> Any:
        request_id = uuid.uuid4().hex
        request_timeout_seconds = _request_timeout_seconds(
            timeout_ms,
            self.timeout_seconds,
        )
        payload = _request_payload(
            request_id,
            method,
            job,
            timeout_ms=timeout_ms,
        )
        deadline = time.monotonic() + request_timeout_seconds

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
    *,
    timeout_ms: int | None = None,
) -> bytes:
    request: dict[str, Any] = {
        "protocol": RUNTIME_PROTOCOL_VERSION,
        "id": request_id,
        "method": method,
    }
    if job is not None:
        request["job"] = job
    if timeout_ms is not None:
        _request_timeout_seconds(timeout_ms, 0)
        request["timeoutMilliseconds"] = timeout_ms
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


def _device_credentials(host: str, port: int, username: str, password: str) -> dict[str, Any]:
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host is required")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
        raise ValueError("port is invalid")
    if not isinstance(username, str) or not username:
        raise ValueError("username is required")
    if not isinstance(password, str) or not password:
        raise ValueError("password is required")
    return {
        "ip": host.strip(),
        "port": port,
        "username": username,
        "password": password,
    }


def _plate_channels(channels: tuple[int, ...]) -> tuple[int, ...]:
    if not isinstance(channels, tuple) or not 1 <= len(channels) <= MAX_RUNTIME_PLATE_CHANNELS:
        raise ValueError("plate stream channels must contain between 1 and 32 items")
    if any(
        isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255 for channel in channels
    ):
        raise ValueError("plate stream channel is invalid")
    if len(set(channels)) != len(channels):
        raise ValueError("plate stream channels must be unique")
    return channels


def _validate_plate_bounds(
    *,
    max_events: int,
    max_payload_bytes: int,
    max_image_bytes: int,
    max_buffer_bytes: int,
) -> None:
    values = (max_events, max_payload_bytes, max_image_bytes, max_buffer_bytes)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError("plate stream bounds must be integers")
    if not 1 <= max_events <= 10_000:
        raise ValueError("max_events must be between 1 and 10000")
    if not 1 <= max_payload_bytes <= 64 * 1024 * 1024:
        raise ValueError("max_payload_bytes must be between 1 byte and 64 MiB")
    if not 1 <= max_image_bytes <= max_payload_bytes:
        raise ValueError("max_image_bytes must not exceed max_payload_bytes")
    if not 1 <= max_buffer_bytes <= 1024 * 1024 * 1024:
        raise ValueError("max_buffer_bytes must be between 1 byte and 1 GiB")


def _parse_plate_start(
    result: Any,
    *,
    expected_stream_id: str,
    expected_delivery_mode: str | None,
) -> tuple[str, tuple[PlateSubscriptionInfo, ...], PlateStreamStats]:
    try:
        if not isinstance(result, dict):
            raise TypeError
        stream_id = result["stream_id"]
        raw_subscriptions = result["subscriptions"]
        delivery_mode = result.get("delivery_mode")
        if (
            not isinstance(stream_id, str)
            or not stream_id
            or len(stream_id) > 128
            or stream_id != expected_stream_id
            or not isinstance(raw_subscriptions, list)
            or not 1 <= len(raw_subscriptions) <= MAX_RUNTIME_PLATE_CHANNELS
            or delivery_mode != expected_delivery_mode
        ):
            raise TypeError
        subscriptions = tuple(_parse_plate_subscription(item) for item in raw_subscriptions)
        stats = _parse_plate_stats(result["stats"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeClientError("runtime returned an invalid plate stream response") from None
    return stream_id, subscriptions, stats


def _parse_plate_poll(
    result: Any,
    *,
    max_items: int,
    acknowledged_delivery: bool,
) -> tuple[tuple[PlateEvent, ...], PlateStreamStats, int | None]:
    try:
        if not isinstance(result, dict):
            raise TypeError
        raw_events = result["events"]
        delivery_sequence = result.get("delivery_sequence")
        if (
            not isinstance(raw_events, list)
            or len(raw_events) > max_items
            or (acknowledged_delivery and len(raw_events) > 1)
            or (acknowledged_delivery and bool(raw_events) != (delivery_sequence is not None))
            or (
                delivery_sequence is not None
                and (
                    not acknowledged_delivery
                    or isinstance(delivery_sequence, bool)
                    or not isinstance(delivery_sequence, int)
                    or delivery_sequence < 1
                    or delivery_sequence > MAX_RUNTIME_DELIVERY_SEQUENCE
                )
            )
        ):
            raise TypeError
        events = tuple(_parse_plate_event(item) for item in raw_events)
        stats = _parse_plate_stats(result["stats"])
    except (binascii.Error, KeyError, TypeError, ValueError):
        raise RuntimeClientError("runtime returned an invalid plate stream response") from None
    return events, stats, delivery_sequence


def _parse_plate_ack(result: Any, *, expected_sequence: int) -> PlateStreamStats:
    try:
        if not isinstance(result, dict) or result["acked_sequence"] != expected_sequence:
            raise TypeError
        return _parse_plate_stats(result["stats"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeClientError("runtime returned an invalid plate stream response") from None


def _parse_plate_subscription(raw: Any) -> PlateSubscriptionInfo:
    if not isinstance(raw, dict) or set(raw) != {"source", "channel_id"}:
        raise TypeError
    channel_id = _required_nonnegative_int(raw["channel_id"])
    if channel_id > 255:
        raise ValueError
    return PlateSubscriptionInfo(source=PlateSource(raw["source"]), channel_id=channel_id)


def _parse_plate_stats(raw: Any) -> PlateStreamStats:
    integer_fields = (
        "callbacks_received",
        "events_parsed",
        "events_dropped",
        "malformed_payloads",
        "rejected_callbacks",
        "ignored_commands",
        "buffered_events",
        "buffered_image_bytes",
    )
    if not isinstance(raw, dict):
        raise TypeError
    values = {field: _required_nonnegative_int(raw[field]) for field in integer_fields}
    last_error = raw["last_error"]
    if last_error is not None and (not isinstance(last_error, str) or len(last_error) > 500):
        raise TypeError
    return PlateStreamStats(**values, last_error=last_error)


def _parse_plate_event(raw: Any) -> PlateEvent:
    if not isinstance(raw, dict):
        raise TypeError
    warnings = raw["warnings"]
    char_confidences = raw["char_confidences"]
    if (
        not isinstance(warnings, list)
        or any(not isinstance(value, str) or len(value) > 200 for value in warnings)
        or not isinstance(char_confidences, list)
        or any(_required_nonnegative_int(value) > 100 for value in char_confidences)
    ):
        raise TypeError
    plate = raw["plate"]
    source_event_id = raw["source_event_id"]
    channel_guid = raw["channel_guid"]
    if not isinstance(plate, str) or len(plate) > 64 or not isinstance(source_event_id, str):
        raise TypeError
    if channel_guid is not None and (not isinstance(channel_guid, str) or len(channel_guid) > 256):
        raise TypeError
    return PlateEvent(
        user_id=_required_nonnegative_int(raw["user_id"]),
        channel_id=_required_nonnegative_int(raw["channel_id"]),
        source=PlateSource(raw["source"]),
        received_at=_aware_datetime(raw["received_at"]),
        occurred_at=_optional_aware_datetime(raw["occurred_at"]),
        source_event_id=source_event_id,
        plate=plate,
        declared_plate_char_count=_optional_nonnegative_int(raw["declared_plate_char_count"]),
        source_encryption_version=_optional_nonnegative_int(raw["source_encryption_version"]),
        confidence=_optional_nonnegative_int(raw["confidence"]),
        char_confidences=tuple(char_confidences),
        direction=VehicleDirection(raw["direction"]),
        plate_rect=_optional_int_tuple(raw["plate_rect"], length=4),
        plate_size=_optional_int_tuple(raw["plate_size"], length=2),
        channel_guid=channel_guid,
        edge_match=EdgePlateMatch(raw["edge_match"]),
        edge_match_code=_optional_nonnegative_int(raw["edge_match_code"]),
        plate_color=PlateColor(raw["plate_color"]),
        plate_color_code=_optional_nonnegative_int(raw["plate_color_code"]),
        plate_brightness=_optional_nonnegative_int(raw["plate_brightness"]),
        plate_color_confidence=_optional_nonnegative_int(raw["plate_color_confidence"]),
        vehicle_type=VehicleType(raw["vehicle_type"]),
        vehicle_type_code=_optional_nonnegative_int(raw["vehicle_type_code"]),
        vehicle_color=VehicleColor(raw["vehicle_color"]),
        vehicle_color_code=_optional_nonnegative_int(raw["vehicle_color_code"]),
        vehicle_brand_code=_optional_nonnegative_int(raw["vehicle_brand_code"]),
        source_end_at=_optional_aware_datetime(raw["source_end_at"]),
        full_image=_optional_base64(raw["full_image_base64"]),
        plate_image=_optional_base64(raw["plate_image_base64"]),
        full_image_format=_optional_enum(ImageFormat, raw["full_image_format"]),
        plate_image_format=_optional_enum(ImageFormat, raw["plate_image_format"]),
        full_image_size=_optional_int_tuple(raw["full_image_size"], length=2),
        is_partial=raw["is_partial"] if isinstance(raw["is_partial"], bool) else _raise_type_error(),
        warnings=tuple(warnings),
    )


def _required_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError
    return value


def _parse_device_info(result: Any) -> RuntimeDeviceInfo:
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeClientError("runtime returned invalid device info")
    string_fields = (
        "device_name",
        "device_model",
        "serial_number",
        "firmware",
        "hardware_version",
        "kernel_version",
        "mcu_version",
    )
    integer_fields = (
        "video_inputs",
        "audio_inputs",
        "sensor_inputs",
        "sensor_outputs",
        "device_type",
    )
    if any(not isinstance(result.get(name), str) for name in string_fields) or any(
        isinstance(result.get(name), bool) or not isinstance(result.get(name), int) or result[name] < 0
        for name in integer_fields
    ):
        raise RuntimeClientError("runtime returned invalid device info")
    return RuntimeDeviceInfo(**{name: result[name] for name in (*string_fields, *integer_fields)})


def _parse_channel_scan(result: Any, *, max_channels: int) -> RuntimeChannelScan:
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeClientError("runtime returned an invalid channel scan")
    identity_fields = ("device_name", "device_model", "serial_number", "firmware")
    cameras = result.get("cameras")
    if (
        any(not isinstance(result.get(name), str) for name in identity_fields)
        or not isinstance(cameras, list)
        or len(cameras) > max_channels
        or result.get("total_channels") != len(cameras)
    ):
        raise RuntimeClientError("runtime returned an invalid channel scan")
    channels: list[RuntimeChannel] = []
    seen: set[int] = set()
    for camera in cameras:
        if not isinstance(camera, dict):
            raise RuntimeClientError("runtime returned an invalid channel scan")
        channel = camera.get("channel")
        status = camera.get("status")
        if (
            isinstance(channel, bool)
            or not isinstance(channel, int)
            or not 0 <= channel <= 255
            or channel in seen
            or status not in {"Online", "Offline"}
            or any(not isinstance(camera.get(name), str) for name in ("name", "address", "protocol", "model"))
            or any(
                isinstance(camera.get(name), bool)
                or not isinstance(camera.get(name), int)
                or not 0 <= camera[name] <= 65_535
                for name in ("port", "httpPort")
            )
        ):
            raise RuntimeClientError("runtime returned an invalid channel scan")
        seen.add(channel)
        channels.append(
            RuntimeChannel(
                channel=channel,
                name=camera["name"],
                address=camera["address"],
                port=camera["port"],
                http_port=camera["httpPort"],
                online=status == "Online",
                protocol=camera["protocol"],
                model=camera["model"],
            )
        )
    return RuntimeChannelScan(
        **{name: result[name] for name in identity_fields},
        channels=tuple(channels),
    )


def _parse_snapshot(result: Any) -> bytes:
    if not isinstance(result, str):
        raise RuntimeClientError("runtime returned an invalid snapshot")
    try:
        image = base64.b64decode(result, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeClientError("runtime returned an invalid snapshot") from exc
    if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9") or len(image) > MAX_RUNTIME_SNAPSHOT_BYTES:
        raise RuntimeClientError("runtime returned an invalid snapshot")
    return image


def _parse_rtsp_url(result: Any) -> str:
    try:
        if (
            not isinstance(result, str)
            or not result
            or len(result.encode("utf-8")) > MAX_RUNTIME_RTSP_URL_BYTES
            or any(ord(char) < 32 or ord(char) == 127 for char in result)
        ):
            raise ValueError
        parsed = urlsplit(result)
        if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.hostname:
            raise ValueError
        _ = parsed.port
    except (UnicodeError, ValueError):
        raise RuntimeClientError("runtime returned an invalid RTSP URL") from None
    return result


def _optional_nonnegative_int(value: Any) -> int | None:
    return None if value is None else _required_nonnegative_int(value)


def _optional_int_tuple(value: Any, *, length: int) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != length:
        raise TypeError
    return tuple(_required_nonnegative_int(item) for item in value)


def _aware_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError
    return parsed


def _optional_aware_datetime(value: Any) -> datetime | None:
    return None if value is None else _aware_datetime(value)


def _optional_base64(value: Any) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError
    return base64.b64decode(value, validate=True)


def _optional_enum(enum_type: Any, value: Any) -> Any:
    return None if value is None else enum_type(value)


def _raise_type_error() -> Any:
    raise TypeError


def _platform_inventory_job(host: str, port: int, username: str, password: str) -> dict[str, Any]:
    return _platform_job("inventorySnapshot", host, port, username, password)


def _platform_job(
    operation: str,
    host: str,
    port: int,
    username: str,
    password: str,
) -> dict[str, Any]:
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
        "operation": operation,
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


def _parse_platform_authority(result: Any) -> RuntimePlatformAuthorityResult:
    try:
        if not isinstance(result, dict) or set(result) != {*PLATFORM_AUTHORITY_LISTS, "fetch_status"}:
            raise TypeError
        fetch_status = result["fetch_status"]
        if (
            not isinstance(fetch_status, dict)
            or set(fetch_status) != set(PLATFORM_AUTHORITY_LISTS)
            or any(value not in {"ok", "unavailable", "failed"} for value in fetch_status.values())
        ):
            raise TypeError
        lists: dict[str, tuple[dict[str, Any], ...]] = {}
        for name in PLATFORM_AUTHORITY_LISTS:
            value = result[name]
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise TypeError
            lists[name] = tuple(value)
    except (KeyError, TypeError, ValueError):
        raise RuntimeClientError("runtime returned an invalid platform authority snapshot") from None
    return RuntimePlatformAuthorityResult(fetch_status=dict(fetch_status), **lists)
