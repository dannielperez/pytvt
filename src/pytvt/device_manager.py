"""Unified device management facade with automatic backend resolution.

``DeviceManager`` provides a single API for TVT device operations that
transparently uses the best available backend:

1. **Native SDK** (``netsdk``) — direct ctypes calls to ``libdvrnetsdk.so``.
   Available on Linux x86_64/aarch64 when the shared library is present.
2. **SDK HTTP** (``sdk_http``) — HTTP calls to the tvt-api Docker container.
   Available on any platform when the container is reachable.

The manager probes backends at construction time and picks the first one
that works.  You can also force a specific backend or inspect what was
chosen.

Usage::

    from pytvt.device_manager import DeviceManager

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

import logging
import shutil
import subprocess
from enum import Enum, unique

from .sdk_http_client import (
    CommandResult,
    DeviceInfoResult,
    DeviceTimeResult,
    RtspUrlResult,
    SdkHttpClient,
)

logger = logging.getLogger(__name__)


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


def _netsdk_available() -> bool:
    """Check if the native SDK can be loaded (Linux + library present)."""
    try:
        from .netsdk.loader import is_netsdk_available

        return is_netsdk_available()
    except Exception:
        return False


def _docker_tvt_api_available(base_url: str, timeout: int = 3) -> bool:
    """Check if the tvt-api container is reachable."""
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
) -> list[Backend]:
    """Probe and return all available backends in priority order.

    Returns:
        List of backends that are ready to use (best first).
    """
    backends: list[Backend] = []
    if _netsdk_available():
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
        backend: Force a specific backend (``None`` = auto-detect).
        api_url: Base URL for the tvt-api container (used by sdk_http backend).
        timeout: HTTP/connection timeout in seconds.

    Raises:
        NoBackendAvailable: If no backend is usable.
    """

    def __init__(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        backend: Backend | str | None = None,
        api_url: str = "http://localhost:3000",
        timeout: int = 30,
    ) -> None:
        self._ip = ip
        self._username = username
        self._password = password
        self._port = port
        self._api_url = api_url
        self._timeout = timeout

        # Resolve backend
        if backend is not None:
            self._backend = Backend(backend) if isinstance(backend, str) else backend
        else:
            self._backend = self._auto_detect()

        logger.info("DeviceManager(%s) using backend=%s", ip, self._backend)

        # Lazy-init holders
        self._http_client: SdkHttpClient | None = None
        self._netsdk_session: object | None = None  # DeviceSession from netsdk.client

    def _auto_detect(self) -> Backend:
        """Pick the best available backend."""
        if _netsdk_available():
            return Backend.NETSDK
        if _docker_tvt_api_available(self._api_url, timeout=min(self._timeout, 5)):
            return Backend.SDK_HTTP
        raise NoBackendAvailable(
            "No backend available. Either run on Linux with libdvrnetsdk.so or start the tvt-api Docker container."
        )

    @property
    def backend(self) -> Backend:
        """The active backend."""
        return self._backend

    @property
    def ip(self) -> str:
        return self._ip

    # ── Internal lazy accessors ──────────────────────────────────

    def _get_http(self) -> SdkHttpClient:
        if self._http_client is None:
            self._http_client = SdkHttpClient(self._api_url, timeout=self._timeout)
        return self._http_client

    def _get_netsdk_session(self):
        """Return a logged-in DeviceSession (netsdk backend)."""
        if self._netsdk_session is None:
            from .netsdk.client import NetSdkClient

            client = NetSdkClient()
            self._netsdk_session = client.login(
                self._ip,
                self._username,
                self._password,
                port=self._port,
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

    def snapshot(self, *, channel: int = 0) -> bytes | None:
        """Capture a JPEG snapshot from a channel."""
        if self._backend == Backend.NETSDK:
            return self._netsdk_snapshot(channel=channel)
        return self._get_http().snapshot(
            self._ip,
            self._username,
            self._password,
            port=self._port,
            channel=channel,
        )

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
        try:
            session = self._get_netsdk_session()
            return session.capture_jpeg(channel)
        except Exception:
            return None

    def _netsdk_rtsp_url(self, *, channel: int = 0, stream_type: int = 0) -> RtspUrlResult:
        try:
            from .netsdk.constants import StreamType

            st = StreamType(stream_type)
            session = self._get_netsdk_session()
            url = session.rtsp_url(channel, st)
            return RtspUrlResult(success=True, rtsp_url=url)
        except Exception as e:
            return RtspUrlResult(success=False, error=str(e))

    def _netsdk_ptz(self, *, channel: int = 0, command: int = 0, speed: int = 4) -> CommandResult:
        try:
            from .netsdk.constants import PtzCommand, PtzSpeed

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
            from .netsdk.constants import PtzCommand

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
        return f"DeviceManager({self._ip!r}, backend={self._backend!r})"
