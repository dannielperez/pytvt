"""Unified device management facade with automatic backend resolution.

``DeviceManager`` provides a single API for TVT device operations that
transparently uses the best available backend:

1. **Native SDK** (``netsdk``) — direct ctypes calls to ``libdvrnetsdk.so``.
    Available on Linux x86_64/aarch64 when a vendor SDK installation is present.
2. **SDK HTTP** (``sdk_http``) — HTTP calls to a compatible SDK bridge service.
    Available on any platform when that service is reachable.

The manager probes backends at construction time and picks the first one
that works.  You can also force a specific backend or inspect what was
chosen.

Usage::

    from pytvt.device_sdk.manager import DeviceManager

    mgr = DeviceManager("10.0.0.1", "admin", "pass")
    print(f"Using backend: {mgr.backend}")

    info = mgr.device_info()
    jpeg = mgr.snapshot(channel=0)
    url  = mgr.rtsp_url(channel=0)
    mgr.reboot()

All methods return the same result types regardless of which backend is
active.  If no backend is available, :class:`NoBackendAvailable` is raised
at construction.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import time
from collections import OrderedDict
from enum import Enum, unique
from threading import Lock
from typing import TypeAlias
from urllib.parse import quote

from ..models import DeviceEntry
from .http_client import (
    CommandResult,
    DeviceInfoResult,
    DeviceTimeResult,
    RtspUrlResult,
    SdkHttpClient,
    SnapshotAttempt,
)

logger = logging.getLogger(__name__)

_DEFAULT_RTSP_URL_CACHE_TTL_SECONDS = 300.0
_MAX_RTSP_URL_CACHE_ENTRIES = 256
_SNAPSHOT_STREAM_TYPES = {
    "main": 0,
    "sub": 1,
    "third": 2,
}
_RtspUrlCacheKey: TypeAlias = tuple[str, int, str, bytes, int, int]


class _RtspUrlCache:
    """Small process-local TTL/LRU cache for credential-bearing RTSP URLs.

    URLs stay in the process that already holds the device credential. They
    are never written to a shared cache, disk, logs, or API response.
    """

    def __init__(self, *, max_entries: int) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[_RtspUrlCacheKey, tuple[float, str]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: _RtspUrlCacheKey, *, now: float) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, url = entry
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return url

    def set(
        self,
        key: _RtspUrlCacheKey,
        url: str,
        *,
        expires_at: float,
    ) -> None:
        with self._lock:
            self._entries[key] = (expires_at, url)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def discard(self, key: _RtspUrlCacheKey) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_RTSP_URL_CACHE = _RtspUrlCache(max_entries=_MAX_RTSP_URL_CACHE_ENTRIES)


def _first_failure(attempts: list[SnapshotAttempt]) -> SnapshotAttempt:
    """Report the most specific failure across the transport legs tried.

    A leg that never had a stream URL says less than one whose capture call
    actually failed, so the latter is preferred; the first attempt is the
    fallback so a reason is always returned.
    """
    for attempt in attempts:
        if attempt.error_kind not in ("", "no_stream_url"):
            return attempt
    return (
        attempts[0]
        if attempts
        else SnapshotAttempt(error="No snapshot transport was attempted.", error_kind="not_attempted")
    )


def _snapshot_stream_type(*, stream: str | None, stream_type: int) -> int:
    """Keep the recorder's numeric stream encoding behind the pytvt boundary."""
    if stream is None:
        return stream_type
    try:
        return _SNAPSHOT_STREAM_TYPES[stream]
    except KeyError as exc:
        choices = ", ".join(_SNAPSHOT_STREAM_TYPES)
        raise ValueError(f"stream must be one of: {choices}") from exc


# ── Backend enum ─────────────────────────────────────────────────────


@unique
class Backend(str, Enum):
    """Which backend is powering the DeviceManager."""

    NETSDK = "netsdk"
    SDK_HTTP = "sdk_http"

    def __str__(self) -> str:
        return self.value


# ── Exceptions ───────────────────────────────────────────────────────


class NoBackendAvailable(RuntimeError):
    """No usable backend could be found."""


# ── Availability probes ──────────────────────────────────────────────


