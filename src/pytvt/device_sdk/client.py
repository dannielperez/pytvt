"""High-level Pythonic wrapper around libdvrnetsdk.so (Linux) and libNetClientSDK.dylib (macOS).

Usage::

    from pytvt.device_sdk import NetSdkClient

    with NetSdkClient() as sdk:
        devices = sdk.discover()
        with sdk.login("10.0.0.1", "admin", "password") as session:
            info = session.device_info()
            jpg  = session.capture_jpeg(channel=0)
            session.ptz(PtzCommand.LEFT, speed=PtzSpeed.SPEED_4, channel=0)

Supports:
- Linux x86_64 and aarch64 with vendor-supplied libdvrnetsdk.so
- macOS x86_64 (or via Rosetta on arm64) with vendor-supplied libNetClientSDK.dylib
"""

from __future__ import annotations

import ctypes as ct
import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from . import bindings as sdk
from .constants import (
    ConnectType,
    DiskProperty,
    DiskStatus,
    PtzCommand,
    PtzSpeed,
    RecordType,
    SdkError,
    StreamType,
)
from .loader import NetSdkUnavailable, ensure_nat_support, load_sdk
from .types import (
    DD_TIME,
    NET_SDK_ALRAM_OUT_STATUS,
    NET_SDK_CH_DEVICE_STATUS,
    NET_SDK_DEV_SUPPORT,
    NET_SDK_DEVICE_DISCOVERY_INFO,
    NET_SDK_DEVICE_IP_INFO,
    NET_SDK_DEVICEINFO,
    NET_SDK_DISK_INFO,
    NET_SDK_IPC_DEVICE_INFO,
    NET_SDK_JPEGPARA,
    NET_SDK_LOG,
    NET_SDK_NVR_DISKREC_DATE_ITEM,
    NET_SDK_REC_FILE,
    NET_SDK_SMART_SUPPORT,
)

logger = logging.getLogger(__name__)
ConnectionMethod = Literal["direct", "nat"]


