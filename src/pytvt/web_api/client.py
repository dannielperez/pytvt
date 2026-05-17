"""TVT HTTP Web API client (LAPI).

Implements the TVT HTTP API protocol documented in the *HTTP API Protocol
User Guide for IP Media Device v2.0.0*.  Uses HTTP Basic auth per request
and XML request/response bodies.

This is separate from :class:`pytvt.xml_api.NvrClient` which targets
the NVMS-9000 NVR CGI interface with session-based auth.

Key design choices
------------------
- **Capability-first**: Call :meth:`get_supported_apis` before assuming
  any endpoint exists.  Firmware versions vary widely.
- **Per-request auth**: HTTP Basic auth on every request (no session state).
- **Preserve existing paths**: The existing :class:`~pytvt.xml_api.NvrClient`
  login/enablement flow is reused via :meth:`ensure_webapi_available` to
  enable the HTTP API service if it's disabled.
"""

from __future__ import annotations

import http.client
import logging
import ssl
from base64 import b64encode
from typing import Any

from . import xml as xml
from .errors import (
    AuthenticationError,
    DeviceOfflineError,
    ServiceNotEnabledError,
    UnsupportedFunctionError,
    WebApiError,
    raise_for_status,
)
from .models import (
    AudioStreamConfig,
    ChannelInfo,
    DateTimeInfo,
    DeviceInfo,
    DiskInfo,
    ImageConfig,
    ImageOsdConfig,
    NatConfig,
    RecordDateResult,
    RecordSegment,
    RecordStatus,
    SnapshotResult,
    VideoStreamConfig,
)

logger = logging.getLogger(__name__)

# Base path for all LAPI endpoints (Section 1.3.1)
LAPI_BASE = "/LAPI/V1.0"