def _netsdk_available(sdk_path: str | None = None, *, require_nat: bool = False) -> bool:
    """Check if the native SDK can be loaded (Linux + library present)."""
    try:
        from .loader import is_netsdk_available

        return is_netsdk_available(sdk_path=sdk_path, require_nat=require_nat)
    except Exception:
        return False


def _resolve_connection_method(ip: str, identifier: str, connection_method: str | None) -> str:
    method = (connection_method or "").strip().lower()
    if method in {"direct", "nat"}:
        return method
    if identifier:
        return "nat"
    if ip:
        return "direct"
    raise ValueError("Either ip or identifier is required")


def _docker_tvt_api_available(base_url: str, timeout: int = 3) -> bool:
    """Check if the SDK bridge service is reachable."""
    try:
        client = SdkHttpClient(base_url, timeout=timeout)
        return client.health()
    except Exception:
        return False


def _docker_running() -> bool:
    """Check if the Docker daemon is accessible."""
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        result = subprocess.run(
            [docker, "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def available_backends(
    api_url: str = "http://localhost:3000",
    sdk_path: str | None = None,
) -> list[Backend]:
    """Probe and return all available backends in priority order.

    Returns:
        List of backends that are ready to use (best first).
    """
    backends: list[Backend] = []
    if _netsdk_available(sdk_path):
        backends.append(Backend.NETSDK)
    if _docker_tvt_api_available(api_url):
        backends.append(Backend.SDK_HTTP)
    return backends


# ── DeviceManager ────────────────────────────────────────────────────


class DeviceManager:
    """Unified TVT device management with automatic backend selection.

    Args:
        ip: Device IP address.
        username: Login username.
        password: Login password.
        port: SDK/protocol port (default 6036).
        http_port: NVR web-CGI port, used to resolve an RTSP URL without a
            native SDK login (default 80).
        backend: Force a specific backend (``None`` = auto-detect).
        api_url: Base URL for the SDK bridge service (used by sdk_http backend).
        sdk_path: Optional vendor SDK path for the netsdk backend.
        timeout: HTTP/connection timeout in seconds.
        direct_camera: Treat the target as a standalone TVT IPC and use its
            standard RTSP profile URL instead of the NVR channel resolver.
        rtsp_url_cache_ttl: Seconds to reuse a resolved camera-direct URL in
            this process. Set to zero to disable caching.

    Raises:
        NoBackendAvailable: If no backend is usable.
    """

    def __init__(
        self,
        ip: str | None,
        username: str,
        password: str,
        *,
        port: int = 6036,
        http_port: int = 80,
        identifier: str | None = None,
        connection_method: str | None = None,
        nat_server: str | None = None,
        nat_port: int | None = None,
        nat_type: str | int = "nat20",
        backend: Backend | str | None = None,
        api_url: str = "http://localhost:3000",
        sdk_path: str | None = None,
        timeout: int = 30,
        direct_camera: bool = False,
        rtsp_url_cache_ttl: float = _DEFAULT_RTSP_URL_CACHE_TTL_SECONDS,
    ) -> None:
        self._ip = ip or ""
        self._username = username
        self._password = password
        self._port = port
        self._http_port = http_port
        self._identifier = (identifier or "").strip()
        self._connection_method = _resolve_connection_method(self._ip, self._identifier, connection_method)
        self._nat_server = (nat_server or "").strip() or None
        self._nat_port = nat_port
        self._nat_type = nat_type
        self._api_url = api_url
        self._sdk_path = sdk_path
        self._timeout = timeout
        self._direct_camera = direct_camera
        self._rtsp_url_cache_ttl = max(0.0, float(rtsp_url_cache_ttl))

        # Resolve backend
        if backend is not None:
            self._backend = Backend(backend) if isinstance(backend, str) else backend
        else:
            self._backend = self._auto_detect()

        if self._connection_method == "nat" and self._backend != Backend.NETSDK:
            raise NoBackendAvailable("NAT connections require the native netsdk backend.")

        logger.info(
            "DeviceManager(%s) using backend=%s connection=%s",
            self.target,
            self._backend,
            self._connection_method,
        )

        # Lazy-init holders
        self._http_client: SdkHttpClient | None = None
        self._netsdk_session: object | None = None  # DeviceSession from netsdk.client

    def _auto_detect(self) -> Backend:
        """Pick the best available backend."""
        if self._connection_method == "nat":
            if _netsdk_available(self._sdk_path, require_nat=True):
                return Backend.NETSDK
            raise NoBackendAvailable(
                "No NAT-capable backend available. Install the vendor SDK, ensure libNatClientSDK.so is present, "
                "and set TVT_SDK_PATH or pass sdk_path=...."
            )

        if _netsdk_available(self._sdk_path):
            return Backend.NETSDK
        if _docker_tvt_api_available(self._api_url, timeout=min(self._timeout, 5)):
            return Backend.SDK_HTTP
        raise NoBackendAvailable(
            "No backend available. Install the vendor SDK and set TVT_SDK_PATH or pass sdk_path=..., "
            "or provide a reachable SDK HTTP service via api_url."
        )

    @property
    def backend(self) -> Backend:
        """The active backend."""
        return self._backend

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def identifier(self) -> str:
        return self._identifier

    @property
    def connection_method(self) -> str:
        return self._connection_method

    @property
    def target(self) -> str:
        return self._ip or self._identifier

    @classmethod
    def from_device(
        cls,
        device: DeviceEntry,
        username: str,
        password: str,
        *,
        backend: Backend | str | None = None,
        api_url: str = "http://localhost:3000",
        sdk_path: str | None = None,
        timeout: int = 30,
    ) -> DeviceManager:
        """Create a manager from a :class:`pytvt.models.DeviceEntry`."""
        return cls(
            device.ip or None,
            username,
            password,
            port=device.port or 6036,
            identifier=device.identifier or None,
            connection_method=device.effective_connection_method,
            nat_server=device.nat_server or None,
            nat_port=device.nat_port or None,
            backend=backend,
            api_url=api_url,
            sdk_path=sdk_path,
            timeout=timeout,
        )

    # ── Internal lazy accessors ──────────────────────────────────

    def _get_http(self) -> SdkHttpClient:
        if self._http_client is None:
            self._http_client = SdkHttpClient(self._api_url, timeout=self._timeout)
        return self._http_client

    def _get_netsdk_session(self):
        """Return a logged-in DeviceSession (netsdk backend)."""
        if self._netsdk_session is None:
            from .client import NetSdkClient

            client = NetSdkClient(sdk_path=self._sdk_path)
            self._netsdk_session = client.connect(
                method=self._connection_method,  # type: ignore[arg-type]
                username=self._username,
                password=self._password,
                host=self._ip or None,
                port=self._port,
                identifier=self._identifier or None,
                timeout=float(self._timeout),
                nat_server=self._nat_server,
                nat_port=self._nat_port,
                connect_type=self._nat_type,
                fallback_to_direct=bool(self._ip),
            )
            # Keep a ref to the client so it doesn't get GC'd
            self._netsdk_session._manager_client = client  # type: ignore[attr-defined]
        return self._netsdk_session

    # ── Public API ───────────────────────────────────────────────

    def device_info(self) -> DeviceInfoResult:
        """Get full device information."""
        if self._backend == Backend.NETSDK:
            return self._netsdk_device_info()
        return self._get_http().device_info(
            self._ip,
            self._username,
            self._password,
            port=self._port,
        )

    def device_time(self, *, set_timestamp: int | None = None) -> DeviceTimeResult:
        """Get or set device time."""
        if self._backend == Backend.NETSDK:
            return self._netsdk_device_time(set_timestamp=set_timestamp)
        return self._get_http().device_time(
            self._ip,
            self._username,
            self._password,
            port=self._port,
            set_timestamp=set_timestamp,
        )

    def snapshot(
        self,
        *,
        channel: int = 0,
        prefer_rtsp: bool = True,
        stream_type: int = 0,
        stream: str | None = None,
        timeout: int = 10,
        total_timeout: float | None = None,
        allow_fallback: bool = True,
    ) -> bytes | None:
        """Capture a JPEG snapshot from a channel.

        The preferred path is a direct-RTSP frame grab (lower latency / NAT-free
        wherever the RTSP endpoint is reachable); on any failure it transparently
        falls back to the SDK/NAT (NETSDK) or HTTP snapshot. Pass
        ``prefer_rtsp=False`` to force the legacy SDK/HTTP path.
        """
        return self.snapshot_attempt(
            channel=channel,
            prefer_rtsp=prefer_rtsp,
            stream_type=stream_type,
            stream=stream,
            timeout=timeout,
            total_timeout=total_timeout,
            allow_fallback=allow_fallback,
        ).image

    def snapshot_attempt(
        self,
        *,
        channel: int = 0,
        prefer_rtsp: bool = True,
        stream_type: int = 0,
        stream: str | None = None,
        timeout: int = 10,
        total_timeout: float | None = None,
        allow_fallback: bool = True,
    ) -> SnapshotAttempt:
        """Capture a JPEG snapshot, reporting WHY when it produces no image.

        Same transport preference as :meth:`snapshot` — RTSP first, then the
        SDK or HTTP path — but the reason each leg produced nothing survives
        into the result instead of collapsing to ``None``. A caller showing an
        operator "capture failed" can say what actually failed.
        """
        stream_type = _snapshot_stream_type(
            stream=stream,
            stream_type=stream_type,
        )
        deadline = None if total_timeout is None else time.monotonic() + max(0.0, total_timeout)
        attempts: list[SnapshotAttempt] = []
        if prefer_rtsp:
            rtsp = self._rtsp_snapshot_attempt(
                channel=channel,
                stream_type=stream_type,
                timeout=timeout,
                deadline=deadline,
                allow_resolver_fallback=allow_fallback,
            )
            if rtsp.image:
                return rtsp
            attempts.append(rtsp)
        if not allow_fallback or (deadline is not None and deadline <= time.monotonic()):
            return _first_failure(attempts)

        if self._backend == Backend.NETSDK:
            if deadline is not None:
                return _first_failure(attempts)
            netsdk = self._netsdk_snapshot_attempt(channel=channel)
            if netsdk.image:
                return netsdk
            attempts.append(netsdk)
            return _first_failure(attempts)

        remaining = None if deadline is None else max(0.001, deadline - time.monotonic())
        http = self._http_snapshot_attempt(channel=channel, timeout=remaining)
        if http.image:
            return http
        attempts.append(http)
        return _first_failure(attempts)

    def _http_snapshot_attempt(
        self,
        *,
        channel: int = 0,
        timeout: float | None = None,
    ) -> SnapshotAttempt:
        try:
            client = self._get_http() if timeout is None else SdkHttpClient(self._api_url, timeout=timeout)
            data = client.snapshot(
                self._ip,
                self._username,
                self._password,
                port=self._port,
                channel=channel,
            )
        except Exception as exc:
            logger.warning(
                "HTTP snapshot failed ip=%s channel=%s: %s: %s",
                self._ip,
                channel,
                type(exc).__name__,
                exc,
            )
            return SnapshotAttempt(
                method="http",
                error=f"{type(exc).__name__}: {exc}",
                error_kind="http_error",
            )
        if not data:
            logger.warning("HTTP snapshot returned no data ip=%s channel=%s", self._ip, channel)
            return SnapshotAttempt(
                method="http",
                error="Device returned an empty HTTP snapshot.",
                error_kind="empty_frame",
            )
        return SnapshotAttempt(image=data, method="http")

    def _rtsp_snapshot(self, *, channel: int = 0, stream_type: int = 0, timeout: int = 10) -> bytes | None:
        """Direct-RTSP JPEG grab via the resolved RTSP URL; ``None`` on any failure."""
        return self._rtsp_snapshot_attempt(channel=channel, stream_type=stream_type, timeout=timeout).image

    def _http_rtsp_url(
        self,
        *,
        channel: int = 0,
        stream_type: int = 0,
        deadline: float | None = None,
    ) -> str | None:
        """Resolve a direct-camera or NVR-channel RTSP URL. ``None`` if it can't.

        The point of this path is what it *doesn't* do. ``NET_SDK_GetRtspUrl``
        needs a native session, and on a loaded recorder that login was
        measured at ~5.9s — paid on every capture, because the bridge runs each
        operation in a throwaway process and keeps no session. The web CGI
        answers the same question over HTTP and points straight at the camera
        rather than the NVR's relay.

        Successful URLs are retained in a small process-local TTL/LRU cache.
        The value contains credentials, so it deliberately never crosses a
        persistence, shared-cache, log, or API boundary.

        Best-effort by design: any failure returns ``None`` so the caller falls
        back to the native resolver it used before.
        """
        if not self._ip:
            return None
        if self._direct_camera:
            user = quote(self._username, safe="")
            password = quote(self._password, safe="")
            profile = {0: "profile1", 1: "profile2", 2: "profile3"}.get(
                stream_type,
                "profile1",
            )
            return f"rtsp://{user}:{password}@{self._ip}:554/{profile}"
        cache_key = self._rtsp_url_cache_key(
            channel=channel,
            stream_type=stream_type,
        )
        if self._rtsp_url_cache_ttl > 0:
            cached = _RTSP_URL_CACHE.get(cache_key, now=time.monotonic())
            if cached is not None:
                return cached
        # Lazy import keeps the NVR XML/HTTP module off the device_sdk load path.
        from ..xml_api import NvrClient

        stream_name = {0: "main", 1: "sub", 2: "third"}.get(stream_type, "main")
        try:
            with NvrClient(
                self._ip,
                self._username,
                self._password,
                port=self._http_port,
                timeout=min(self._timeout, 10),
                deadline=deadline,
            ) as nvr:
                nvr.login()
                # The web CGI numbers channels from 1; device_sdk from 0.
                url = nvr.get_rtsp_url(channel + 1, stream_name) or None
                if url is not None and self._rtsp_url_cache_ttl > 0:
                    _RTSP_URL_CACHE.set(
                        cache_key,
                        url,
                        expires_at=time.monotonic() + self._rtsp_url_cache_ttl,
                    )
                return url
        except Exception as exc:
            logger.info(
                "HTTP RTSP url resolve failed ip=%s channel=%s: %s: %s",
                self._ip,
                channel,
                type(exc).__name__,
                exc,
            )
            return None

    def _rtsp_url_cache_key(
        self,
        *,
        channel: int,
        stream_type: int,
    ) -> _RtspUrlCacheKey:
        credential_fingerprint = hashlib.sha256(self._password.encode("utf-8")).digest()
        return (
            self._ip,
            self._http_port,
            self._username,
            credential_fingerprint,
            channel,
            stream_type,
        )

    def _discard_cached_rtsp_url(
        self,
        *,
        channel: int,
        stream_type: int,
    ) -> None:
        _RTSP_URL_CACHE.discard(
            self._rtsp_url_cache_key(
                channel=channel,
                stream_type=stream_type,
            )
        )

    def _rtsp_snapshot_attempt(
        self,
        *,
        channel: int = 0,
        stream_type: int = 0,
        timeout: int = 10,
        deadline: float | None = None,
        allow_resolver_fallback: bool = True,
    ) -> SnapshotAttempt:
        """RTSP leg, reporting which step produced no frame.

        Two distinct failures used to look identical: the recorder never gave
        us a stream URL, and it gave us one that yielded no frame. They point
        at different problems, so they are reported apart.
        """
        rtsp_url = self._http_rtsp_url(
            channel=channel,
            stream_type=stream_type,
            deadline=deadline,
        )
        if rtsp_url is None:
            if not allow_resolver_fallback or deadline is not None:
                return SnapshotAttempt(
                    method="rtsp",
                    error="Direct RTSP URL is not cached or could not be resolved.",
                    error_kind="no_stream_url",
                )
            result = self.rtsp_url(channel=channel, stream_type=stream_type)
            if not result.success or not isinstance(result.rtsp_url, str) or not result.rtsp_url:
                detail = result.error or "Device did not return an RTSP URL."
                logger.info("RTSP url unavailable ip=%s channel=%s: %s", self._ip, channel, detail)
                return SnapshotAttempt(method="rtsp", error=str(detail), error_kind="no_stream_url")
            rtsp_url = result.rtsp_url
        # Lazy import avoids pulling the NVR XML/HTTP module at device_sdk load.
        from ..xml_api import rtsp_snapshot_attempt_bytes

        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            return SnapshotAttempt(
                method="rtsp",
                error="Snapshot deadline expired before the frame grab.",
                error_kind="deadline_exceeded",
            )
        try:
            frame_timeout = timeout if remaining is None else max(1, min(timeout, int(remaining)))
            if remaining is None:
                frame = rtsp_snapshot_attempt_bytes(rtsp_url, timeout=frame_timeout)
            else:
                frame = rtsp_snapshot_attempt_bytes(
                    rtsp_url,
                    timeout=frame_timeout,
                    wall_timeout=remaining,
                )
        except Exception as exc:
            self._discard_cached_rtsp_url(
                channel=channel,
                stream_type=stream_type,
            )
            logger.warning(
                "RTSP frame grab failed ip=%s channel=%s: %s: %s",
                self._ip,
                channel,
                type(exc).__name__,
                exc,
            )
            return SnapshotAttempt(
                method="rtsp",
                error=f"{type(exc).__name__}: {exc}",
                error_kind="rtsp_error",
            )
        if not frame.image:
            self._discard_cached_rtsp_url(
                channel=channel,
                stream_type=stream_type,
            )
            logger.info(
                "RTSP frame grab failed ip=%s channel=%s kind=%s",
                self._ip,
                channel,
                frame.error_kind,
            )
            return SnapshotAttempt(
                method="rtsp",
                error=frame.error or "RTSP stream yielded no frame.",
                error_kind=frame.error_kind or "empty_frame",
            )
        return SnapshotAttempt(image=frame.image, method="rtsp")

    def rtsp_url(self, *, channel: int = 0, stream_type: int = 0) -> RtspUrlResult:
        """Get RTSP stream URL for a channel."""
        if self._backend == Backend.NETSDK:
            return self._netsdk_rtsp_url(channel=channel, stream_type=stream_type)
        return self._get_http().rtsp_url(
            self._ip,
            self._username,
            self._password,
            port=self._port,
            channel=channel,
            stream_type=stream_type,
        )

    def ptz(self, *, channel: int = 0, command: int = 0, speed: int = 4) -> CommandResult:
        """Send a PTZ command."""
        if self._backend == Backend.NETSDK:
            return self._netsdk_ptz(channel=channel, command=command, speed=speed)
        return self._get_http().ptz(
            self._ip,
            self._username,
            self._password,
            port=self._port,
            channel=channel,
            command=command,
            speed=speed,
        )

    def ptz_preset(
        self,
        *,
        channel: int = 0,
        command: int = 16,
        preset_index: int = 1,
    ) -> CommandResult:
        """Manage PTZ presets."""
        if self._backend == Backend.NETSDK:
            return self._netsdk_ptz_preset(
                channel=channel,
                command=command,
                preset_index=preset_index,
            )
        return self._get_http().ptz_preset(
            self._ip,
            self._username,
            self._password,
            port=self._port,
            channel=channel,
            command=command,
            preset_index=preset_index,
        )

    def reboot(self) -> CommandResult:
        """Reboot the device."""
        if self._backend == Backend.NETSDK:
            return self._netsdk_reboot()
        return self._get_http().reboot(
            self._ip,
            self._username,
            self._password,
            port=self._port,
        )

    # ── netsdk backend implementations ───────────────────────────

    def _netsdk_device_info(self) -> DeviceInfoResult:
        try:
            session = self._get_netsdk_session()
            info = session.device_info()
            return DeviceInfoResult(
                success=True,
                device_name=info.device_name,
                device_model=info.product,
                serial_number=info.serial_number,
                firmware=info.firmware,
                hardware_version=info.hardware_version,
                kernel_version=info.kernel_version,
                video_inputs=info.video_inputs,
                audio_inputs=info.audio_inputs,
                sensor_inputs=info.sensor_inputs,
                sensor_outputs=info.sensor_outputs,
                device_type=info.device_type,
            )
        except Exception as e:
            return DeviceInfoResult(success=False, error=str(e))

    def _netsdk_device_time(self, *, set_timestamp: int | None = None) -> DeviceTimeResult:
        try:
            session = self._get_netsdk_session()
            if set_timestamp is not None:
                session.sync_time(set_timestamp)
                return DeviceTimeResult(
                    success=True,
                    action="set",
                    timestamp=set_timestamp,
                )
            dt = session.device_time()
            return DeviceTimeResult(
                success=True,
                action="get",
                device_time=dt.isoformat(),
            )
        except Exception as e:
            return DeviceTimeResult(success=False, error=str(e))

    def _netsdk_snapshot(self, *, channel: int = 0) -> bytes | None:
        return self._netsdk_snapshot_attempt(channel=channel).image

    def _netsdk_snapshot_attempt(self, *, channel: int = 0) -> SnapshotAttempt:
        """NETSDK leg, preserving the vendor error instead of discarding it.

        This bare ``except`` used to return ``None``, so a failed login, an
        unsupported capability and a socket timeout all reached the caller as
        "no image" — indistinguishable from a recorder with nothing to send,
        and invisible to anything but a stack trace nobody logged.
        """
        try:
            session = self._get_netsdk_session()
        except Exception as exc:
            logger.warning(
                "NETSDK session unavailable ip=%s channel=%s: %s: %s",
                self._ip,
                channel,
                type(exc).__name__,
                exc,
            )
            return SnapshotAttempt(
                method="netsdk",
                error=f"{type(exc).__name__}: {exc}",
                error_kind="session_error",
            )
        try:
            data = session.capture_jpeg(channel)
        except Exception as exc:
            logger.warning(
                "NETSDK capture failed ip=%s channel=%s: %s: %s",
                self._ip,
                channel,
                type(exc).__name__,
                exc,
            )
            return SnapshotAttempt(
                method="netsdk",
                error=f"{type(exc).__name__}: {exc}",
                error_kind="sdk_error",
            )
        if not data:
            logger.info("NETSDK capture returned no data ip=%s channel=%s", self._ip, channel)
            return SnapshotAttempt(
                method="netsdk",
                error="Recorder returned an empty JPEG buffer.",
                error_kind="empty_frame",
            )
        return SnapshotAttempt(image=data, method="netsdk")

    def _netsdk_rtsp_url(self, *, channel: int = 0, stream_type: int = 0) -> RtspUrlResult:
        try:
            from .constants import StreamType

            st = StreamType(stream_type)
            session = self._get_netsdk_session()
            url = session.rtsp_url(channel, st)
            return RtspUrlResult(success=True, rtsp_url=url)
        except Exception as e:
            return RtspUrlResult(success=False, error=str(e))

    def _netsdk_ptz(self, *, channel: int = 0, command: int = 0, speed: int = 4) -> CommandResult:
        try:
            from .constants import PtzCommand, PtzSpeed

            session = self._get_netsdk_session()
            session.ptz(PtzCommand(command), channel=channel, speed=PtzSpeed(speed))
            return CommandResult(success=True)
        except Exception as e:
            return CommandResult(success=False, error=str(e))

    def _netsdk_ptz_preset(
        self,
        *,
        channel: int = 0,
        command: int = 16,
        preset_index: int = 1,
    ) -> CommandResult:
        try:
            from .constants import PtzCommand

            session = self._get_netsdk_session()
            session.ptz_preset(PtzCommand(command), preset_index, channel=channel)
            return CommandResult(success=True)
        except Exception as e:
            return CommandResult(success=False, error=str(e))

    def _netsdk_reboot(self) -> CommandResult:
        try:
            session = self._get_netsdk_session()
            session.reboot()
            return CommandResult(success=True)
        except Exception as e:
            return CommandResult(success=False, error=str(e))

    # ── Cleanup ──────────────────────────────────────────────────

    def close(self) -> None:
        """Release any resources held by the manager."""
        if self._netsdk_session is not None:
            try:
                self._netsdk_session.logout()
                client = getattr(self._netsdk_session, "_manager_client", None)
                if client:
                    client.cleanup()
            except Exception:
                pass
            self._netsdk_session = None
        self._http_client = None

    def __enter__(self) -> DeviceManager:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"DeviceManager(target={self.target!r}, backend={self._backend!r}, "
            f"connection_method={self._connection_method!r})"
        )