# ── Result dataclasses ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    """Device found via LAN discovery."""

    ip: str
    mac: str
    product: str
    device_name: str
    net_port: int
    http_port: int
    activated: bool
    firmware_build: int


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Full device information from login or GetDeviceInfo."""

    serial_number: str
    product: str
    device_name: str
    device_type: int
    mac: str
    ip: str
    port: int
    firmware: str
    hardware_version: str
    kernel_version: str
    build_date: str
    video_inputs: int
    audio_inputs: int
    sensor_inputs: int
    sensor_outputs: int


@dataclass(frozen=True, slots=True)
class ChannelStatus:
    """Channel online/offline status."""

    channel: int
    name: str
    online: bool
    channel_type: int


@dataclass(frozen=True, slots=True)
class IpcInfo:
    """IPC camera connected to an NVR channel."""

    channel: int
    ip: str
    port: int
    http_port: int
    manufacturer: str
    model: str
    name: str
    online: bool
    poe: bool


@dataclass(frozen=True, slots=True)
class DiskInfo:
    """Hard disk information."""

    index: int
    status: DiskStatus
    property: DiskProperty
    total_mb: int
    free_mb: int


@dataclass(frozen=True, slots=True)
class RecordingFile:
    """Recording file segment."""

    channel: int
    start: datetime
    stop: datetime
    record_type: int
    locked: bool
    partition: int
    file_index: int


@dataclass(frozen=True, slots=True)
class LogEntry:
    """Device log entry."""

    time: datetime
    major_type: int
    minor_type: int
    user: str
    remote_host: int
    content: str


@dataclass(frozen=True, slots=True)
class RecordingDateRange:
    """NVR disk recording date range."""

    disk_index: int
    disk_count: int
    size_gb: str
    start_date: str
    end_date: str


@dataclass(frozen=True, slots=True)
class AlarmOutStatus:
    """Alarm relay output status."""

    name: str
    online: bool
    active: bool


@dataclass(frozen=True, slots=True)
class EncodeStream:
    """One main-stream encode profile of an NVR channel.

    NVMS-9000 exposes two main-stream record profiles per channel:
    ``continuous`` (the ``<an>`` element — schedule / 24x7 recording) and
    ``event`` (the ``<ae>`` element — motion / alarm / AI recording).
    """

    kind: str  # "continuous" | "event"
    resolution: str  # e.g. "2560x1440"
    fps: int
    bitrate_type: str  # "VBR" | "CBR"
    quality: str  # "low" | "medium" | "higher"
    max_bitrate: int  # QoI cap, in kbps
    audio: bool  # audio track on/off
    codec: str  # "h264" | "h265" | "h265p" (channel-level, shared by both streams)


@dataclass(frozen=True, slots=True)
class NodeEncodeInfo:
    """Per-channel main-stream encode config (``queryNodeEncodeInfo``)."""

    channel: int  # 1-based channel number
    node_id: str  # device node GUID, e.g. "{00000001-0000-0000-0000-000000000000}"
    name: str
    codec: str  # <main enct=...>
    a_gop: int
    m_gop: int
    continuous: EncodeStream | None  # <an>
    event: EncodeStream | None  # <ae>
    supported_resolutions: tuple[str, ...]  # from <mainCaps>
    allowed_bitrates: tuple[int, ...]  # from <mainStreamQualityNote>


@dataclass(frozen=True, slots=True)
class RecordSchedule:
    """Per-channel record-mode switches (``queryRecordScheduleList``).

    Together these describe the "Record Mode" the web UI shows: e.g. all four
    on = "Always(24x7)+Motion+Sensor+AI Record".
    """

    channel: int  # 1-based channel number
    node_id: str
    name: str
    schedule: bool  # scheduleRec — 24x7 continuous
    motion: bool  # motionRec
    alarm: bool  # alarmRec (sensor)
    intelligent: bool  # intelligentRec — AI


# ── Lenient XML helpers for api_call responses ──────────────────────
# Device CGI XML is NOT guaranteed well-formed (camera names routinely contain
# raw '&' and other chars that break strict parsers), so parse with regex.


def _xml_status(xml: str) -> str:
    m = re.search(r"<status>\s*([^<\s]+)\s*</status>", xml or "")
    return m.group(1) if m else ""


def _xml_attrs(tag_body: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)="([^"]*)"', tag_body or ""))


def _xml_items(xml: str):
    """Yield (item_id, inner_body) for each ``<item id="...">...</item>``."""
    for m in re.finditer(r'<item id="([^"]+)"[^>]*>(.*?)</item>', xml or "", re.S):
        yield m.group(1), m.group(2)


def _node_channel(node_id: str) -> int:
    """ "{0000000C-...}" -> 12 (1-based channel number)."""
    try:
        return int(node_id[1:9], 16)
    except (ValueError, IndexError):
        return 0


def _node_guid(channel: int) -> str:
    return f"{{{channel:08X}-0000-0000-0000-000000000000}}"


def _tag_attrs(body: str, tag: str) -> dict[str, str]:
    """Attributes of the first ``<tag ...>`` element in ``body``."""
    t = re.search(rf"<{tag}\s+([^/>]*)/?>", body)
    return _xml_attrs(t.group(1)) if t else {}


def _tag_switch(body: str, tag: str) -> bool:
    """``<tag><switch>true</switch></tag>`` -> True."""
    b = re.search(rf"<{tag}>(.*?)</{tag}>", body, re.S)
    s = re.search(r"<switch>(.*?)</switch>", b.group(1)) if b else None
    return bool(s) and s.group(1).strip() == "true"


@dataclass(frozen=True, slots=True)
class SmartSupport:
    """Smart analytics capabilities for a channel."""

    aoi_entry: bool = False
    aoi_leave: bool = False
    asd: bool = False
    audio_alarm: bool = False
    auto_track: bool = False
    avd: bool = False
    binocular_count: bool = False
    cdd: bool = False
    cpc: bool = False
    fire: bool = False
    heat_map: bool = False
    ipd: bool = False
    loitering: bool = False
    osc: bool = False
    passline: bool = False
    pea: bool = False
    pvd: bool = False
    region_stats: bool = False
    temperature: bool = False
    tripwire: bool = False
    vehicle_plate: bool = False
    vfd: bool = False
    video_metadata: bool = False


@dataclass(frozen=True, slots=True)
class DeviceSupport:
    """Device-level capability flags."""

    thermometry: bool = False
    vfd: bool = False
    vfd_match: bool = False
    thermal: bool = False
    passline: bool = False


# ── Errors ──────────────────────────────────────────────────────────


class NetSdkError(Exception):
    """Raised when an SDK call fails."""

    def __init__(self, message: str, code: SdkError | int | None = None) -> None:
        self.code = code
        super().__init__(f"{message} (error={code})" if code else message)


class NatUnavailableError(NetSdkError):
    """Raised when AutoNAT support is unavailable in the current SDK setup."""


class NetSdkCapabilityError(NetSdkError, NetSdkUnavailable):
    """Raised when the loaded SDK lacks a required capability."""


class NatLoginFailed(NetSdkError):
    """Raised when an AutoNAT login attempt fails."""


class NatTimeoutError(NatLoginFailed):
    """Raised when an AutoNAT login attempt times out."""


# ── Session (logged-in handle) ──────────────────────────────────────


class DeviceSession:
    """Authenticated session to a single device.

    Obtain via :meth:`NetSdkClient.login`.  Use as a context manager
    to ensure logout on exit.
    """

    def __init__(
        self,
        handle: int,
        client: NetSdkClient,
        *,
        connection_method: ConnectionMethod = "direct",
        target: str = "",
        identifier: str | None = None,
        handshake_duration_ms: int | None = None,
    ) -> None:
        self._handle = handle
        self._client = client
        self._connection_method = connection_method
        self._target = target
        self._identifier = identifier
        self._handshake_duration_ms = handshake_duration_ms

    def __enter__(self) -> DeviceSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.logout()

    @property
    def handle(self) -> int:
        return self._handle

    @property
    def connection_method(self) -> ConnectionMethod:
        return self._connection_method

    @property
    def target(self) -> str:
        return self._target

    @property
    def identifier(self) -> str | None:
        return self._identifier

    @property
    def handshake_duration_ms(self) -> int | None:
        return self._handshake_duration_ms

    def _check(self, ok: bool, action: str) -> None:
        if not ok:
            code = sdk._lib.NET_SDK_GetLastError()  # type: ignore[union-attr]
            raise NetSdkError(action, SdkError(code) if code in SdkError._value2member_map_ else code)

    # ── Logout ──────────────────────────────────────────────────

    def logout(self) -> None:
        if self._handle < 0:
            return
        sdk._lib.NET_SDK_Logout(self._handle)  # type: ignore[union-attr]
        logger.debug("Logged out handle=%d", self._handle)
        self._handle = -1

    # ── Device information ──────────────────────────────────────

    def device_info(self) -> DeviceInfo:
        """Query full device information."""
        info = NET_SDK_DEVICEINFO()
        self._check(
            sdk._lib.NET_SDK_GetDeviceInfo(self._handle, ct.byref(info)),  # type: ignore[union-attr]
            "GetDeviceInfo",
        )
        return _device_info_from(info)

    # ── Generic device web-CGI over the SDK transport ───────────

    def api_call(
        self,
        url: str,
        content: str = "",
        *,
        request: str | None = None,
        buf_size: int = 131072,
    ) -> str:
        """Run a device web-CGI command over the SDK session (``NET_SDK_ApiInterface``).

        Reusable "write to device via SDK" primitive — works LAN-direct or
        NAT-tunneled, with no dependence on the device's HTTP port. ``url`` is the
        CGI command (e.g. ``"editPlatformCfg"``); ``content`` is the inner XML
        placed inside the default NVMS-9000 request envelope. Pass a full
        ``request`` to override the envelope (e.g. a camera's ``ipc.com/ver10``
        format). Returns the raw XML response.
        """
        if request is None:
            request = (
                '<?xml version="1.0" encoding="utf-8" ?>'
                '<request version="1.0" systemType="NVMS-9000" clientType="WEB">'
                f"<token>null</token>{content}</request>"
            )
        buf = ct.create_string_buffer(buf_size)
        ret = ct.c_uint(0)
        ok = sdk._lib.NET_SDK_ApiInterface(  # type: ignore[union-attr]
            self._handle,
            request.encode("utf-8"),
            url.encode("utf-8"),
            buf,
            buf_size,
            ct.byref(ret),
        )
        self._check(bool(ok), f"ApiInterface({url})")
        return buf.raw[: ret.value].decode("utf-8", "replace")

    def device_time(self) -> datetime:
        """Query current device clock."""
        t = DD_TIME()
        self._check(
            sdk._lib.NET_SDK_GetDeviceTime(self._handle, ct.byref(t)),  # type: ignore[union-attr]
            "GetDeviceTime",
        )
        return t.to_datetime()

    def channel_status(self, max_channels: int = 128) -> list[ChannelStatus]:
        """Query online/offline status of each video channel."""
        buf = (NET_SDK_CH_DEVICE_STATUS * max_channels)()
        count = ct.c_long(0)
        self._check(
            sdk._lib.NET_SDK_GetDeviceCHStatus(  # type: ignore[union-attr]
                self._handle,
                buf,
                max_channels,
                ct.byref(count),
            ),
            "GetDeviceCHStatus",
        )
        return [
            ChannelStatus(
                channel=buf[i].channel,
                name=buf[i].name.decode("utf-8", errors="replace"),
                online=buf[i].is_online,
                channel_type=buf[i].chlType,
            )
            for i in range(count.value)
        ]

    def ipc_info(self, max_channels: int = 128) -> list[IpcInfo]:
        """Query IPC camera details per NVR channel."""
        buf = (NET_SDK_IPC_DEVICE_INFO * max_channels)()
        count = ct.c_long(0)
        self._check(
            sdk._lib.NET_SDK_GetDeviceIPCInfo(  # type: ignore[union-attr]
                self._handle,
                buf,
                max_channels,
                ct.byref(count),
            ),
            "GetDeviceIPCInfo",
        )
        return [
            IpcInfo(
                channel=buf[i].channel,
                ip=buf[i].szServer.decode("utf-8", errors="replace"),
                port=buf[i].nPort,
                http_port=buf[i].nHttpPort,
                manufacturer=buf[i].manufacturerName.decode("utf-8", errors="replace"),
                model=buf[i].productModel.decode("utf-8", errors="replace"),
                name=buf[i].szChlname.decode("utf-8", errors="replace"),
                online=buf[i].is_online,
                poe=bool(buf[i].bPOEDevice),
            )
            for i in range(count.value)
        ]

    def device_support(self) -> DeviceSupport:
        """Query device-level capability flags."""
        s = NET_SDK_DEV_SUPPORT()
        self._check(
            sdk._lib.NET_SDK_GetDeviceSupportFunction(self._handle, ct.byref(s)),  # type: ignore[union-attr]
            "GetDeviceSupportFunction",
        )
        return DeviceSupport(
            thermometry=s.thermometry,
            vfd=s.vfd,
            vfd_match=s.vfd_match,
            thermal=s.thermal,
            passline=s.passline,
        )

    def smart_support(self, channel: int) -> SmartSupport:
        """Query smart analytics capabilities for a channel."""
        s = NET_SDK_SMART_SUPPORT()
        self._check(
            sdk._lib.NET_SDK_GetSmarEventSupport(  # type: ignore[union-attr]
                self._handle,
                channel,
                ct.byref(s),
            ),
            "GetSmarEventSupport",
        )
        return SmartSupport(
            aoi_entry=bool(s.supportAOIEntry),
            aoi_leave=bool(s.supportAOILeave),
            asd=bool(s.supportASD),
            audio_alarm=bool(s.supportAudioAlarmOut),
            auto_track=bool(s.supportAutoTrack),
            avd=bool(s.supportAvd),
            binocular_count=bool(s.supportBinocularCount),
            cdd=bool(s.supportCdd),
            cpc=bool(s.supportCpc),
            fire=bool(s.supportFire),
            heat_map=bool(s.supportHeatMap),
            ipd=bool(s.supportIpd),
            loitering=bool(s.supportLoitering),
            osc=bool(s.supportOsc),
            passline=bool(s.supportPassLine),
            pea=bool(s.supportPea),
            pvd=bool(s.supportPvd),
            region_stats=bool(s.supportRegionStatistics),
            temperature=bool(s.supportTemperature),
            tripwire=bool(s.supportTripwire),
            vehicle_plate=bool(s.supportVehiclePlate),
            vfd=bool(s.supportVfd),
            video_metadata=bool(s.supportVideoMetadata),
        )

    # ── RTSP URL ────────────────────────────────────────────────

    def rtsp_url(self, channel: int, stream: StreamType = StreamType.MAIN) -> str:
        """Get the RTSP stream URL for a channel."""
        buf = ct.create_string_buffer(256)
        self._check(
            sdk._lib.NET_SDK_GetRtspUrl(  # type: ignore[union-attr]
                self._handle,
                channel,
                stream,
                buf,
            ),
            "GetRtspUrl",
        )
        return buf.value.decode("utf-8", errors="replace")

    # ── JPEG capture ────────────────────────────────────────────

    def capture_jpeg(
        self,
        channel: int,
        *,
        pic_size: int = 0xFF,
        pic_quality: int = 0,
        buf_size: int = 2 * 1024 * 1024,
    ) -> bytes:
        """Capture a JPEG snapshot from a channel.

        Args:
            channel: Video channel index (0-based).
            pic_size: Image size mode (0xFF = current resolution).
            pic_quality: Quality level (0 = best).
            buf_size: Maximum buffer size in bytes (default 2 MB).

        Returns:
            Raw JPEG bytes.
        """
        para = NET_SDK_JPEGPARA(wPicSize=pic_size, wPicQuality=pic_quality)
        buf = ct.create_string_buffer(buf_size)
        returned = ct.c_uint(0)
        self._check(
            sdk._lib.NET_SDK_CaptureJPEGData_V2(  # type: ignore[union-attr]
                self._handle,
                channel,
                ct.byref(para),
                buf,
                buf_size,
                ct.byref(returned),
            ),
            "CaptureJPEGData_V2",
        )
        return buf.raw[: returned.value]

    # ── PTZ control ─────────────────────────────────────────────

    def ptz(
        self,
        command: PtzCommand,
        *,
        channel: int = 0,
        speed: PtzSpeed = PtzSpeed.SPEED_4,
    ) -> None:
        """Send a PTZ command (pan/tilt/zoom/focus/iris)."""
        self._check(
            sdk._lib.NET_SDK_PTZControl_Other(  # type: ignore[union-attr]
                self._handle,
                channel,
                command,
                speed,
            ),
            "PTZControl_Other",
        )

    def ptz_preset(
        self,
        command: PtzCommand,
        preset_index: int,
        *,
        channel: int = 0,
    ) -> None:
        """Manage PTZ presets (set / go to / delete)."""
        self._check(
            sdk._lib.NET_SDK_PTZPreset_Other(  # type: ignore[union-attr]
                self._handle,
                channel,
                command,
                preset_index,
            ),
            "PTZPreset_Other",
        )

    def ptz_cruise(
        self,
        command: PtzCommand,
        cruise_index: int,
        *,
        channel: int = 0,
    ) -> None:
        """Manage PTZ cruises (run / stop / delete)."""
        self._check(
            sdk._lib.NET_SDK_PTZCruise_Other(  # type: ignore[union-attr]
                self._handle,
                channel,
                command,
                cruise_index,
            ),
            "PTZCruise_Other",
        )

    # ── Alarm management ────────────────────────────────────────

    def alarm_subscribe(self) -> int:
        """Open alarm channel and return alarm handle."""
        handle = sdk._lib.NET_SDK_SetupAlarmChan(self._handle)  # type: ignore[union-attr]
        if handle < 0:
            code = sdk._lib.NET_SDK_GetLastError()  # type: ignore[union-attr]
            raise NetSdkError("SetupAlarmChan", code)
        return handle

    def alarm_unsubscribe(self, alarm_handle: int) -> None:
        """Close alarm channel."""
        self._check(
            sdk._lib.NET_SDK_CloseAlarmChan(alarm_handle),  # type: ignore[union-attr]
            "CloseAlarmChan",
        )

    def alarm_out_status(self, max_outputs: int = 32) -> list[AlarmOutStatus]:
        """Query alarm relay output statuses."""
        buf = (NET_SDK_ALRAM_OUT_STATUS * max_outputs)()
        count = ct.c_long(0)
        self._check(
            sdk._lib.NET_SDK_GetAlarmOutStatus(  # type: ignore[union-attr]
                self._handle,
                buf,
                max_outputs,
                ct.byref(count),
            ),
            "GetAlarmOutStatus",
        )
        return [
            AlarmOutStatus(
                name=buf[i].szName.decode("utf-8", errors="replace"),
                online=bool(buf[i].bOnlineStatus),
                active=bool(buf[i].bSwitch),
            )
            for i in range(count.value)
        ]

    # ── Recording search ────────────────────────────────────────

    def find_recordings(
        self,
        channel: int,
        start: datetime,
        stop: datetime,
        record_type: RecordType = RecordType.ALL,
    ) -> list[RecordingFile]:
        """Search for recording files in a time range.

        Args:
            channel: Video channel index (0-based).
            start: Start of search range.
            stop: End of search range.
            record_type: Filter by recording event type.

        Returns:
            List of recording file segments.
        """
        t_start = DD_TIME.from_datetime(start)
        t_stop = DD_TIME.from_datetime(stop)
        find_handle = sdk._lib.NET_SDK_FindFile(  # type: ignore[union-attr]
            self._handle,
            channel,
            record_type,
            ct.byref(t_start),
            ct.byref(t_stop),
        )
        if find_handle < 0:
            code = sdk._lib.NET_SDK_GetLastError()  # type: ignore[union-attr]
            raise NetSdkError("FindFile", code)

        results: list[RecordingFile] = []
        try:
            rec = NET_SDK_REC_FILE()
            while True:
                ret = sdk._lib.NET_SDK_FindNextFile(find_handle, ct.byref(rec))  # type: ignore[union-attr]
                if ret <= 0:
                    break
                results.append(
                    RecordingFile(
                        channel=rec.dwChannel,
                        start=rec.startTime.to_datetime(),
                        stop=rec.stopTime.to_datetime(),
                        record_type=rec.dwRecType,
                        locked=bool(rec.bFileLocked),
                        partition=rec.dwPartition,
                        file_index=rec.dwFileIndex,
                    )
                )
        finally:
            sdk._lib.NET_SDK_FindClose(find_handle)  # type: ignore[union-attr]

        return results

    # ── Recording control ───────────────────────────────────────

    def start_recording(self, channel: int, record_type: int = 0) -> None:
        """Start manual recording on a channel."""
        self._check(
            sdk._lib.NET_SDK_StartDVRRecord(self._handle, channel, record_type),  # type: ignore[union-attr]
            "StartDVRRecord",
        )

    def stop_recording(self, channel: int) -> None:
        """Stop manual recording on a channel."""
        self._check(
            sdk._lib.NET_SDK_StopDVRRecord(self._handle, channel),  # type: ignore[union-attr]
            "StopDVRRecord",
        )

    # ── Disk management ─────────────────────────────────────────

    def disk_info(self) -> list[DiskInfo]:
        """Query all disk statuses."""
        find_handle = sdk._lib.NET_SDK_FindDisk(self._handle)  # type: ignore[union-attr]
        if find_handle < 0:
            code = sdk._lib.NET_SDK_GetLastError()  # type: ignore[union-attr]
            raise NetSdkError("FindDisk", code)

        disks: list[DiskInfo] = []
        try:
            info = NET_SDK_DISK_INFO()
            while sdk._lib.NET_SDK_GetNextDiskInfo(find_handle, ct.byref(info)):  # type: ignore[union-attr]
                disks.append(
                    DiskInfo(
                        index=info.diskIndex,
                        status=DiskStatus(info.diskStatus),
                        property=DiskProperty(info.diskProperty),
                        total_mb=info.diskTotalSpace,
                        free_mb=info.diskFreeSpace,
                    )
                )
        finally:
            sdk._lib.NET_SDK_FindDiskClose(find_handle)  # type: ignore[union-attr]

        return disks

    def recording_days(self, max_items: int = 64) -> list[RecordingDateRange]:
        """Query NVR disk recording date ranges."""
        buf = (NET_SDK_NVR_DISKREC_DATE_ITEM * max_items)()
        count = ct.c_long(0)
        self._check(
            sdk._lib.NET_SDK_GetNvrRecordDays(  # type: ignore[union-attr]
                self._handle,
                buf,
                max_items,
                ct.byref(count),
            ),
            "GetNvrRecordDays",
        )
        return [
            RecordingDateRange(
                disk_index=buf[i].diskIndex,
                disk_count=buf[i].diskCount,
                size_gb=buf[i].szDiskSizeGB.decode("utf-8", errors="replace"),
                start_date=buf[i].szStartDate.decode("utf-8", errors="replace"),
                end_date=buf[i].szEndDate.decode("utf-8", errors="replace"),
            )
            for i in range(count.value)
        ]

    # ── Encode / record config (web CGI over the SDK handle) ─────
    # These drive the NVR "Encode Parameters" and "Record Mode" pages. There is
    # no dedicated SDK struct for them, so they go over NET_SDK_ApiInterface
    # (see api_call) using the NVMS-9000 XML CGI dialect — works LAN or NAT.

    _ENCODE_REQ = (
        '<?xml version="1.0" encoding="utf-8" ?>'
        '<request version="1.0" systemType="NVMS-9000" clientType="WEB">'
        "<requireField><name/><mainCaps/><main/><an/><ae/>"
        "<mainStreamQualityNote/></requireField></request>"
    )

    @staticmethod
    def _encode_stream(kind: str, attrs: dict, codec: str) -> EncodeStream | None:
        if not attrs:
            return None
        return EncodeStream(
            kind=kind,
            resolution=attrs.get("res", ""),
            fps=int(attrs.get("fps", 0) or 0),
            bitrate_type=attrs.get("bitType", ""),
            quality=attrs.get("level", ""),
            max_bitrate=int(attrs.get("QoI", 0) or 0),
            audio=(attrs.get("audio", "").upper() == "ON"),
            codec=codec,
        )

    def node_encode_info(self) -> list[NodeEncodeInfo]:
        """Read every channel's main-stream encode config (``queryNodeEncodeInfo``).

        Returns the continuous (``<an>``) and event (``<ae>``) profiles per
        channel, plus the channel codec, GOP, supported resolutions and the
        allowed bitrate (QoI) values.
        """
        xml = self.api_call("queryNodeEncodeInfo", request=self._ENCODE_REQ)
        if _xml_status(xml) != "success":
            raise NetSdkError("queryNodeEncodeInfo", -1)
        out: list[NodeEncodeInfo] = []
        for node_id, body in _xml_items(xml):
            nm = re.search(r"<name>(.*?)</name>", body, re.S)
            name = re.sub(r"<!\[CDATA\[|\]\]>", "", nm.group(1) if nm else "").strip()
            mn = re.search(r"<main\s+([^/>]*)/?>", body)
            main = _xml_attrs(mn.group(1)) if mn else {}
            codec = main.get("enct", "")
            note = re.search(r"<mainStreamQualityNote>([^<]*)</mainStreamQualityNote>", body)
            bitrates = tuple(int(x) for x in (note.group(1).split(",") if note else []) if x.strip().isdigit())
            out.append(
                NodeEncodeInfo(
                    channel=_node_channel(node_id),
                    node_id=node_id,
                    name=name,
                    codec=codec,
                    a_gop=int(main.get("aGOP", 0) or 0),
                    m_gop=int(main.get("mGOP", 0) or 0),
                    continuous=self._encode_stream("continuous", _tag_attrs(body, "an"), codec),
                    event=self._encode_stream("event", _tag_attrs(body, "ae"), codec),
                    supported_resolutions=tuple(re.findall(r"<res[^>]*>([^<]+)</res>", body)),
                    allowed_bitrates=bitrates,
                )
            )
        return out

    def record_schedule(self) -> list[RecordSchedule]:
        """Read each channel's record-mode switches (``queryRecordScheduleList``)."""
        xml = self.api_call("queryRecordScheduleList")
        if _xml_status(xml) != "success":
            raise NetSdkError("queryRecordScheduleList", -1)
        out: list[RecordSchedule] = []
        for node_id, body in _xml_items(xml):
            nm = re.search(r"<name>(.*?)</name>", body, re.S)
            name = re.sub(r"<!\[CDATA\[|\]\]>", "", nm.group(1) if nm else "").strip()
            out.append(
                RecordSchedule(
                    channel=_node_channel(node_id),
                    node_id=node_id,
                    name=name,
                    schedule=_tag_switch(body, "scheduleRec"),
                    motion=_tag_switch(body, "motionRec"),
                    alarm=_tag_switch(body, "alarmRec"),
                    intelligent=_tag_switch(body, "intelligentRec"),
                )
            )
        return out

    def set_node_encode(
        self,
        channel: int,
        *,
        continuous: dict | None = None,
        event: dict | None = None,
        codec: str | None = None,
        verify: bool = True,
    ) -> NodeEncodeInfo:
        """Patch one channel's main-stream encode config (``editNodeEncodeInfo``).

        Read-modify-write: reads the channel's current profiles, applies only the
        keys you pass, and writes both ``<an>`` and ``<ae>`` back (the device
        edits them as a pair). ``continuous`` / ``event`` accept any of
        :class:`EncodeStream`'s writable fields: ``resolution``, ``fps``,
        ``quality``, ``max_bitrate``, ``bitrate_type``, ``audio`` (bool).

        Example — cut the continuous bitrate cap and force audio off::

            session.set_node_encode(3, continuous={"max_bitrate": 3072, "audio": False},
                                       event={"audio": False})

        Returns the re-read :class:`NodeEncodeInfo` (unless ``verify=False``).
        Raises :class:`NetSdkError` if the device rejects the write.
        """
        node_id = _node_guid(channel)
        current = {n.channel: n for n in self.node_encode_info()}
        if channel not in current:
            raise NetSdkError(f"editNodeEncodeInfo: channel {channel} not found", -1)
        cur = current[channel]
        enct = codec or cur.codec

        def _merge(stream: EncodeStream | None, override: dict | None) -> dict:
            base = {
                "res": stream.resolution if stream else "",
                "fps": stream.fps if stream else 0,
                "bitType": stream.bitrate_type if stream else "VBR",
                "level": stream.quality if stream else "medium",
                "QoI": stream.max_bitrate if stream else 0,
                "audio": "ON" if (stream and stream.audio) else "OFF",
                "type": "main",
            }
            for k, v in (override or {}).items():
                key = {
                    "resolution": "res",
                    "fps": "fps",
                    "bitrate_type": "bitType",
                    "quality": "level",
                    "max_bitrate": "QoI",
                    "audio": "audio",
                }.get(k, k)
                base[key] = "ON" if (k == "audio" and v) else "OFF" if k == "audio" else v
            return base

        an = _merge(cur.continuous, continuous)
        ae = _merge(cur.event, event)

        def _render(tag: str, a: dict) -> str:
            order = ["res", "fps", "bitType", "level", "QoI", "audio", "type"]
            body = " ".join(f'{k}="{a[k]}"' for k in order if a.get(k) not in (None, ""))
            main = f'<main enct="{enct}"'
            if cur.a_gop:
                main += f' aGOP="{cur.a_gop}"'
            if cur.m_gop:
                main += f' mGOP="{cur.m_gop}"'
            return f'<item id="{node_id}"><{tag} {body}/>{main}/></item>'

        req = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<request version="1.0" systemType="NVMS-9000" clientType="WEB" url="editNodeEncodeInfo">'
            f'<content type="list" total="2">{_render("an", an)}{_render("ae", ae)}</content></request>'
        )
        resp = self.api_call("editNodeEncodeInfo", request=req)
        if _xml_status(resp) != "success":
            raise NetSdkError(f"editNodeEncodeInfo(ch{channel}) rejected: {resp[:160]}", -1)
        if not verify:
            return cur
        return {n.channel: n for n in self.node_encode_info()}[channel]

    # ── Log search ──────────────────────────────────────────────

    def find_logs(
        self,
        start: datetime,
        stop: datetime,
        log_type: int = 0,
    ) -> list[LogEntry]:
        """Search device logs in a time range.

        Args:
            start: Start of search range.
            stop: End of search range.
            log_type: Log type filter (0 = all).

        Returns:
            List of log entries.
        """
        t_start = DD_TIME.from_datetime(start)
        t_stop = DD_TIME.from_datetime(stop)
        find_handle = sdk._lib.NET_SDK_FindDVRLog(  # type: ignore[union-attr]
            self._handle,
            log_type,
            ct.byref(t_start),
            ct.byref(t_stop),
        )
        if find_handle < 0:
            code = sdk._lib.NET_SDK_GetLastError()  # type: ignore[union-attr]
            raise NetSdkError("FindDVRLog", code)

        entries: list[LogEntry] = []
        try:
            log = NET_SDK_LOG()
            while True:
                ret = sdk._lib.NET_SDK_FindNextLog(find_handle, ct.byref(log))  # type: ignore[union-attr]
                if ret <= 0:
                    break
                entries.append(
                    LogEntry(
                        time=log.strLogTime.to_datetime(),
                        major_type=log.dwMajorType,
                        minor_type=log.dwMinorType,
                        user=log.sNetUser.decode("utf-8", errors="replace"),
                        remote_host=log.dwRemoteHostAddr,
                        content=log.sContent.decode("utf-8", errors="replace"),
                    )
                )
        finally:
            sdk._lib.NET_SDK_FindLogClose(find_handle)  # type: ignore[union-attr]

        return entries

    # ── Device management ───────────────────────────────────────

    def reboot(self) -> None:
        """Reboot the device."""
        self._check(
            sdk._lib.NET_SDK_RebootDVR(self._handle),  # type: ignore[union-attr]
            "RebootDVR",
        )

    def shutdown(self) -> None:
        """Shut down the device."""
        self._check(
            sdk._lib.NET_SDK_ShutDownDVR(self._handle),  # type: ignore[union-attr]
            "ShutDownDVR",
        )

    def sync_time(self, timestamp: int | None = None) -> None:
        """Set device time to a Unix timestamp (default: now)."""
        if timestamp is None:
            timestamp = int(datetime.now().timestamp())
        self._check(
            sdk._lib.NET_SDK_ChangTime(self._handle, timestamp),  # type: ignore[union-attr]
            "ChangTime",
        )

    def restore_defaults(self) -> None:
        """Restore device to factory defaults."""
        self._check(
            sdk._lib.NET_SDK_RestoreConfig(self._handle),  # type: ignore[union-attr]
            "RestoreConfig",
        )

    def export_config(self, file_path: str) -> None:
        """Export device configuration to a file."""
        self._check(
            sdk._lib.NET_SDK_GetConfigFile(  # type: ignore[union-attr]
                self._handle,
                file_path.encode("utf-8"),
            ),
            "GetConfigFile",
        )

    def import_config(self, file_path: str) -> None:
        """Import device configuration from a file."""
        self._check(
            sdk._lib.NET_SDK_SetConfigFile(  # type: ignore[union-attr]
                self._handle,
                file_path.encode("utf-8"),
            ),
            "SetConfigFile",
        )

    # ── Firmware upgrade ────────────────────────────────────────

    def upgrade(self, firmware_path: str) -> int:
        """Start firmware upgrade and return upgrade handle.

        Check progress with :meth:`NetSdkClient.upgrade_progress`.
        """
        handle = sdk._lib.NET_SDK_Upgrade(  # type: ignore[union-attr]
            self._handle,
            firmware_path.encode("utf-8"),
        )
        if handle < 0:
            code = sdk._lib.NET_SDK_GetLastError()  # type: ignore[union-attr]
            raise NetSdkError("Upgrade", code)
        return handle

    # ── Access control ──────────────────────────────────────────

    def unlock_door(self, channel: int = 0) -> None:
        """Trigger door unlock on an access control device."""
        self._check(
            sdk._lib.NET_SDK_UnlockAccessControl(self._handle, channel),  # type: ignore[union-attr]
            "UnlockAccessControl",
        )


