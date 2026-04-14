"""High-level SDK HTTP client — wraps all SDK bridge HTTP endpoints.

Provides a Pythonic interface over an SDK bridge service that
proxies the native TVT C++ SDK.  Each method performs a single HTTP
request and returns typed dataclass results.

Usage::

    from pytvt.sdk_http_client import SdkHttpClient

    client = SdkHttpClient("http://localhost:3000")
    info = client.device_info("10.0.0.1", "admin", "pass")
    print(info.device_name, info.serial_number)

    jpeg = client.snapshot("10.0.0.1", "admin", "pass", channel=0)
    if jpeg:
        Path("snap.jpg").write_bytes(jpeg)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Response dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceInfoResult:
    success: bool
    device_name: str = ""
    device_model: str = ""
    serial_number: str = ""
    firmware: str = ""
    hardware_version: str = ""
    kernel_version: str = ""
    mcu_version: str = ""
    video_inputs: int = 0
    audio_inputs: int = 0
    sensor_inputs: int = 0
    sensor_outputs: int = 0
    device_type: int = 0
    error: str | None = None


@dataclass(frozen=True)
class DeviceTimeResult:
    success: bool
    action: str = ""
    device_time: str | None = None
    timestamp: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class RtspUrlResult:
    success: bool
    rtsp_url: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class CommandResult:
    """Generic success/error for simple commands (reboot, ptz, etc.)."""

    success: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class SdkHttpClient:
    """Typed wrapper around SDK bridge HTTP endpoints.

    Args:
        base_url: Base URL of the SDK bridge service (e.g. ``http://localhost:3000``).
        timeout: Default HTTP timeout in seconds.
    """

    def __init__(self, base_url: str = "http://localhost:3000", *, timeout: int = 30) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # -- internal helpers ---------------------------------------------------

    def _connect_payload(
        self,
        ip: str,
        username: str,
        password: str,
        port: int = 6036,
        **extra: object,
    ) -> bytes:
        body: dict[str, object] = {
            "ip": ip,
            "port": port,
            "username": username,
            "password": password,
        }
        body.update(extra)
        return json.dumps(body).encode()

    def _post_json(self, path: str, payload: bytes) -> dict:
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read().decode())

    def _post_raw(self, path: str, payload: bytes) -> tuple[int, bytes, str]:
        """POST returning (status, body_bytes, content_type)."""
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")

    # -- public API ---------------------------------------------------------

    def health(self) -> bool:
        """Check if the SDK bridge service is reachable."""
        try:
            req = urllib.request.Request(f"{self._base_url}/health")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode())
                return data.get("status") == "ok"
        except Exception:
            return False

    def device_info(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
    ) -> DeviceInfoResult:
        """Get full device information (name, model, SN, firmware, etc.)."""
        try:
            data = self._post_json(
                "/device/info",
                self._connect_payload(ip, username, password, port),
            )
            return DeviceInfoResult(
                success=data.get("success", False),
                device_name=data.get("device_name", ""),
                device_model=data.get("device_model", ""),
                serial_number=data.get("serial_number", ""),
                firmware=data.get("firmware", ""),
                hardware_version=data.get("hardware_version", ""),
                kernel_version=data.get("kernel_version", ""),
                mcu_version=data.get("mcu_version", ""),
                video_inputs=data.get("video_inputs", 0),
                audio_inputs=data.get("audio_inputs", 0),
                sensor_inputs=data.get("sensor_inputs", 0),
                sensor_outputs=data.get("sensor_outputs", 0),
                device_type=data.get("device_type", 0),
                error=data.get("error"),
            )
        except urllib.error.URLError as e:
            return DeviceInfoResult(success=False, error=f"Connection error: {e.reason}")
        except TimeoutError:
            return DeviceInfoResult(success=False, error=f"Timeout after {self._timeout}s")

    def device_time(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        set_timestamp: int | None = None,
    ) -> DeviceTimeResult:
        """Get or set device time.

        If *set_timestamp* is provided, sets the device clock to that Unix
        timestamp.  Otherwise returns the current device time.
        """
        extra: dict[str, object] = {}
        if set_timestamp is not None:
            extra["set_timestamp"] = set_timestamp
        try:
            data = self._post_json(
                "/device/time",
                self._connect_payload(ip, username, password, port, **extra),
            )
            return DeviceTimeResult(
                success=data.get("success", False),
                action=data.get("action", ""),
                device_time=data.get("device_time"),
                timestamp=data.get("timestamp"),
                error=data.get("error"),
            )
        except urllib.error.URLError as e:
            return DeviceTimeResult(success=False, error=f"Connection error: {e.reason}")
        except TimeoutError:
            return DeviceTimeResult(success=False, error=f"Timeout after {self._timeout}s")

    def reboot(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
    ) -> CommandResult:
        """Reboot the device."""
        try:
            data = self._post_json(
                "/device/reboot",
                self._connect_payload(ip, username, password, port),
            )
            return CommandResult(success=data.get("success", False), error=data.get("error"))
        except urllib.error.URLError as e:
            return CommandResult(success=False, error=f"Connection error: {e.reason}")
        except TimeoutError:
            return CommandResult(success=False, error=f"Timeout after {self._timeout}s")

    def snapshot(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        channel: int = 0,
    ) -> bytes | None:
        """Capture a JPEG snapshot from a channel.

        Returns raw JPEG bytes on success, ``None`` on failure.
        """
        try:
            _status, body, content_type = self._post_raw(
                "/snapshot",
                self._connect_payload(ip, username, password, port, channel=channel),
            )
            if "image/jpeg" in content_type:
                return body
            return None
        except Exception:
            return None

    def rtsp_url(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        channel: int = 0,
        stream_type: int = 0,
    ) -> RtspUrlResult:
        """Get the RTSP stream URL for a channel.

        Args:
            stream_type: 0 = main stream, 1 = sub stream.
        """
        try:
            data = self._post_json(
                "/rtsp-url",
                self._connect_payload(
                    ip,
                    username,
                    password,
                    port,
                    channel=channel,
                    stream_type=stream_type,
                ),
            )
            return RtspUrlResult(
                success=data.get("success", False),
                rtsp_url=data.get("rtsp_url"),
                error=data.get("error"),
            )
        except urllib.error.URLError as e:
            return RtspUrlResult(success=False, error=f"Connection error: {e.reason}")
        except TimeoutError:
            return RtspUrlResult(success=False, error=f"Timeout after {self._timeout}s")

    def ptz(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        channel: int = 0,
        command: int = 0,
        speed: int = 4,
    ) -> CommandResult:
        """Send a PTZ command.

        Commands: 0=stop, 1=left, 2=right, 3=up, 4=down, 5=left_up,
        6=left_down, 7=right_up, 8=right_down, 9=focus_near, 10=focus_far,
        11=zoom_out, 12=zoom_in, 13=iris_open, 14=iris_close.
        """
        try:
            data = self._post_json(
                "/ptz",
                self._connect_payload(
                    ip,
                    username,
                    password,
                    port,
                    channel=channel,
                    command=command,
                    speed=speed,
                ),
            )
            return CommandResult(success=data.get("success", False), error=data.get("error"))
        except urllib.error.URLError as e:
            return CommandResult(success=False, error=f"Connection error: {e.reason}")
        except TimeoutError:
            return CommandResult(success=False, error=f"Timeout after {self._timeout}s")

    def ptz_preset(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        channel: int = 0,
        command: int = 16,
        preset_index: int = 1,
    ) -> CommandResult:
        """Manage PTZ presets.

        Commands: 15=set, 16=goto, 17=delete.
        """
        try:
            data = self._post_json(
                "/ptz/preset",
                self._connect_payload(
                    ip,
                    username,
                    password,
                    port,
                    channel=channel,
                    command=command,
                    preset_index=preset_index,
                ),
            )
            return CommandResult(success=data.get("success", False), error=data.get("error"))
        except urllib.error.URLError as e:
            return CommandResult(success=False, error=f"Connection error: {e.reason}")
        except TimeoutError:
            return CommandResult(success=False, error=f"Timeout after {self._timeout}s")

    def scan(
        self,
        ip: str,
        username: str,
        password: str,
        *,
        port: int = 6036,
        max_cameras: int = 64,
    ) -> dict:
        """Run a full scan (existing /scan endpoint).

        Returns the raw JSON response dict.
        """
        try:
            return self._post_json(
                "/scan",
                self._connect_payload(
                    ip,
                    username,
                    password,
                    port,
                    maxCameras=max_cameras,
                ),
            )
        except urllib.error.URLError as e:
            return {"success": False, "error": f"Connection error: {e.reason}"}
        except TimeoutError:
            return {"success": False, "error": f"Timeout after {self._timeout}s"}