class WebApiClient:
    """Client for the TVT HTTP API (LAPI protocol).

    Uses HTTP Basic auth per request.  All responses are XML.

    Args:
        host: Device IP address or hostname.
        username: Login username (typically ``admin``).
        password: Login password.
        port: HTTP port (default 80).
        timeout: Request timeout in seconds.
        use_https: Use HTTPS instead of HTTP.

    Example::

        client = WebApiClient("192.168.1.100", "admin", "password")
        caps = client.get_supported_apis()
        if "GetDeviceInfo" in caps:
            info = client.get_device_info()
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 80,
        timeout: int = 10,
        use_https: bool = False,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.use_https = use_https

        # Precompute Basic auth header
        creds = b64encode(f"{username}:{password}".encode()).decode()
        self._auth_header = f"Basic {creds}"

        # Cache for supported APIs (populated by get_supported_apis)
        self._supported_apis: set[str] | None = None

    # ── Low-level HTTP ───────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        body: str = "",
        *,
        accept: str = "application/xml",
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, str]:
        """Send an HTTP request with Basic auth.

        Returns:
            (http_status, response_body_bytes, content_type)
        """
        if self.use_https:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(
                self.host,
                self.port,
                timeout=self.timeout,
                context=ctx,
            )
        else:
            conn = http.client.HTTPConnection(
                self.host,
                self.port,
                timeout=self.timeout,
            )

        headers: dict[str, str] = {
            "Authorization": self._auth_header,
            "Accept": accept,
        }
        if body:
            headers["Content-Type"] = "application/xml"
        if extra_headers:
            headers.update(extra_headers)

        try:
            conn.request(method, path, body=body.encode("utf-8") if body else None, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            content_type = resp.getheader("Content-Type", "")
            return resp.status, data, content_type
        except (OSError, http.client.HTTPException) as exc:
            raise DeviceOfflineError(
                f"Cannot reach {self.host}:{self.port}: {exc}",
            ) from exc
        finally:
            conn.close()

    def _post(self, path: str, body: str = "") -> bytes:
        """POST to a LAPI endpoint, check status, return response body bytes.

        Raises:
            WebApiError subclass for non-200 responses or TVT error codes.
        """
        full_path = f"{LAPI_BASE}{path}"
        http_status, data, _content_type = self._request("POST", full_path, body)

        if http_status == 401:
            raise AuthenticationError(
                f"Authentication failed for {self.host}",
                status_code=401,
            )
        if http_status == 403:
            from .errors import PermissionDeniedError

            raise PermissionDeniedError(
                f"Permission denied for {full_path}",
                status_code=403,
            )

        # Parse XML response for TVT-level status codes
        if data and b"<" in data:
            try:
                root = xml.parse_response(data)
                status_code, sub_code, status_string = xml.extract_status(root)
                raise_for_status(status_code, sub_code, status_string)
            except WebApiError:
                raise
            except Exception:
                pass  # Non-XML response or parse error — let caller handle

        if http_status >= 400:
            raise WebApiError(
                f"HTTP {http_status} from {full_path}",
                status_code=http_status,
            )

        return data

    def _get(self, path: str) -> bytes:
        """GET from a LAPI endpoint, check status, return response body bytes."""
        full_path = f"{LAPI_BASE}{path}"
        http_status, data, _content_type = self._request("GET", full_path)

        if http_status == 401:
            raise AuthenticationError(
                f"Authentication failed for {self.host}",
                status_code=401,
            )

        if http_status >= 400:
            raise WebApiError(
                f"HTTP {http_status} from {full_path}",
                status_code=http_status,
            )

        return data

    def _get_raw(self, path: str, accept: str = "*/*") -> tuple[bytes, str]:
        """GET that returns raw bytes + content type (for binary responses like snapshots)."""
        full_path = f"{LAPI_BASE}{path}"
        http_status, data, content_type = self._request(
            "GET",
            full_path,
            accept=accept,
        )

        if http_status == 401:
            raise AuthenticationError(
                f"Authentication failed for {self.host}",
                status_code=401,
            )
        if http_status >= 400:
            raise WebApiError(
                f"HTTP {http_status} from {full_path}",
                status_code=http_status,
            )

        return data, content_type

    # ── Capability detection ─────────────────────────────────────

    def get_supported_apis(self) -> set[str]:
        """Discover which API endpoints the device supports.

        Calls ``GetSupportedAPIs`` (Section 2.1.1) and caches the result.

        Returns:
            Set of supported API names (e.g. ``{"GetDeviceInfo", "GetSnapshot", ...}``).
        """
        data = self._post("/System/SupportedAPIs")
        root = xml.parse_response(data)

        apis: set[str] = set()
        # The response wraps APIs in <SupportedAPIs><SupportedAPI>
        for api_elem in root.iter("SupportedAPI"):
            name = xml.find_text(api_elem, "apiName")
            if name:
                apis.add(name)
        # Also check direct <apiName> children
        for api_elem in root.iter("apiName"):
            if api_elem.text:
                apis.add(api_elem.text.strip())

        self._supported_apis = apis
        logger.debug("Supported APIs on %s: %d endpoints", self.host, len(apis))
        return apis

    def supports(self, api_name: str) -> bool:
        """Check if a specific API is supported by the device.

        Calls :meth:`get_supported_apis` if not already cached.
        """
        if self._supported_apis is None:
            self.get_supported_apis()
        return api_name in self._supported_apis  # type: ignore[operator]

    # ── System commands (Section 2) ──────────────────────────────

    def get_device_info(self) -> DeviceInfo:
        """Get device information (Section 2.1.2).

        Returns:
            :class:`~.models.DeviceInfo` with device name, model, firmware, etc.
        """
        data = self._post("/System/DeviceInfo")
        root = xml.parse_response(data)
        found = root.find("DeviceInfo")
        di = found if found is not None else root

        return DeviceInfo(
            device_name=xml.find_text(di, "deviceName"),
            device_id=xml.find_text(di, "deviceID"),
            device_model=xml.find_text(di, "deviceModel"),
            serial_number=xml.find_text(di, "serialNumber"),
            mac_address=xml.find_text(di, "macAddress"),
            firmware_version=xml.find_text(di, "firmwareVersion"),
            firmware_release_date=xml.find_text(di, "firmwareReleaseDate"),
            boot_version=xml.find_text(di, "bootVersion"),
            hardware_version=xml.find_text(di, "hardwareVersion"),
            device_type=xml.find_text(di, "deviceType"),
            telecontrol_id=xml.find_int(di, "telecontrolID"),
            video_input_num=xml.find_int(di, "videoInputNum"),
            video_output_num=xml.find_int(di, "videoOutputNum"),
            audio_input_num=xml.find_int(di, "audioInputNum"),
            audio_output_num=xml.find_int(di, "audioOutputNum"),
            alarm_input_num=xml.find_int(di, "alarmInputNum"),
            alarm_output_num=xml.find_int(di, "alarmOutputNum"),
        )

    def get_nat_config(self) -> NatConfig:
        """Get NAT / P2P cloud relay configuration.

        Retrieves the device's cloud NAT identifier (the address shown
        on the "Function Panel > NAT" page of the web UI, e.g.
        "NAAC909BNQGD") along with its registration status.

        Returns:
            :class:`~.models.NatConfig` with NAT serial, status, and visit address.

        Raises:
            WebApiError: If the endpoint is unavailable or the device
                does not support NAT configuration.
        """
        try:
            data = self._post("/Network/NAT")
        except Exception:
            # Fallback: some firmware versions use /Network/NATConfig
            data = self._post("/Network/NATConfig")

        root = xml.parse_response(data)
        nat = root.find("NAT") or root.find("NATConfig") or root

        enabled_text = (
            xml.find_text(nat, "enabled")
            or xml.find_text(nat, "natEnabled")
            or xml.find_text(nat, "enable")
        )
        enabled = enabled_text.lower() in ("true", "1", "yes") if enabled_text else False

        nat_serial = (
            xml.find_text(nat, "natSerial")
            or xml.find_text(nat, "serialNumber")
            or xml.find_text(nat, "cloudSN")
            or xml.find_text(nat, "p2pSerial")
            or xml.find_text(nat, "deviceSerial")
        )
        nat_status = (
            xml.find_text(nat, "natStatus")
            or xml.find_text(nat, "status")
        )
        visit_address = (
            xml.find_text(nat, "visitAddress")
            or xml.find_text(nat, "serverAddress")
        )
        security_access_text = (
            xml.find_text(nat, "securityAccess")
            or xml.find_text(nat, "securityMode")
        )
        security_access = security_access_text.lower() in ("true", "1", "yes") if security_access_text else False

        return NatConfig(
            enabled=enabled,
            nat_serial=nat_serial,
            nat_status=nat_status,
            visit_address=visit_address,
            security_access=security_access,
        )

    def get_channel_info(self) -> list[ChannelInfo]:
        """Get all channel information (Section 2.1.4).

        Returns:
            List of :class:`~.models.ChannelInfo`.
        """
        data = self._post("/System/ChannelInfo")
        root = xml.parse_response(data)

        channels: list[ChannelInfo] = []
        for ch_elem in root.iter("ChannelInfo"):
            channels.append(
                ChannelInfo(
                    channel_id=xml.find_int(ch_elem, "channelID"),
                    channel_name=xml.find_text(ch_elem, "channelName"),
                    ip_address=xml.find_text(ch_elem, "ipAddress"),
                    channel_type=xml.find_text(ch_elem, "channelType"),
                    online=xml.find_bool(ch_elem, "online"),
                )
            )
        return channels

    def get_disk_info(self) -> list[DiskInfo]:
        """Get disk information (Section 2.1.3).

        Returns:
            List of :class:`~.models.DiskInfo`.
        """
        data = self._post("/System/DiskInfo")
        root = xml.parse_response(data)

        disks: list[DiskInfo] = []
        for disk_elem in root.iter("DiskInfo"):
            disks.append(
                DiskInfo(
                    disk_id=xml.find_int(disk_elem, "diskID"),
                    disk_name=xml.find_text(disk_elem, "diskName"),
                    disk_type=xml.find_text(disk_elem, "diskType"),
                    status=xml.find_text(disk_elem, "status"),
                    capacity_mb=xml.find_int(disk_elem, "capacity"),
                    free_mb=xml.find_int(disk_elem, "freeSpace"),
                    property=xml.find_text(disk_elem, "property"),
                )
            )
        return disks

    def get_date_and_time(self) -> DateTimeInfo:
        """Get current date/time settings (Section 2.2.1).

        Returns:
            :class:`~.models.DateTimeInfo`.
        """
        data = self._post("/System/DateAndTime")
        root = xml.parse_response(data)
        found = root.find("DateAndTime")
        dt = found if found is not None else root

        return DateTimeInfo(
            mode=xml.find_text(dt, "dateTimeMode"),
            local_time=xml.find_text(dt, "localTime"),
            time_zone=xml.find_text(dt, "timeZone"),
            ntp_server=xml.find_text(dt, "ntpServerAddress"),
            ntp_port=xml.find_int(dt, "ntpPort", 123),
            ntp_interval=xml.find_int(dt, "ntpRefreshInterval", 1440),
        )

    def set_date_and_time(
        self,
        *,
        mode: str | None = None,
        local_time: str | None = None,
        time_zone: str | None = None,
        ntp_server: str | None = None,
    ) -> None:
        """Set date/time configuration (Section 2.2.2).

        Only provided fields are included in the request.

        Args:
            mode: ``"NTP"`` or ``"Manual"``.
            local_time: ISO datetime string for manual mode.
            time_zone: Timezone string (e.g. ``"CST-8:00:00"``).
            ntp_server: NTP server address.
        """
        fields: dict[str, Any] = {}
        if mode is not None:
            fields["dateTimeMode"] = mode
        if local_time is not None:
            fields["localTime"] = local_time
        if time_zone is not None:
            fields["timeZone"] = time_zone
        if ntp_server is not None:
            fields["ntpServerAddress"] = ntp_server

        body = xml.build_set_request("DateAndTime", fields)
        self._post("/System/DateAndTime/Set", body)

    # ── Security commands (Section 8) ────────────────────────────

    def modify_password(
        self,
        old_password: str,
        new_password: str,
        user_id: str = "1",
    ) -> None:
        """Change a user's password (Section 8.1.1).

        Args:
            old_password: Current password.
            new_password: New password.
            user_id: User ID to modify (default ``"1"`` = admin).
        """
        body = xml.build_set_request(
            "ModifyPassword",
            {
                "userName": self.username,
                "oldPassword": old_password,
                "newPassword": new_password,
            },
        )
        self._post(f"/Security/UserManagement/{user_id}/ModifyPassword", body)
        # Update stored password so subsequent requests work
        self.password = new_password
        creds = b64encode(f"{self.username}:{new_password}".encode()).decode()
        self._auth_header = f"Basic {creds}"

    # ── Image commands (Section 3) ───────────────────────────────

    def get_image_config(self, channel_id: int = 1) -> ImageConfig:
        """Get image configuration for a channel (Section 3.2.1).

        Returns:
            :class:`~.models.ImageConfig`.
        """
        data = self._post(f"/Image/Channels/{channel_id}/ImageConfig")
        root = xml.parse_response(data)
        found = root.find("ImageConfig")
        ic = found if found is not None else root

        return ImageConfig(
            channel_id=channel_id,
            brightness=xml.find_int(ic, "brightness"),
            contrast=xml.find_int(ic, "contrast"),
            saturation=xml.find_int(ic, "saturation"),
            sharpness=xml.find_int(ic, "sharpness"),
            exposure_mode=xml.find_text(ic, "exposureMode"),
            white_balance_mode=xml.find_text(ic, "whiteBalanceMode"),
            ir_cut_mode=xml.find_text(ic, "irCutMode"),
            wdr_enabled=xml.find_bool(ic, "WDREnabled"),
            wdr_level=xml.find_int(ic, "WDRLevel"),
        )

    def get_video_stream_config(self, channel_id: int = 1) -> list[VideoStreamConfig]:
        """Get video stream configuration for a channel (Section 3.3.3).

        Returns:
            List of :class:`~.models.VideoStreamConfig` (main, sub, third streams).
        """
        data = self._post(f"/Image/Channels/{channel_id}/VideoStreamConfig")
        root = xml.parse_response(data)

        streams: list[VideoStreamConfig] = []
        for vs_elem in root.iter("VideoStreamConfig"):
            streams.append(
                VideoStreamConfig(
                    channel_id=channel_id,
                    stream_type=xml.find_text(vs_elem, "streamType"),
                    codec=xml.find_text(vs_elem, "codec"),
                    resolution_width=xml.find_int(vs_elem, "resolutionWidth"),
                    resolution_height=xml.find_int(vs_elem, "resolutionHeight"),
                    bitrate_type=xml.find_text(vs_elem, "bitrateType"),
                    bitrate=xml.find_int(vs_elem, "bitrate"),
                    max_bitrate=xml.find_int(vs_elem, "maxBitrate"),
                    frame_rate=xml.find_int(vs_elem, "frameRate"),
                    gop=xml.find_int(vs_elem, "GOP"),
                    quality=xml.find_int(vs_elem, "quality"),
                )
            )
        return streams

    def get_audio_stream_config(self, channel_id: int = 1) -> AudioStreamConfig:
        """Get audio stream configuration for a channel (Section 3.3.1).

        Returns:
            :class:`~.models.AudioStreamConfig`.
        """
        data = self._post(f"/Image/Channels/{channel_id}/AudioStreamConfig")
        root = xml.parse_response(data)
        found = root.find("AudioStreamConfig")
        ac = found if found is not None else root

        return AudioStreamConfig(
            channel_id=channel_id,
            enabled=xml.find_bool(ac, "enabled"),
            codec=xml.find_text(ac, "audioCodecType"),
            sample_rate=xml.find_int(ac, "sampleRate"),
            bitrate=xml.find_int(ac, "audioBitrate"),
        )

    def get_image_osd_config(self, channel_id: int = 1) -> ImageOsdConfig:
        """Get OSD (on-screen display) configuration (Section 3.4.1).

        Returns:
            :class:`~.models.ImageOsdConfig`.
        """
        data = self._post(f"/Image/Channels/{channel_id}/OsdConfig")
        root = xml.parse_response(data)
        found = root.find("ImageOsdConfig")
        osd = found if found is not None else root

        return ImageOsdConfig(
            channel_id=channel_id,
            time_osd_enabled=xml.find_bool(osd, "timeOSDEnabled"),
            channel_name_enabled=xml.find_bool(osd, "channelNameEnabled"),
            channel_name=xml.find_text(osd, "channelName"),
        )

    # ── Snapshot (Section 3.2.5 / 3.2.6) ────────────────────────

    def get_snapshot_webapi(self, channel_id: int = 1) -> SnapshotResult:
        """Get a live snapshot via Web API ``GetSnapshot`` (Section 3.2.5).

        The device returns a JPEG image directly.

        Returns:
            :class:`~.models.SnapshotResult` with image bytes.
        """
        try:
            data, content_type = self._get_raw(
                f"/Image/Channels/{channel_id}/Snapshot",
                accept="image/jpeg",
            )
        except (UnsupportedFunctionError, WebApiError) as exc:
            return SnapshotResult(
                success=False,
                method="webapi",
                error=str(exc),
            )

        if not data or len(data) < 100:
            return SnapshotResult(
                success=False,
                method="webapi",
                error="Empty or invalid snapshot response",
            )

        return SnapshotResult(
            success=True,
            image_data=data,
            content_type=content_type,
            method="webapi",
        )

    def get_snapshot_by_time(
        self,
        channel_id: int = 1,
        date_time: str = "",
    ) -> SnapshotResult:
        """Get a snapshot by time via ``GetSnapshotByTime`` (Section 3.2.6).

        Args:
            channel_id: Channel number.
            date_time: ISO datetime string (e.g. ``"2024-01-15T10:30:00"``).
                If empty, the device returns the most recent available.

        Returns:
            :class:`~.models.SnapshotResult` with image bytes.
        """
        body = ""
        if date_time:
            body = xml.build_set_request(
                "SnapshotByTime",
                {
                    "channelID": channel_id,
                    "dateTime": date_time,
                },
            )

        path = f"/Image/Channels/{channel_id}/SnapshotByTime"
        full_path = f"{LAPI_BASE}{path}"

        try:
            http_status, data, content_type = self._request(
                "POST",
                full_path,
                body,
                accept="image/jpeg",
            )
        except DeviceOfflineError:
            raise
        except (WebApiError, OSError) as exc:
            return SnapshotResult(
                success=False,
                method="webapi_by_time",
                error=str(exc),
            )

        if http_status == 401:
            raise AuthenticationError(
                f"Authentication failed for {self.host}",
                status_code=401,
            )

        if http_status >= 400 or not data or len(data) < 100:
            return SnapshotResult(
                success=False,
                method="webapi_by_time",
                error=f"HTTP {http_status}" if http_status >= 400 else "Empty response",
            )

        return SnapshotResult(
            success=True,
            image_data=data,
            content_type=content_type,
            method="webapi_by_time",
        )

    def get_snapshot(self, channel_id: int = 1) -> SnapshotResult:
        """Get a snapshot using the best available method.

        Priority:
        1. Web API ``GetSnapshot`` if supported
        2. Web API ``GetSnapshotByTime`` if supported
        3. RTSP fallback via existing :func:`pytvt.xml_api.rtsp_snapshot`

        For the RTSP fallback, call :meth:`get_snapshot_with_rtsp_fallback`
        which requires additional connection parameters.

        Returns:
            :class:`~.models.SnapshotResult`.
        """
        # Try GetSnapshot first
        if self.supports("GetSnapshot"):
            result = self.get_snapshot_webapi(channel_id)
            if result.success:
                return result
            logger.debug(
                "GetSnapshot failed on %s CH%d: %s — trying alternatives",
                self.host,
                channel_id,
                result.error,
            )

        # Try GetSnapshotByTime
        if self.supports("GetSnapshotByTime"):
            result = self.get_snapshot_by_time(channel_id)
            if result.success:
                return result
            logger.debug(
                "GetSnapshotByTime failed on %s CH%d: %s",
                self.host,
                channel_id,
                result.error,
            )

        return SnapshotResult(
            success=False,
            method="webapi",
            error="No supported Web API snapshot method available",
        )

    def get_snapshot_with_rtsp_fallback(
        self,
        channel_id: int = 1,
        *,
        rtsp_url: str = "",
        output_path: str = "",
        rtsp_timeout: int = 10,
    ) -> SnapshotResult:
        """Get a snapshot with full fallback chain including RTSP.

        Priority:
        1. Web API snapshot (GetSnapshot / GetSnapshotByTime)
        2. RTSP via ffmpeg (existing :func:`pytvt.xml_api.rtsp_snapshot`)

        For RTSP fallback, either provide ``rtsp_url`` directly or
        ``output_path`` where the JPEG file will be saved.

        Args:
            channel_id: Channel number.
            rtsp_url: Pre-built RTSP URL. If empty, RTSP fallback is skipped.
            output_path: File path for RTSP snapshot output.
            rtsp_timeout: Timeout for ffmpeg RTSP capture.

        Returns:
            :class:`~.models.SnapshotResult`.
        """
        # Try Web API first
        result = self.get_snapshot(channel_id)
        if result.success:
            return result

        # RTSP fallback
        if rtsp_url and output_path:
            logger.debug(
                "Falling back to RTSP for %s CH%d",
                self.host,
                channel_id,
            )
            try:
                from pytvt.xml_api import rtsp_snapshot

                ok = rtsp_snapshot(rtsp_url, output_path, timeout=rtsp_timeout)
                if ok:
                    with open(output_path, "rb") as f:
                        image_data = f.read()
                    return SnapshotResult(
                        success=True,
                        image_data=image_data,
                        content_type="image/jpeg",
                        method="rtsp",
                    )
                else:
                    return SnapshotResult(
                        success=False,
                        method="rtsp",
                        error="ffmpeg RTSP capture failed",
                    )
            except Exception as exc:
                return SnapshotResult(
                    success=False,
                    method="rtsp",
                    error=f"RTSP fallback error: {exc}",
                )

        # Nothing worked
        web_error = result.error or "Web API snapshot not available"
        return SnapshotResult(
            success=False,
            method="none",
            error=f"{web_error}; RTSP fallback not configured",
        )

    # ── Playback / recording (Section 6) ─────────────────────────

    def get_record_status(self) -> list[RecordStatus]:
        """Get recording status for all channels (Section 6.2.1).

        Returns:
            List of :class:`~.models.RecordStatus`.
        """
        data = self._post("/Playback/RecordStatusInfo")
        root = xml.parse_response(data)

        statuses: list[RecordStatus] = []
        for rs_elem in root.iter("RecordStatusInfo"):
            statuses.append(
                RecordStatus(
                    channel_id=xml.find_int(rs_elem, "channelID"),
                    is_recording=xml.find_bool(rs_elem, "isRecording"),
                    record_type=xml.find_text(rs_elem, "recordType"),
                )
            )
        return statuses

    def search_record_date(
        self,
        channel_id: int = 1,
        year: int = 0,
        month: int = 0,
    ) -> RecordDateResult:
        """Search for dates that have recordings (Section 6.1.2).

        Args:
            channel_id: Channel to search.
            year: Year to search (0 = current year).
            month: Month to search (0 = current month).

        Returns:
            :class:`~.models.RecordDateResult` with list of date strings.
        """
        fields: dict[str, Any] = {"channelID": channel_id}
        if year:
            fields["year"] = year
        if month:
            fields["month"] = month

        body = xml.build_set_request("SearchRecordDate", fields)
        data = self._post("/Playback/SearchRecordDate", body)
        root = xml.parse_response(data)

        dates: list[str] = []
        for date_elem in root.iter("date"):
            if date_elem.text:
                dates.append(date_elem.text.strip())

        return RecordDateResult(channel_id=channel_id, dates=dates)

    def search_by_time(
        self,
        channel_id: int = 1,
        start_time: str = "",
        end_time: str = "",
    ) -> list[RecordSegment]:
        """Search recordings by time range (Section 6.1.3).

        Args:
            channel_id: Channel to search.
            start_time: Start time (ISO format, e.g. ``"2024-01-15T00:00:00"``).
            end_time: End time (ISO format).

        Returns:
            List of :class:`~.models.RecordSegment`.
        """
        fields: dict[str, Any] = {"channelID": channel_id}
        if start_time:
            fields["startTime"] = start_time
        if end_time:
            fields["endTime"] = end_time

        body = xml.build_set_request("SearchByTime", fields)
        data = self._post("/Playback/SearchByTime", body)
        root = xml.parse_response(data)

        segments: list[RecordSegment] = []
        for seg_elem in root.iter("RecordSegment"):
            segments.append(
                RecordSegment(
                    channel_id=xml.find_int(seg_elem, "channelID", channel_id),
                    start_time=xml.find_text(seg_elem, "startTime"),
                    end_time=xml.find_text(seg_elem, "endTime"),
                    record_type=xml.find_text(seg_elem, "recordType"),
                )
            )
        return segments

    # ── Service enablement (hybrid flow) ─────────────────────────

    def ensure_webapi_available(
        self,
        *,
        nvr_web_port: int = 80,
    ) -> bool:
        """Ensure the HTTP API service is enabled, using existing NvrClient if needed.

        If :meth:`get_supported_apis` fails because the Web API service
        is disabled, this method uses the existing
        :class:`~pytvt.xml_api.NvrClient` to log in via the NVR CGI and
        enable the API server.

        Args:
            nvr_web_port: HTTP port for the NVR CGI login (may differ from
                the Web API port).

        Returns:
            ``True`` if the Web API is now available.
        """
        # First, just try the Web API directly
        try:
            self.get_supported_apis()
            return True
        except (AuthenticationError, DeviceOfflineError):
            raise  # Credentials wrong or device unreachable — don't retry
        except WebApiError:
            pass  # Might be service-not-enabled

        # Try enabling via existing NvrClient
        logger.info(
            "Web API not available on %s — trying NVR CGI enablement",
            self.host,
        )
        try:
            from pytvt.xml_api import NvrClient

            with NvrClient(
                self.host,
                self.username,
                self.password,
                port=nvr_web_port,
                timeout=self.timeout,
            ) as nvr:
                nvr.login()
                nvr.ensure_services_enabled()
        except Exception as exc:
            raise ServiceNotEnabledError(
                f"Cannot enable Web API on {self.host}: {exc}",
            ) from exc

        # Retry capability check
        try:
            self.get_supported_apis()
            return True
        except WebApiError as exc:
            raise ServiceNotEnabledError(
                f"Web API still unavailable after enablement: {exc}",
            ) from exc