# ── NetSdkClient ────────────────────────────────────────────────────


class NetSdkClient:
    """Main entry point for the TVT NetSDK.

    Use as a context manager to ensure SDK init/cleanup::

        with NetSdkClient() as client:
            devices = client.discover()
    """

    def __init__(
        self,
        *,
        sdk_path: str | None = None,
        connect_timeout: int = 5000,
        recv_timeout: int = 5000,
        reconnect_interval: int = 0,
    ) -> None:
        self._sdk_path = sdk_path
        self._connect_timeout = connect_timeout
        self._recv_timeout = recv_timeout
        self._reconnect_interval = reconnect_interval
        self._lib = load_sdk(sdk_path=sdk_path)
        sdk.bind(self._lib)
        if not self._lib.NET_SDK_Init():
            raise NetSdkError("NET_SDK_Init failed")
        self._lib.NET_SDK_SetConnectTime(connect_timeout, recv_timeout)
        if reconnect_interval > 0:
            self._lib.NET_SDK_SetReconnect(reconnect_interval, True)
        else:
            self._lib.NET_SDK_SetReconnect(0, False)
        logger.debug("NetSDK initialized (v%s)", self.sdk_version())

    def __enter__(self) -> NetSdkClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Release SDK resources."""
        if self._lib is not None:
            self._lib.NET_SDK_Cleanup()
            self._lib = None
            logger.debug("NetSDK cleaned up")

    def _last_error(self) -> int:
        return sdk._lib.NET_SDK_GetLastError()  # type: ignore[union-attr]

    @staticmethod
    def _sdk_error(code: int) -> SdkError | int:
        return SdkError(code) if code in SdkError._value2member_map_ else code

    @contextmanager
    def _temporary_connect_timeout(self, timeout_ms: int | None):
        if timeout_ms is None:
            yield
            return

        self._lib.NET_SDK_SetConnectTime(timeout_ms, timeout_ms)
        try:
            yield
        finally:
            self._lib.NET_SDK_SetConnectTime(self._connect_timeout, self._recv_timeout)

    # ── Version info ────────────────────────────────────────────

    def sdk_version(self) -> str:
        """Return the SDK's *internal API version* as a 'major.minor.patch' string.

        This is decoded from ``NET_SDK_GetSDKVersion`` (a packed uint) and is
        NOT the vendor's package/firmware version. For example the
        ``1.3.2.202601161500`` device-SDK drop (build 90116) reports a packed
        value of ``0x00010003`` here, i.e. ``"0.1.3"``. The vendor does not
        document the encoding, so treat this string as an opaque internal
        identifier only; use :meth:`sdk_build_version` (build number) or the
        SDK package/manifest version to identify the actual release.
        """
        v = self._lib.NET_SDK_GetSDKVersion()
        return f"{(v >> 24) & 0xFF}.{(v >> 16) & 0xFF}.{v & 0xFFFF}"

    def sdk_build_version(self) -> int:
        """Return the SDK build number (e.g. 90116 for the 1.3.2 device drop).

        More reliable than :meth:`sdk_version` for distinguishing releases:
        the 1.2.1.036 drop reports 60222, the 1.3.2 drop reports 90116.
        """
        return self._lib.NET_SDK_GetSDKBuildVersion()

    # ── Logging ─────────────────────────────────────────────────

    def enable_log(
        self,
        log_dir: str = "/tmp/pytvt_netsdk",
        auto_delete: bool = True,
        level: int = 3,
    ) -> None:
        """Enable SDK file logging."""
        self._lib.NET_SDK_SetLogToFile(True, log_dir.encode("utf-8"), auto_delete, level)

    # ── Discovery ───────────────────────────────────────────────

    def discover(self, max_devices: int = 256, timeout_ms: int = 3000) -> list[DiscoveredDevice]:
        """Discover TVT devices on the local network.

        Args:
            max_devices: Maximum number of devices to return.
            timeout_ms: Discovery timeout in milliseconds.

        Returns:
            List of discovered devices.
        """
        buf = (NET_SDK_DEVICE_DISCOVERY_INFO * max_devices)()
        count = self._lib.NET_SDK_DiscoverDevice(buf, max_devices, timeout_ms)
        if count < 0:
            raise NetSdkError("DiscoverDevice", self._last_error())
        return [
            DiscoveredDevice(
                ip=buf[i].strIP.decode("utf-8", errors="replace"),
                mac=buf[i].mac_str,
                product=buf[i].productType.decode("utf-8", errors="replace"),
                device_name=buf[i].devName.decode("utf-8", errors="replace"),
                net_port=buf[i].netPort,
                http_port=buf[i].httpPort,
                activated=buf[i].is_activated,
                firmware_build=buf[i].softBuildDate,
            )
            for i in range(count)
        ]

    # ── Device activation ───────────────────────────────────────

    def activate(self, ip: str, port: int, password: str) -> None:
        """Activate an uninitialized device with a new admin password."""
        ok = self._lib.NET_SDK_ActiveDevice(
            ip.encode("utf-8"),
            port,
            password.encode("utf-8"),
        )
        if not ok:
            raise NetSdkError("ActiveDevice", self._last_error())

    def activate_by_mac(self, mac: str, password: str) -> None:
        """Activate a device by MAC address."""
        ok = self._lib.NET_SDK_ActiveDeviceByMac(
            mac.encode("utf-8"),
            password.encode("utf-8"),
        )
        if not ok:
            raise NetSdkError("ActiveDeviceByMac", self._last_error())

    def set_device_ip_by_mac(
        self,
        mac: str,
        password: str,
        *,
        ip: str = "",
        netmask: str = "",
        gateway: str = "",
        dns1: str = "",
        dns2: str = "",
        dhcp: bool = False,
    ) -> None:
        """Readdress a LAN device by MAC using the vendor provisioning path.

        Prefers the newer ``NET_SDK_SetDeviceIP`` helper when exported by the
        loaded library and falls back to ``NET_SDK_ModifyDeviceNetInfo`` on
        SDK builds that only expose the legacy struct-based API.
        """
        mac = mac.strip().upper()
        if not mac:
            raise ValueError("mac is required")

        set_device_ip = getattr(self._lib, "NET_SDK_SetDeviceIP", None)
        if set_device_ip is not None:
            ok = set_device_ip(
                mac.encode("utf-8"),
                password.encode("utf-8"),
                ip.encode("utf-8"),
                netmask.encode("utf-8"),
                gateway.encode("utf-8"),
                dns1.encode("utf-8"),
                dns2.encode("utf-8"),
            )
            if not ok:
                raise NetSdkError("SetDeviceIP", self._last_error())
            return

        modify_net_info = getattr(self._lib, "NET_SDK_ModifyDeviceNetInfo", None)
        if modify_net_info is None:
            raise NetSdkCapabilityError(
                "Loaded TVT NetSDK does not export NET_SDK_SetDeviceIP or NET_SDK_ModifyDeviceNetInfo.",
            )

        payload = NET_SDK_DEVICE_IP_INFO()
        payload.szMac = mac.encode("utf-8")
        payload.szIpAddr = ip.encode("utf-8")
        payload.szMark = netmask.encode("utf-8")
        payload.szGateway = gateway.encode("utf-8")
        payload.szPassword = password.encode("utf-8")
        payload.szDdns1 = dns1.encode("utf-8")
        payload.szDdns2 = dns2.encode("utf-8")
        payload.ucIPMode = 1 if dhcp else 0

        ok = modify_net_info(ct.byref(payload))
        if not ok:
            raise NetSdkError("ModifyDeviceNetInfo", self._last_error())

    # ── Login ───────────────────────────────────────────────────

    def login(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 9008,
    ) -> DeviceSession:
        """Log in to a device and return a session handle.

        Args:
            host: Device IP address or hostname.
            username: Login username (usually "admin").
            password: Login password.
            port: SDK data port (default 9008).

        Returns:
            :class:`DeviceSession` — use as context manager.

        Raises:
            NetSdkError: On authentication or connection failure.
        """
        info = NET_SDK_DEVICEINFO()
        started_at = time.perf_counter()
        handle = self._lib.NET_SDK_Login(
            host.encode("utf-8"),
            port,
            username.encode("utf-8"),
            password.encode("utf-8"),
            ct.byref(info),
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        if handle < 0:
            code = self._last_error()
            raise NetSdkError(
                f"Login to {host}:{port} as {username}",
                self._sdk_error(code),
            )
        logger.info(
            "Connected via direct SDK to %s:%d in %dms — %s (%s) SN=%s",
            host,
            port,
            elapsed_ms,
            info.deviceName.decode("utf-8", errors="replace"),
            info.firmwareVersion.decode("utf-8", errors="replace"),
            info.szSN.decode("utf-8", errors="replace"),
        )
        return DeviceSession(
            handle,
            self,
            connection_method="direct",
            target=f"{host}:{port}",
            handshake_duration_ms=elapsed_ms,
        )

    def login_nat(
        self,
        identifier: str,
        username: str,
        password: str,
        timeout: float | None = None,
        *,
        nat_server: str | None = None,
        nat_port: int | None = None,
        connect_type: ConnectType | int | str = ConnectType.NAT20,
    ) -> DeviceSession:
        """Log in to a device through the SDK's NAT/P2P flow."""
        device_id = identifier.strip()
        if not device_id:
            raise ValueError("identifier is required for NAT login")

        try:
            ensure_nat_support(self._sdk_path)
        except NetSdkUnavailable as exc:
            raise NatUnavailableError(str(exc)) from exc

        resolved_connect_type = self._coerce_connect_type(connect_type)
        timeout_ms = None if timeout is None else max(1, int(timeout * 1000))
        nat_host = (nat_server or "").strip()
        nat_service_port = 0 if nat_port is None else nat_port

        if resolved_connect_type is ConnectType.NAT20 and nat_host and nat_service_port > 0:
            ok = self._lib.NET_SDK_SetNat2Addr(
                nat_host.encode("utf-8"),
                nat_service_port,
            )
            if not ok:
                raise NatUnavailableError(
                    f"Failed to configure NAT2 server {nat_host}:{nat_service_port}",
                    self._sdk_error(self._last_error()),
                )

        info = NET_SDK_DEVICEINFO()
        started_at = time.perf_counter()
        with self._temporary_connect_timeout(timeout_ms):
            handle = self._lib.NET_SDK_LoginEx(
                nat_host.encode("utf-8"),
                nat_service_port,
                username.encode("utf-8"),
                password.encode("utf-8"),
                ct.byref(info),
                int(resolved_connect_type),
                device_id.encode("utf-8"),
            )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)

        if handle < 0:
            code = self._last_error()
            error = self._sdk_error(code)
            message = f"NAT login to {device_id} as {username} via {resolved_connect_type.name}"
            if error in {SdkError.NETWORK_RECV_TIMEOUT, SdkError.COMMAND_TIMEOUT}:
                raise NatTimeoutError(message, error)
            raise NatLoginFailed(message, error)

        logger.info(
            "Connected via %s SDK to %s in %dms — %s (%s) SN=%s",
            resolved_connect_type.name.lower(),
            device_id,
            elapsed_ms,
            info.deviceName.decode("utf-8", errors="replace"),
            info.firmwareVersion.decode("utf-8", errors="replace"),
            info.szSN.decode("utf-8", errors="replace"),
        )
        return DeviceSession(
            handle,
            self,
            connection_method="nat",
            target=device_id,
            identifier=device_id,
            handshake_duration_ms=elapsed_ms,
        )

    def connect(
        self,
        *,
        method: ConnectionMethod = "direct",
        username: str,
        password: str,
        host: str | None = None,
        port: int = 9008,
        identifier: str | None = None,
        timeout: float | None = None,
        nat_server: str | None = None,
        nat_port: int | None = None,
        connect_type: ConnectType | int | str = ConnectType.NAT20,
        fallback_to_direct: bool = True,
    ) -> DeviceSession:
        """Connect to a device using either direct or NAT-backed login."""
        if method == "direct":
            if not host:
                raise ValueError("host is required for direct connections")
            return self.login(host, username, password, port=port)

        if method != "nat":
            raise ValueError(f"Unsupported connection method: {method!r}")

        if not identifier:
            raise ValueError("identifier is required for NAT connections")

        try:
            return self.login_nat(
                identifier,
                username,
                password,
                timeout=timeout,
                nat_server=nat_server,
                nat_port=nat_port,
                connect_type=connect_type,
            )
        except (NatUnavailableError, NatLoginFailed, NatTimeoutError) as exc:
            if fallback_to_direct and host:
                logger.warning(
                    "NAT login failed for %s (%s); falling back to direct %s:%d",
                    identifier,
                    exc,
                    host,
                    port,
                )
                return self.login(host, username, password, port=port)
            raise

    @staticmethod
    def _coerce_connect_type(connect_type: ConnectType | int | str) -> ConnectType:
        if isinstance(connect_type, ConnectType):
            return connect_type
        if isinstance(connect_type, str):
            normalized = connect_type.strip().upper()
            if normalized == "NAT":
                return ConnectType.NAT
            if normalized in {"NAT20", "NAT2", "P2P", "P2P2"}:
                return ConnectType.NAT20
            raise ValueError(f"Unsupported NAT connect type: {connect_type!r}")
        return ConnectType(connect_type)


class TVTClient(NetSdkClient):
    """Compatibility-friendly alias for NetSdkClient with ``connect`` support."""

    # ── Upgrade progress ────────────────────────────────────────

    @staticmethod
    def upgrade_progress(upgrade_handle: int) -> int:
        """Check firmware upgrade progress (0-100, or negative on error)."""
        progress = ct.c_int(0)
        ret = sdk._lib.NET_SDK_GetUpgradeProgress(  # type: ignore[union-attr]
            upgrade_handle,
            ct.byref(progress),
        )
        return progress.value if ret >= 0 else ret

    @staticmethod
    def upgrade_close(upgrade_handle: int) -> None:
        """Close upgrade handle."""
        sdk._lib.NET_SDK_CloseUpgradeHandle(upgrade_handle)  # type: ignore[union-attr]


# ── Helpers ─────────────────────────────────────────────────────────


def _device_info_from(info: NET_SDK_DEVICEINFO) -> DeviceInfo:
    return DeviceInfo(
        serial_number=info.szSN.decode("utf-8", errors="replace"),
        product=info.deviceProduct.decode("utf-8", errors="replace"),
        device_name=info.deviceName.decode("utf-8", errors="replace"),
        device_type=info.deviceType,
        mac=info.mac_str,
        ip=info.ip_str,
        port=info.devicePort,
        firmware=info.firmwareVersion.decode("utf-8", errors="replace"),
        hardware_version=info.hardwareVersion.decode("utf-8", errors="replace"),
        kernel_version=info.kernelVersion.decode("utf-8", errors="replace"),
        build_date=info.build_date_str,
        video_inputs=info.videoInputNum,
        audio_inputs=info.audioInputNum,
        sensor_inputs=info.sensorInputNum,
        sensor_outputs=info.sensorOutputNum,
    )
