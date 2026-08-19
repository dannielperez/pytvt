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
import math
import re
import threading
import time
import warnings
import weakref
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from .._jpeg import strip_jpeg_nul_padding
from ..models import PlatformAccessConfig
from ..platform_access import parse_platform_access_config, response_status
from . import bindings as sdk
from .constants import (
    ConnectType,
    DiskProperty,
    DiskStatus,
    FaceMatchCommand,
    PtzCommand,
    PtzSpeed,
    RecordType,
    RollingGateExecute,
    SdkError,
    SmartEventType,
    StreamType,
    TripwireDirection,
)
from .loader import NetSdkUnavailable, ensure_nat_support, load_sdk
from .plate_events import (
    DEFAULT_MAX_BUFFER_BYTES,
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_IMAGE_BYTES,
    DEFAULT_MAX_PAYLOAD_BYTES,
    DropPolicy,
    PlateEventStream,
    PlateSource,
    PlateSubscriptionInfo,
    _SmartSubscriptionToken,
)
from .types import (
    CALL_RECORD,
    CALL_RECORD_QUERY_PARAM,
    CLOUD_UPGRADE_INFO,
    DD_TIME,
    DD_TIME_EX,
    NET_DVR_IVE_POINT_T,
    NET_DVR_SUBSCRIBE_REPLY,
    NET_SDK_ALRAM_OUT_STATUS,
    NET_SDK_CH_DEVICE_STATUS,
    NET_SDK_CH_SNAP_FACE_IMG_LIST,
    NET_SDK_CH_SNAP_FACE_IMG_LIST_SEARCH,
    NET_SDK_DEV_SUPPORT,
    NET_SDK_DEVICE_DISCOVERY_INFO,
    NET_SDK_DEVICE_IP_INFO,
    NET_SDK_DEVICEINFO,
    NET_SDK_DISK_INFO,
    NET_SDK_FACE_IMG_INFO_CH,
    NET_SDK_FACE_INFO_IMG_DATA,
    NET_SDK_IPC_DEVICE_INFO,
    NET_SDK_LOG,
    NET_SDK_NVR_DISKREC_DATE_ITEM,
    NET_SDK_REC_FILE,
    NET_SDK_RECORD_DEVICE,
    NET_SDK_RECORD_STATUS,
    NET_SDK_RECORD_STATUS_EX,
    NET_SDK_SMART_SUPPORT,
    NET_SDK_USER_INFO,
    RULE_POINT,
    RULE_POINT_LIST,
    SUBSCRIBE_CALLBACK_V2,
    TALK_DATA_CALLBACK,
    UNLOCK_PARAM,
    NVRChlInfoStruct,
    NVRChlListStruct,
)

logger = logging.getLogger(__name__)
ConnectionMethod = Literal["direct", "nat"]

# The TVT smart-event callback and SDK lifecycle are process-global. Track
# ownership across every NetSdkClient instance, not just within one wrapper.
_PROCESS_SDK_LOCK = threading.RLock()
_PROCESS_SUBSCRIBE_OWNER: NetSdkClient | None = None
_LIVE_CLIENTS: weakref.WeakSet[NetSdkClient] = weakref.WeakSet()
_CALLBACK_DRAIN_TIMEOUT_SECONDS = 5.0
_DEFAULT_SUBSCRIPTION_SETUP_TIMEOUT_SECONDS = 30.0
_MAX_SUBSCRIPTION_SETUP_TIMEOUT_SECONDS = 300.0


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
class NativeFaceCapture:
    """Typed index entry for a face image stored by a legacy NVR.

    ``captured_at_device`` is deliberately naive: the NetSDK result contains
    the recorder wall clock but no UTC offset. Consumers must attach the
    device/site timezone or use their own receipt time; pytvt will not silently
    reinterpret it in the bridge/container timezone.
    """

    _native_channel: int = field(repr=False)
    channel_index: int | None = field(init=False)
    channel_deleted: bool = field(init=False)
    captured_at_device: datetime
    device_time_ticks: int
    snapshot_image_id: int
    target_image_id: int
    panorama: bool

    def __post_init__(self) -> None:
        if (
            isinstance(self._native_channel, bool)
            or not isinstance(self._native_channel, int)
            or not 0 <= self._native_channel <= 255
        ):
            raise ValueError("_native_channel must be a NetSDK identity between 0 and 255")
        deleted = self._native_channel == 255
        object.__setattr__(self, "channel_deleted", deleted)
        object.__setattr__(self, "channel_index", None if deleted else self._native_channel)
        if not isinstance(self.captured_at_device, datetime):
            raise TypeError("captured_at_device must be a datetime")
        if self.captured_at_device.utcoffset() is not None:
            raise ValueError("captured_at_device must be a naive recorder-local datetime")
        if (
            isinstance(self.device_time_ticks, bool)
            or not isinstance(self.device_time_ticks, int)
            or not 0 <= self.device_time_ticks < 10_000_000
        ):
            raise ValueError("device_time_ticks must be between 0 and 9999999")
        for name, value in (
            ("snapshot_image_id", self.snapshot_image_id),
            ("target_image_id", self.target_image_id),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"{name} must be an unsigned 32-bit integer")
        if not isinstance(self.panorama, bool):
            raise TypeError("panorama must be a bool")


@dataclass(frozen=True, slots=True)
class NativeFaceCapturePage:
    """One bounded page of native face capture index entries."""

    captures: tuple[NativeFaceCapture, ...]
    complete: bool
    page: int


def _validate_face_search_input(
    channel: int,
    start: datetime,
    end: datetime,
    page: int,
    page_size: int,
) -> None:
    """Keep local and HTTP face search backends on one input contract."""
    if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel < 255:
        raise ValueError("channel must be a routeable NetSDK index between 0 and 254")
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise TypeError("start and end must be datetimes")
    if start.utcoffset() is not None or end.utcoffset() is not None:
        raise ValueError("start and end must be naive recorder-local datetimes")
    if start > end:
        raise ValueError("start must be before or equal to end")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 0xFFFFFFFF:
        raise ValueError("page must be an unsigned 32-bit positive integer")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")


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
class DeviceUser:
    """A user account on the device (NET_SDK_GetDeviceUsers)."""

    name: str
    group_name: str
    group_guid: str
    email: str
    enabled: bool
    allow_modify_password: bool
    close_permission_control: bool


@dataclass(frozen=True, slots=True)
class RecordStatus:
    """Per-channel recording status (NET_SDK_GetRecordStatus)."""

    channel: int
    record_type: int


@dataclass(frozen=True, slots=True)
class RecordStatusEx:
    """Extended per-channel recording status (NET_SDK_GetRecordStatusEx)."""

    channel: int
    device_name: str
    record_type: int
    record_status: int
    stream_type: int
    resolution: str
    frame_rate: int
    bitrate_cap_kbps: int
    bitrate_type: int
    quality_level: int


@dataclass(frozen=True, slots=True)
class RecordDevice:
    """A recording channel node (NET_SDK_GetRecordDevice)."""

    channel: int
    node_id: str
    name: str


@dataclass(frozen=True, slots=True)
class NvrChannelInfo:
    """IPC details for one NVR channel (NET_SDK_GetNvrChlInfo)."""

    software_version: int
    detailed_software_version: str
    product_type: int
    device_type: int
    supports_soft_encrypt: bool
    mac: str


@dataclass(frozen=True, slots=True)
class CallLogEntry:
    """Intercom call-log entry (NET_SDK_GetCallLog)."""

    missed: bool
    device_name: str
    call_type: int
    record_time: datetime
    start_time: datetime
    end_time: datetime
    channel_id: str
    device_type: int
    community_no: int
    sector_no: int
    building_no: int
    unit_no: int
    floor_no: int
    door_station_no: int


@dataclass(frozen=True, slots=True)
class CloudUpgradeStatus:
    """Cloud-upgrade state for the NVR or one channel (NET_SDK_GetCloudUpgradeInfo)."""

    channel: int  # -1 == the NVR itself
    state: str
    progress_pct: float  # 0.0-100.0 (device reports basis points)
    version: str
    new_version_guid: str


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
    supported_codecs: tuple[str, ...]  # from <mainCaps supEnct=...>, e.g. ("h264","h265","h265p")
    allowed_bitrates: tuple[int, ...]  # from <mainStreamQualityNote>
    supported_bitrate_types: tuple[str, ...] = ()  # from <mainCaps bitType=...>
    # NVMS-9000's stable ``level`` protocol enum; firmware does not advertise it.
    supported_quality_levels: tuple[str, ...] = (
        "low",
        "lower",
        "medium",
        "higher",
        "high",
        "highest",
    )


@dataclass(frozen=True, slots=True)
class NodeEncodeUpdateResult:
    """Outcome of a guarded encode mutation verified through a fresh session."""

    status: Literal[
        "updated",
        "conflict",
        "write_unconfirmed",
        "verification_mismatch",
        "verification_unavailable",
    ]
    channel: NodeEncodeInfo
    connection_method: ConnectionMethod


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


@dataclass(frozen=True, slots=True)
class MotionConfig:
    """Per-channel motion-detector configuration (``queryMotion``).

    ``mask`` is the detector grid as binary row strings: ``"1"`` enables a
    cell and ``"0"`` excludes it.  Sensitivity bounds and supported hold times
    come from the device because they vary across camera firmware.
    """

    channel: int  # 1-based channel number
    node_id: str
    enabled: bool
    sensitivity: int
    sensitivity_min: int
    sensitivity_max: int
    hold_time_seconds: int | None
    allowed_hold_times: tuple[int, ...]
    mask: tuple[str, ...]
    person_filter: bool | None
    car_filter: bool | None

    @property
    def rows(self) -> int:
        return len(self.mask)

    @property
    def columns(self) -> int:
        return len(self.mask[0]) if self.mask else 0

    @property
    def active_cells(self) -> int:
        return sum(row.count("1") for row in self.mask)

    @property
    def total_cells(self) -> int:
        return self.rows * self.columns

    @property
    def coverage_percent(self) -> float:
        return (self.active_cells / self.total_cells * 100.0) if self.total_cells else 0.0


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

    @property
    def invalidates_session(self) -> bool:
        """Whether this failure makes an authenticated handle unsafe to reuse.

        TVT reports both connection failures and operation-local rejections
        through ``NET_SDK_GetLastError``.  Consumers that pool authenticated
        handles must not turn a bad channel, unsupported operation, or local
        file/size failure into an unnecessary logout and four-second relogin.
        Uncoded, unknown, and recognized-but-unreviewed failures remain
        fail-closed until their scope is proven.
        """
        if self.code is None:
            return True
        try:
            code = SdkError(self.code)
        except ValueError:
            return True
        return code not in _SESSION_PRESERVING_SDK_ERRORS


_SESSION_PRESERVING_SDK_ERRORS = frozenset(
    {
        SdkError.CHANNEL_ERROR,
        SdkError.PARAMETER_ERROR,
        SdkError.NOSUPPORT,
        SdkError.BUSY,
        SdkError.DVR_OPRATE_FAILED,
    }
)


class NetSdkCredentialRejectedError(NetSdkError):
    """Raised when a recorder explicitly rejects login credentials."""


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
        # Keep ctypes voice-talk callbacks alive while the SDK holds them
        # (handle -> (ctypes thunk, python target)); see start_voice_talk.
        self._voice_callbacks: dict[int, tuple[object, object]] = {}
        # Smart-event callback registration is process-global, so at most one
        # plate stream may be owned by this session/client at a time.
        self._plate_stream: PlateEventStream | None = None

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

    @staticmethod
    def _require(symbol: str):
        """Return the bound SDK function, or raise if the library lacks it.

        The 1.3.2 additions are absent from older ``libdvrnetsdk.so`` drops; a
        wrapper that calls one must fail with a clear capability error rather
        than an ``AttributeError`` from the mock/real library.
        """
        fn = getattr(sdk._lib, symbol, None)
        if fn is None:
            raise NetSdkCapabilityError(f"Loaded TVT NetSDK does not export {symbol} (needs the 1.3.2+ device SDK).")
        return fn

    # ── Logout ──────────────────────────────────────────────────

    def logout(self) -> None:
        if self._handle < 0:
            return
        if self._plate_stream is not None and not self._plate_stream.closed:
            # A failed native unsubscribe leaves retry tokens attached to this
            # live session. Preserve the handle and skip logout so close() can
            # be retried with the same native session.
            self._plate_stream.close()
        handle = self._handle
        try:
            sdk._lib.NET_SDK_Logout(handle)  # type: ignore[union-attr]
            logger.debug("Logged out handle=%d", handle)
        finally:
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

    def query_platform_access(self) -> PlatformAccessConfig:
        """Read Platform Access (auto-report) settings over the SDK session.

        Same ``queryPlatformCfg`` CGI as :meth:`pytvt.NvrClient.query_platform_access`
        but tunnelled through ``NET_SDK_ApiInterface`` on the authenticated
        handle, so it works LAN-direct **and** NAT-tunnelled (recorders reached
        by serial only).  Raises :class:`NetSdkError` when the device reports a
        non-success status.
        """
        data = self.api_call("queryPlatformCfg")
        status, code = response_status(data)
        if status is not None and status != "success":
            detail = f" errorCode={code}" if code else ""
            raise NetSdkError(f"queryPlatformCfg failed: status={status}{detail}")
        return parse_platform_access_config(data)

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
        prefer_file: bool = False,
    ) -> bytes:
        """Capture a JPEG snapshot from a channel.

        Args:
            channel: Video channel index (0-based).
            pic_size: Retained for call compatibility only. NetSDK 1.3.2's
                stream-less capture APIs take no size or resolution
                parameter — the recorder/IPC returns its configured snapshot
                stream (verified live 2026-08-19: ``CaptureJPEGData_V2``,
                ``CaptureJPEGFile_V2`` and ``CaptureJpeg`` all return the same
                frame). Callers that need a larger still change the camera's
                snapshot/sub-stream setting or grab a main-stream frame.
            pic_quality: Retained for call compatibility only (see ``pic_size``).
            buf_size: Maximum buffer size in bytes (default 2 MB).
            prefer_file: Try the SDK's file-based capture before the in-memory
                API. This is useful for legacy recorder firmware where
                ``NET_SDK_CaptureJPEGData_V2`` can block instead of returning
                an error. The default preserves the existing in-memory-first
                behavior for callers without that compatibility requirement.

        Returns:
            Raw JPEG bytes.
        """
        # Recorder firmware can report a capture length that includes NUL
        # padding after the JPEG EOI marker (observed fleet-wide on UAT,
        # 2026-08-18). Normalize that vendor quirk here so every consumer —
        # runtime, bridge, or direct — receives one strictly framed image.
        return strip_jpeg_nul_padding(
            self._capture_jpeg_raw(
                channel,
                pic_size=pic_size,
                pic_quality=pic_quality,
                buf_size=buf_size,
                prefer_file=prefer_file,
            ),
        )

    def _capture_jpeg_raw(
        self,
        channel: int,
        *,
        pic_size: int,
        pic_quality: int,
        buf_size: int,
        prefer_file: bool,
    ) -> bytes:
        if prefer_file:
            try:
                return self._capture_jpeg_file(channel, buf_size=buf_size)
            except (NetSdkCapabilityError, NetSdkError):
                return self._capture_jpeg_data(
                    channel,
                    pic_size=pic_size,
                    pic_quality=pic_quality,
                    buf_size=buf_size,
                )

        try:
            return self._capture_jpeg_data(
                channel,
                pic_size=pic_size,
                pic_quality=pic_quality,
                buf_size=buf_size,
            )
        except NetSdkError:
            # Some TVT recorder/firmware combinations reject the in-memory
            # capture function while supporting the SDK's file-based variant.
            # Keep this compatibility fallback inside pytvt so every caller
            # gets the same vendor behavior and HTTP bridges stay transport-only.
            return self._capture_jpeg_file(channel, buf_size=buf_size)

    def _capture_jpeg_data(
        self,
        channel: int,
        *,
        pic_size: int,
        pic_quality: int,
        buf_size: int,
    ) -> bytes:
        # ``pic_size``/``pic_quality`` are accepted for compatibility; the
        # NetSDK 1.3.2 data API has no such parameters (see bindings.py).
        del pic_size, pic_quality
        buf = ct.create_string_buffer(buf_size)
        returned = ct.c_uint(0)
        self._check(
            sdk._lib.NET_SDK_CaptureJPEGData_V2(  # type: ignore[union-attr]
                self._handle,
                channel,
                buf,
                buf_size,
                ct.byref(returned),
            ),
            "CaptureJPEGData_V2",
        )
        return buf.raw[: returned.value]

    def _capture_jpeg_file(self, channel: int, *, buf_size: int) -> bytes:
        capture_file = self._require("NET_SDK_CaptureJPEGFile_V2")
        try:
            with TemporaryDirectory(prefix="pytvt-snapshot-") as directory:
                snapshot_path = Path(directory) / f"channel-{channel}.jpg"
                self._check(
                    capture_file(
                        self._handle,
                        channel,
                        str(snapshot_path).encode("utf-8"),
                    ),
                    "CaptureJPEGFile_V2",
                )
                size = snapshot_path.stat().st_size
                if size > buf_size:
                    raise NetSdkError(
                        "CaptureJPEGFile_V2 snapshot exceeds configured byte limit",
                    )
                return snapshot_path.read_bytes()
        except (NetSdkCapabilityError, NetSdkError):
            raise
        except OSError as file_error:
            raise NetSdkError(
                "CaptureJPEGFile_V2 did not produce a readable snapshot",
            ) from file_error

    # ── Native face capture search ─────────────────────────────

    def search_face_captures(
        self,
        channel: int,
        start: datetime,
        end: datetime,
        *,
        page: int = 1,
        page_size: int = 100,
    ) -> NativeFaceCapturePage:
        """Search the legacy NVR's stored face captures without fetching images.

        Channel numbers are NetSDK indices (0-based). ``start`` and ``end`` are
        naive recorder-local wall-clock values because the native structure
        carries no timezone. Results copy out every vendor-owned structure
        before this method returns.
        """
        _validate_face_search_input(channel, start, end, page, page_size)

        operate = self._require("NET_SDK_FaceMatchOperate")
        query = NET_SDK_CH_SNAP_FACE_IMG_LIST_SEARCH(
            dwChannel=channel,
            startTime=DD_TIME_EX.from_datetime(start),
            endTime=DD_TIME_EX.from_datetime(end),
            pageIndex=page,
            pageSize=page_size,
        )
        output_size = 100 * 1024
        output = ct.create_string_buffer(output_size)
        returned = ct.c_uint(0)
        self._check(
            operate(
                self._handle,
                FaceMatchCommand.SEARCH_CHANNEL_CAPTURE_LIST,
                ct.byref(query),
                ct.sizeof(query),
                output,
                output_size,
                ct.byref(returned),
            ),
            "FaceMatchOperate(SEARCH_CHANNEL_CAPTURE_LIST)",
        )
        if returned.value > output_size:
            raise NetSdkError("Face capture search returned an invalid byte count")
        if returned.value < ct.sizeof(NET_SDK_CH_SNAP_FACE_IMG_LIST):
            raise NetSdkError("Face capture search returned a truncated result header")

        result = ct.cast(output, ct.POINTER(NET_SDK_CH_SNAP_FACE_IMG_LIST)).contents
        if result.listNum > page_size:
            raise NetSdkError("Face capture search returned more entries than requested")
        if result.listNum and not result.pCHFaceImgItem:
            raise NetSdkError("Face capture search returned a null item pointer")

        captures: list[NativeFaceCapture] = []
        for index in range(result.listNum):
            native = result.pCHFaceImgItem[index]
            captured_at = native.frameTime.to_datetime().replace(
                microsecond=min(native.frameTime.nMicrosecond // 10, 999_999)
            )
            captures.append(
                NativeFaceCapture(
                    _native_channel=int(native.chl),
                    captured_at_device=captured_at,
                    device_time_ticks=int(native.frameTime.nMicrosecond),
                    snapshot_image_id=int(native.snapImgId),
                    target_image_id=int(native.targetImgId),
                    panorama=bool(native.isPanorama),
                )
            )
        return NativeFaceCapturePage(captures=tuple(captures), complete=bool(result.bEnd), page=page)

    def get_face_capture_image(
        self,
        capture: NativeFaceCapture,
        *,
        max_image_bytes: int = 8 * 1024 * 1024,
    ) -> bytes:
        """Copy one stored legacy-NVR face JPEG into Python-owned bytes."""
        if not isinstance(capture, NativeFaceCapture):
            raise TypeError("capture must be a NativeFaceCapture")
        if (
            isinstance(max_image_bytes, bool)
            or not isinstance(max_image_bytes, int)
            or not 1 <= max_image_bytes <= 32 * 1024 * 1024
        ):
            raise ValueError("max_image_bytes must be between 1 and 33554432")

        operate = self._require("NET_SDK_FaceMatchOperate")
        native = NET_SDK_FACE_IMG_INFO_CH(
            frameTime=DD_TIME_EX.from_datetime(capture.captured_at_device),
            snapImgId=capture.snapshot_image_id,
            targetImgId=capture.target_image_id,
            chl=capture._native_channel,
            isPanorama=int(capture.panorama),
        )
        native.frameTime.nMicrosecond = capture.device_time_ticks
        output_size = max_image_bytes + ct.sizeof(NET_SDK_FACE_INFO_IMG_DATA)
        output = ct.create_string_buffer(output_size)
        returned = ct.c_uint(0)
        self._check(
            operate(
                self._handle,
                FaceMatchCommand.SEARCH_CHANNEL_CAPTURE_IMAGE,
                ct.byref(native),
                ct.sizeof(native),
                output,
                output_size,
                ct.byref(returned),
            ),
            "FaceMatchOperate(SEARCH_CHANNEL_CAPTURE_IMAGE)",
        )
        if returned.value > output_size:
            raise NetSdkError("Face capture image returned an invalid byte count")
        if returned.value < ct.sizeof(NET_SDK_FACE_INFO_IMG_DATA):
            raise NetSdkError("Face capture image returned a truncated result header")

        result = ct.cast(output, ct.POINTER(NET_SDK_FACE_INFO_IMG_DATA)).contents
        if result.imgLen > max_image_bytes:
            raise NetSdkError("Face capture image exceeds configured byte limit")
        if result.imgLen and not result.imgData:
            raise NetSdkError("Face capture image returned a null data pointer")
        return ct.string_at(result.imgData, result.imgLen) if result.imgLen else b""

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

    def subscribe_plate_events(
        self,
        channels: Iterable[int],
        *,
        commands: Iterable[SmartEventType] = (
            SmartEventType.VEHICLE,
            SmartEventType.NVR_VEHICLE,
        ),
        max_events: int = DEFAULT_MAX_EVENTS,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
        max_buffer_bytes: int = DEFAULT_MAX_BUFFER_BYTES,
        drop_policy: DropPolicy = DropPolicy.DROP_OLDEST,
        setup_timeout: float = _DEFAULT_SUBSCRIPTION_SETUP_TIMEOUT_SECONDS,
        experimental: bool = False,
    ) -> PlateEventStream:
        """Subscribe to typed IPC/NVR plate events for selected channels.

        Registration is atomic: if any command/channel subscription fails, all
        successful registrations are compensated and the global callback is
        cleared. The returned stream is bounded and must be closed (or used as
        a context manager) before another process-global callback is installed.
        ``setup_timeout`` bounds the registration sequence between native calls;
        it cannot interrupt a single native call that never returns.
        """
        if not experimental:
            raise NetSdkCapabilityError(
                "Live plate subscriptions are provisional because vendor renewal timing is unvalidated; "
                "pass experimental=True only for an approved read-only conformance pilot."
            )
        if (
            not math.isfinite(setup_timeout)
            or setup_timeout <= 0
            or setup_timeout > _MAX_SUBSCRIPTION_SETUP_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "setup_timeout must be finite, positive, and no greater than "
                f"{_MAX_SUBSCRIPTION_SETUP_TIMEOUT_SECONDS:g} seconds"
            )
        if self._handle < 0:
            raise NetSdkError("subscribe_plate_events requires an active device session")
        if self._plate_stream is not None and not self._plate_stream.closed:
            raise NetSdkCapabilityError("This device session already owns an active plate-event stream.")
        if self._client._active_plate_stream is not None and not self._client._active_plate_stream.closed:
            raise NetSdkCapabilityError("The NetSDK client already has an active process-global plate-event stream.")
        if self._client._subscribe_callback is not None:
            raise NetSdkCapabilityError(
                "The process-global smart-event callback is already occupied; clear it before plate subscription."
            )

        channel_ids = tuple(dict.fromkeys(int(channel) for channel in channels))
        if not channel_ids or len(channel_ids) > 256 or any(channel < 0 for channel in channel_ids):
            raise ValueError("channels must contain 1 to 256 non-negative channel ids")
        command_values = tuple(dict.fromkeys(SmartEventType(int(command)) for command in commands))
        allowed = {SmartEventType.VEHICLE, SmartEventType.NVR_VEHICLE}
        if not command_values or any(command not in allowed for command in command_values):
            raise ValueError("commands must contain VEHICLE and/or NVR_VEHICLE")

        setup_deadline = time.monotonic() + setup_timeout

        def _check_setup_deadline() -> None:
            if time.monotonic() >= setup_deadline:
                raise NetSdkError("Plate-event subscription setup deadline exceeded")

        subscribe = self._require("NET_SDK_SmartSubscrib")
        unsubscribe = self._require("NET_SDK_UnSmartSubscrib")
        stream = PlateEventStream(
            max_events=max_events,
            max_payload_bytes=max_payload_bytes,
            max_image_bytes=max_image_bytes,
            max_buffer_bytes=max_buffer_bytes,
            drop_policy=drop_policy,
        )
        tokens: list[_SmartSubscriptionToken] = []

        def _close() -> None:
            first_error: NetSdkError | None = None
            remaining: list[_SmartSubscriptionToken] = []
            for token in reversed(tokens):
                result = ct.c_int(0)
                ok = bool(
                    unsubscribe(
                        self._handle,
                        int(token.command),
                        token.info.channel_id,
                        ct.c_char_p(token.server_address),
                        ct.byref(result),
                    )
                )
                if not ok:
                    remaining.append(token)
                    if first_error is None:
                        first_error = NetSdkError(
                            "UnSmartSubscrib",
                            self._client._sdk_error(self._client._last_error()),
                        )
            tokens[:] = reversed(remaining)
            stream._configure(subscriptions=tuple(tokens), closer=_close)
            try:
                self._client.subscribe_v2(None)
            except NetSdkError as exc:
                if first_error is None:
                    first_error = exc
            if first_error is not None:
                # Even if callback deregistration succeeded, failed native
                # unsubscribe tokens contaminate this process-global session.
                # Reserve ownership so another client cannot start or clean up
                # the shared SDK until this stream retries or its owner exits.
                with _PROCESS_SDK_LOCK:
                    global _PROCESS_SUBSCRIBE_OWNER
                    _PROCESS_SUBSCRIBE_OWNER = self._client
                raise first_error
            if self._client._active_plate_stream is stream:
                self._client._active_plate_stream = None
            if self._plate_stream is stream:
                self._plate_stream = None

        stream._configure(subscriptions=(), closer=_close)
        self._client.subscribe_v2(
            stream.ingest,
            max_payload_bytes=max_payload_bytes,
            on_rejected=stream.reject_callback,
        )
        self._client._active_plate_stream = stream
        self._plate_stream = stream
        try:
            _check_setup_deadline()
            for command in command_values:
                for channel_id in channel_ids:
                    _check_setup_deadline()
                    reply = NET_DVR_SUBSCRIBE_REPLY()
                    if not subscribe(self._handle, int(command), channel_id, ct.byref(reply)):
                        raise NetSdkError(
                            "SmartSubscrib",
                            self._client._sdk_error(self._client._last_error()),
                        )
                    server_address = bytes(reply.serverAddress).split(b"\0", 1)[0]
                    tokens.append(
                        _SmartSubscriptionToken(
                            command=command,
                            info=PlateSubscriptionInfo(
                                source=(PlateSource.IPC if command is SmartEventType.VEHICLE else PlateSource.NVR),
                                channel_id=channel_id,
                            ),
                            server_address=server_address,
                            current_time=int(reply.currentTime),
                            termination_time=int(reply.terminationTime),
                        )
                    )
                    # Some supported NetSDK builds leave ``serverAddress``
                    # empty while still returning success.  The matching
                    # ``NET_SDK_UnSmartSubscrib`` ABI accepts that empty value;
                    # the boolean return remains the authoritative lifecycle
                    # result.  Rejecting it here turns a working subscription
                    # into a false setup failure.
                    _check_setup_deadline()
        except Exception as setup_error:
            try:
                stream.close()
            except Exception as rollback_error:
                raise NetSdkError(
                    f"Plate-event subscription setup failed ({setup_error}); rollback also failed ({rollback_error})"
                ) from setup_error
            raise
        stream._configure(subscriptions=tuple(tokens), closer=_close)
        return stream

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
            caps = re.search(r"<mainCaps\s+([^>]*)>", body)
            cap_attrs = _xml_attrs(caps.group(1)) if caps else {}
            sup_enct = cap_attrs.get("supEnct", "")
            bitrate_types = cap_attrs.get("bitType", "")
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
                    supported_codecs=tuple(x for x in sup_enct.split(",") if x),
                    allowed_bitrates=bitrates,
                    supported_bitrate_types=tuple(x for x in bitrate_types.split(",") if x),
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

    def motion_config(self, channel: int) -> MotionConfig:
        """Read one channel's motion detector settings (``queryMotion``).

        The request is routed through :meth:`api_call`, so it works for direct
        and NAT SDK sessions without opening or authenticating to the NVR's HTTP
        service.
        """
        if channel < 1:
            raise ValueError("channel must be a 1-based positive integer")
        node_id = _node_guid(channel)
        request = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<request version="1.0" systemType="NVMS-9000" clientType="WEB">'
            f"<condition><chlId>{node_id}</chlId></condition>"
            "<requireField><param/></requireField></request>"
        )
        xml = self.api_call("queryMotion", request=request)
        if _xml_status(xml) != "success":
            raise NetSdkError(f"queryMotion(ch{channel}) rejected: {xml[:160]}", -1)

        chl = re.search(r'<chl\s+id="([^"]+)"[^>]*>(.*?)</chl>', xml, re.S)
        if not chl:
            raise NetSdkError(f"queryMotion(ch{channel}) returned no channel config", -1)
        response_id, body = chl.groups()
        if response_id.upper() != node_id:
            raise NetSdkError(f"queryMotion(ch{channel}) returned config for {response_id}", -1)
        param = re.search(r"<param>(.*?)</param>", body, re.S)
        if not param:
            raise NetSdkError(f"queryMotion(ch{channel}) returned no param block", -1)
        body = param.group(1)

        switch = re.search(r"<switch>\s*(true|false)\s*</switch>", body)
        sensitivity = re.search(r"<sensitivity\s+([^>]*)>\s*(\d+)\s*</sensitivity>", body)
        area = re.search(r"<area\s+([^>]*)>(.*?)</area>", body, re.S)
        if not switch or not sensitivity or not area:
            raise NetSdkError(f"queryMotion(ch{channel}) returned an incomplete config", -1)

        sensitivity_attrs = _xml_attrs(sensitivity.group(1))
        try:
            sensitivity_min = int(sensitivity_attrs["min"])
            sensitivity_max = int(sensitivity_attrs["max"])
            sensitivity_value = int(sensitivity.group(2))
        except (KeyError, ValueError) as exc:
            raise NetSdkError(f"queryMotion(ch{channel}) returned invalid sensitivity bounds", -1) from exc
        if not sensitivity_min <= sensitivity_value <= sensitivity_max:
            raise NetSdkError(f"queryMotion(ch{channel}) returned sensitivity outside its bounds", -1)

        area_attrs = _xml_attrs(area.group(1))
        item_type = re.search(r"<itemType\s+([^>]*)/?>", area.group(2))
        item_attrs = _xml_attrs(item_type.group(1)) if item_type else {}
        mask = tuple(x.strip() for x in re.findall(r"<item>\s*([^<]+?)\s*</item>", area.group(2), re.S))
        try:
            expected_rows = int(area_attrs["count"])
            min_columns = int(item_attrs["minLen"])
            max_columns = int(item_attrs["maxLen"])
        except (KeyError, ValueError) as exc:
            raise NetSdkError(f"queryMotion(ch{channel}) returned invalid area dimensions", -1) from exc
        if (
            not mask
            or len(mask) != expected_rows
            or min_columns != max_columns
            or any(len(row) != max_columns or set(row) - {"0", "1"} for row in mask)
        ):
            raise NetSdkError(f"queryMotion(ch{channel}) returned an invalid area mask", -1)

        hold = re.search(r"<holdTime(?:\s+[^>]*)?>\s*(\d+)\s*</holdTime>", body)
        hold_note = re.search(r"<holdTimeNote>\s*([^<]*)\s*</holdTimeNote>", body)

        def _object_filter(name: str) -> bool | None:
            group = re.search(rf"<{name}>(.*?)</{name}>", body, re.S)
            value = re.search(r"<switch>\s*(true|false)\s*</switch>", group.group(1)) if group else None
            return (value.group(1) == "true") if value else None

        return MotionConfig(
            channel=channel,
            node_id=response_id,
            enabled=switch.group(1) == "true",
            sensitivity=sensitivity_value,
            sensitivity_min=sensitivity_min,
            sensitivity_max=sensitivity_max,
            hold_time_seconds=int(hold.group(1)) if hold else None,
            allowed_hold_times=tuple(int(x) for x in re.findall(r"\d+", hold_note.group(1))) if hold_note else (),
            mask=mask,
            person_filter=_object_filter("person"),
            car_filter=_object_filter("car"),
        )

    def set_motion_config(
        self,
        channel: int,
        *,
        enabled: bool | None = None,
        sensitivity: int | None = None,
        hold_time_seconds: int | None = None,
        mask: tuple[str, ...] | list[str] | None = None,
        person_filter: bool | None = None,
        car_filter: bool | None = None,
        verify: bool = True,
    ) -> MotionConfig:
        """Safely patch one channel's motion settings (``editMotion``).

        This is a read-modify-write operation: unspecified values are preserved,
        values are checked against the camera-reported bounds, and the effective
        configuration is re-read by default.  A no-op request performs no write.
        """
        current = self.motion_config(channel)
        new_enabled = current.enabled if enabled is None else enabled
        new_sensitivity = current.sensitivity if sensitivity is None else sensitivity
        if not current.sensitivity_min <= new_sensitivity <= current.sensitivity_max:
            raise ValueError(f"sensitivity must be between {current.sensitivity_min} and {current.sensitivity_max}")

        new_hold = current.hold_time_seconds if hold_time_seconds is None else hold_time_seconds
        if hold_time_seconds is not None:
            if current.hold_time_seconds is None:
                raise ValueError("this channel does not expose a writable hold time")
            if current.allowed_hold_times and hold_time_seconds not in current.allowed_hold_times:
                raise ValueError(f"hold_time_seconds must be one of {current.allowed_hold_times}")

        new_mask = current.mask if mask is None else tuple(mask)
        if len(new_mask) != current.rows or any(
            len(row) != current.columns or set(row) - {"0", "1"} for row in new_mask
        ):
            raise ValueError(f"mask must contain {current.rows} binary rows of {current.columns} columns")

        def _filter(current_value: bool | None, override: bool | None, name: str) -> bool | None:
            if override is not None and current_value is None:
                raise ValueError(f"this channel does not expose a writable {name} filter")
            return current_value if override is None else override

        new_person = _filter(current.person_filter, person_filter, "person")
        new_car = _filter(current.car_filter, car_filter, "car")
        effective = (
            new_enabled,
            new_sensitivity,
            new_hold,
            new_mask,
            new_person,
            new_car,
        )
        original = (
            current.enabled,
            current.sensitivity,
            current.hold_time_seconds,
            current.mask,
            current.person_filter,
            current.car_filter,
        )
        if effective == original:
            return current

        filters = ""
        if new_person is not None or new_car is not None:
            filters = "<objectFilter>"
            if new_car is not None:
                filters += f"<car><switch>{str(new_car).lower()}</switch></car>"
            if new_person is not None:
                filters += f"<person><switch>{str(new_person).lower()}</switch></person>"
            filters += "</objectFilter>"
        hold_xml = f'<holdTime unit="s">{new_hold}</holdTime>' if new_hold is not None else ""
        rows = "".join(f"<item>{row}</item>" for row in new_mask)
        content = (
            f'<content><chl id="{current.node_id}"><param>'
            f"<switch>{str(new_enabled).lower()}</switch>{filters}"
            f'<sensitivity min="{current.sensitivity_min}" max="{current.sensitivity_max}">'
            f"{new_sensitivity}</sensitivity>{hold_xml}"
            f'<area type="list" count="{current.rows}">'
            f'<itemType minLen="{current.columns}" maxLen="{current.columns}"/>{rows}'
            "</area></param></chl></content>"
        )
        response = self.api_call("editMotion", content=content)
        if _xml_status(response) != "success":
            raise NetSdkError(f"editMotion(ch{channel}) rejected: {response[:160]}", -1)
        if not verify:
            return current

        updated = self.motion_config(channel)
        actual = (
            updated.enabled,
            updated.sensitivity,
            updated.hold_time_seconds,
            updated.mask,
            updated.person_filter,
            updated.car_filter,
        )
        if actual != effective:
            raise NetSdkError(f"editMotion(ch{channel}) verification mismatch", -1)
        return updated

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

        def _tag(tag: str, a: dict) -> str:
            order = ["res", "fps", "bitType", "level", "QoI", "audio", "type"]
            body = " ".join(f'{k}="{a[k]}"' for k in order if a.get(k) not in (None, ""))
            return f"<{tag} {body}/>"

        main = f'<main enct="{enct}"'
        if cur.a_gop:
            main += f' aGOP="{cur.a_gop}"'
        if cur.m_gop:
            main += f' mGOP="{cur.m_gop}"'
        main += "/>"
        # Both <an> and <ae> MUST go in a SINGLE <item>: the device silently
        # applies only the first item when the same id appears twice.
        item = f'<item id="{node_id}">{_tag("an", an)}{_tag("ae", ae)}{main}</item>'
        req = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<request version="1.0" systemType="NVMS-9000" clientType="WEB" url="editNodeEncodeInfo">'
            f'<content type="list" total="1">{item}</content></request>'
        )
        resp = self.api_call("editNodeEncodeInfo", request=req)
        if _xml_status(resp) != "success":
            raise NetSdkError(f"editNodeEncodeInfo(ch{channel}) rejected: {resp[:160]}", -1)
        if not verify:
            return cur
        # NOTE: the device serves cached encode config for the life of a session,
        # so this same-session re-read can be stale; reconnect for authoritative
        # verification.
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

    def unlock_door_ex(self, *, lock_id: int = 0, channel: int = 0) -> None:
        """Trigger a specific door lock (``NET_SDK_UnlockAccessControlEx``, 1.3.2+).

        ``lock_id`` is the 1-based lock number; ``0`` unlocks every lock on the
        device. Prefer this over :meth:`unlock_door` on multi-door controllers.
        """
        fn = self._require("NET_SDK_UnlockAccessControlEx")
        param = UNLOCK_PARAM(lockID=lock_id)
        self._check(fn(self._handle, channel, param), "UnlockAccessControlEx")

    def rolling_gate_control(self, action: RollingGateExecute) -> None:
        """Drive a rolling/roller gate (``NET_SDK_RollingGateControl``, 1.3.2+)."""
        fn = self._require("NET_SDK_RollingGateControl")
        self._check(fn(self._handle, int(action)), "RollingGateControl")

    def call_log(
        self,
        start: datetime,
        stop: datetime,
        *,
        page: int = 1,
        page_size: int = 100,
    ) -> tuple[list[CallLogEntry], int]:
        """Query the intercom call log (``NET_SDK_GetCallLog``, 1.3.2+).

        Returns ``(entries, total)`` where ``total`` is the server-side count for
        the time window (for pagination); ``entries`` is the current page.
        """
        fn = self._require("NET_SDK_GetCallLog")
        query = CALL_RECORD_QUERY_PARAM(
            startTime=DD_TIME.from_datetime(start),
            endTime=DD_TIME.from_datetime(stop),
            pageIndex=max(1, page),
            pageSize=page_size,
        )
        buf = (CALL_RECORD * page_size)()
        num = ct.c_uint(0)
        total = ct.c_uint(0)
        self._check(
            fn(self._handle, ct.byref(query), buf, page_size, ct.byref(num), ct.byref(total)),
            "GetCallLog",
        )
        entries = [
            CallLogEntry(
                missed=bool(buf[i].missedCall),
                device_name=buf[i].devName.decode("utf-8", errors="replace"),
                call_type=buf[i].callType,
                record_time=buf[i].recordTime.to_datetime(),
                start_time=buf[i].startTime.to_datetime(),
                end_time=buf[i].endTime.to_datetime(),
                channel_id=buf[i].chlId.as_string,
                device_type=buf[i].devType,
                community_no=buf[i].communityNo,
                sector_no=buf[i].sectorNo,
                building_no=buf[i].buildingNo,
                unit_no=buf[i].unitNo,
                floor_no=buf[i].floorNo,
                door_station_no=buf[i].doorStationNo,
            )
            for i in range(min(num.value, page_size))
        ]
        return entries, total.value

    # ── User accounts ───────────────────────────────────────────

    def device_users(self, max_users: int = 64) -> list[DeviceUser]:
        """List device user accounts (``NET_SDK_GetDeviceUsers``, 1.3.2+)."""
        fn = self._require("NET_SDK_GetDeviceUsers")
        buf = (NET_SDK_USER_INFO * max_users)()
        count = ct.c_long(max_users)
        self._check(fn(self._handle, buf, ct.byref(count)), "GetDeviceUsers")
        return [
            DeviceUser(
                name=buf[i].m_szUserName.decode("utf-8", errors="replace"),
                group_name=buf[i].szGroup.szGroupName.decode("utf-8", errors="replace"),
                group_guid=buf[i].szGroup.szGroupGuid.decode("utf-8", errors="replace"),
                email=buf[i].m_szEmail.decode("utf-8", errors="replace"),
                enabled=bool(buf[i].m_szEnabled),
                allow_modify_password=bool(buf[i].m_szAllowModifyPassword),
                close_permission_control=bool(buf[i].m_szClosePermissionControl),
            )
            for i in range(min(count.value, max_users))
        ]

    def modify_integrate_user(self, username: str, password: str) -> None:
        """Change the integration user's credentials (``NET_SDK_ModifyIntegrateUser``, 1.3.2+)."""
        fn = self._require("NET_SDK_ModifyIntegrateUser")
        self._check(
            fn(self._handle, username.encode("utf-8"), password.encode("utf-8")),
            "ModifyIntegrateUser",
        )

    # ── NVR channel enumeration ─────────────────────────────────

    def online_channels(self) -> list[str]:
        """List online channel GUIDs on an NVR (``NET_SDK_QueryOnlineChlList``, 1.3.2+)."""
        fn = self._require("NET_SDK_QueryOnlineChlList")
        chl_list = NVRChlListStruct()
        out_size = ct.c_int(0)
        self._check(fn(self._handle, ct.byref(chl_list), ct.byref(out_size)), "QueryOnlineChlList")
        count = max(0, min(out_size.value, 256))
        # chlList is a nested c_char array; elements expose bytes via `.value`.
        out = []
        for i in range(count):
            guid = chl_list.chlList[i].value
            if guid:
                out.append(guid.decode("utf-8", errors="replace"))
        return out

    def nvr_channel_info(self, channel_guid: str) -> NvrChannelInfo:
        """Read IPC details for one NVR channel (``NET_SDK_GetNvrChlInfo``, 1.3.2+)."""
        fn = self._require("NET_SDK_GetNvrChlInfo")
        info = NVRChlInfoStruct()
        self._check(
            fn(self._handle, channel_guid.encode("utf-8"), ct.byref(info)),
            "GetNvrChlInfo",
        )
        return NvrChannelInfo(
            software_version=info.softwareVersion,
            detailed_software_version=info.detailedSoftwareVersion.decode("utf-8", errors="replace"),
            product_type=info.productType,
            device_type=info.deviceType,
            supports_soft_encrypt=bool(info.supportSoftEncrypt),
            mac=info.mac.decode("utf-8", errors="replace"),
        )

    # ── Recording status / devices ──────────────────────────────

    def record_status(self, max_channels: int = 256) -> list[RecordStatus]:
        """Per-channel recording status (``NET_SDK_GetRecordStatus``, 1.3.2+)."""
        fn = self._require("NET_SDK_GetRecordStatus")
        buf = (NET_SDK_RECORD_STATUS * max_channels)()
        count = fn(self._handle, buf, max_channels)
        if count < 0:
            raise NetSdkError("GetRecordStatus", self._sdk_last_error())
        return [
            RecordStatus(channel=buf[i].dwChannel, record_type=buf[i].dwRecordType)
            for i in range(min(count, max_channels))
        ]

    def record_status_ex(self, max_channels: int = 256) -> list[RecordStatusEx]:
        """Extended per-channel recording status (``NET_SDK_GetRecordStatusEx``, 1.3.2+)."""
        fn = self._require("NET_SDK_GetRecordStatusEx")
        buf = (NET_SDK_RECORD_STATUS_EX * max_channels)()
        count = fn(self._handle, buf, max_channels)
        if count < 0:
            raise NetSdkError("GetRecordStatusEx", self._sdk_last_error())
        return [
            RecordStatusEx(
                channel=buf[i].dwChannel,
                device_name=buf[i].deviceName.decode("utf-8", errors="replace"),
                record_type=buf[i].dwRecordType,
                record_status=buf[i].dwRecordStatus,
                stream_type=buf[i].dwStreamType,
                resolution=buf[i].resolution_str,
                frame_rate=buf[i].dwFrameRate,
                bitrate_cap_kbps=buf[i].dwQuality,
                bitrate_type=buf[i].dwBitType,
                quality_level=buf[i].dwLevel,
            )
            for i in range(min(count, max_channels))
        ]

    def record_devices(self, max_devices: int = 256) -> list[RecordDevice]:
        """List recording channel nodes (``NET_SDK_GetRecordDevice``, 1.3.2+)."""
        fn = self._require("NET_SDK_GetRecordDevice")
        buf = (NET_SDK_RECORD_DEVICE * max_devices)()
        count = fn(self._handle, buf, max_devices)
        return [
            RecordDevice(
                channel=buf[i].nodeChlID.channel,
                node_id=buf[i].nodeChlID.as_string,
                name=buf[i].deviceName.decode("utf-8", errors="replace"),
            )
            for i in range(min(count, max_devices))
        ]

    def playback_sync_handle(self, channel: int) -> int:
        """Get a synchronized-playback handle for a channel (``NET_SDK_GetPlayBackSyncHandle``, 1.3.2+)."""
        fn = self._require("NET_SDK_GetPlayBackSyncHandle")
        handle = fn(self._handle, channel)
        if handle <= 0:
            raise NetSdkError("GetPlayBackSyncHandle", self._sdk_last_error())
        return handle

    # ── Thermal snapshot ────────────────────────────────────────

    def capture_thermal_jpeg(
        self,
        channel: int,
        *,
        resolution: int = 0,
        buf_size: int = 2 * 1024 * 1024,
    ) -> bytes:
        """Capture a thermal JPEG snapshot (``NET_SDK_CaptureThermalJpeg``, 1.3.2+)."""
        fn = self._require("NET_SDK_CaptureThermalJpeg")
        buf = ct.create_string_buffer(buf_size)
        returned = ct.c_uint(0)
        self._check(
            fn(self._handle, channel, resolution, buf, buf_size, ct.byref(returned)),
            "CaptureThermalJpeg",
        )
        return buf.raw[: returned.value]

    # ── Cloud upgrade ───────────────────────────────────────────

    def cloud_upgrade(self, version_guid: str) -> None:
        """Start a cloud firmware upgrade of the device (``NET_SDK_CloudUpgrade``, 1.3.2+)."""
        fn = self._require("NET_SDK_CloudUpgrade")
        self._check(fn(self._handle, version_guid.encode("utf-8")), "CloudUpgrade")

    def cloud_upgrade_node(self, channel: int, version_guid: str) -> None:
        """Start a cloud upgrade of one NVR channel (``NET_SDK_CloudUpgradeNode``, 1.3.2+)."""
        fn = self._require("NET_SDK_CloudUpgradeNode")
        self._check(
            fn(self._handle, channel, version_guid.encode("utf-8")),
            "CloudUpgradeNode",
        )

    def cloud_upgrade_info(self, max_items: int = 256) -> list[CloudUpgradeStatus]:
        """Read cloud-upgrade state for the NVR and its channels (``NET_SDK_GetCloudUpgradeInfo``, 1.3.2+)."""
        fn = self._require("NET_SDK_GetCloudUpgradeInfo")
        buf = (CLOUD_UPGRADE_INFO * max_items)()
        count = ct.c_long(0)
        self._check(fn(self._handle, buf, max_items, ct.byref(count)), "GetCloudUpgradeInfo")
        return [
            CloudUpgradeStatus(
                channel=buf[i].chlid,
                state=buf[i].state.decode("utf-8", errors="replace"),
                progress_pct=buf[i].progress / 100.0,
                version=buf[i].version.decode("utf-8", errors="replace"),
                new_version_guid=buf[i].newVersionGUID.decode("utf-8", errors="replace"),
            )
            for i in range(min(count.value, max_items))
        ]

    # ── Smart-event configuration (opaque device payloads) ──────
    # These marshal raw device config buffers keyed by ``command`` (an
    # IVM_rule_config_type value). The payload schema is the device's, not
    # ours — callers own encoding/decoding; the wrapper only moves bytes.

    def get_smart_event_config(
        self,
        command: int,
        channel: int,
        *,
        buf_size: int = 64 * 1024,
    ) -> bytes:
        """Read a smart-event config blob (``NET_SDK_GetSmartEventConfig``, 1.3.2+)."""
        fn = self._require("NET_SDK_GetSmartEventConfig")
        buf = ct.create_string_buffer(buf_size)
        returned = ct.c_uint(0)
        self._check(
            fn(self._handle, command, channel, buf, buf_size, ct.byref(returned)),
            "GetSmartEventConfig",
        )
        return buf.raw[: returned.value]

    def edit_smart_event_config(self, command: int, channel: int, data: bytes) -> None:
        """Write a smart-event config blob (``NET_SDK_EditSmartEventConfig``, 1.3.2+)."""
        fn = self._require("NET_SDK_EditSmartEventConfig")
        buf = ct.create_string_buffer(bytes(data), len(data))
        self._check(
            fn(self._handle, command, channel, buf, len(data)),
            "EditSmartEventConfig",
        )

    def edit_smart_event_point(
        self,
        command: int,
        channel: int,
        points: list[tuple[int, int]],
        direction: TripwireDirection = TripwireDirection.NONE,
    ) -> None:
        """Set a smart-event rule's point geometry (``NET_SDK_EditSmartEventPoint``, 1.3.2+).

        ``points`` are ``(x, y)`` pairs in device coordinates; ``direction``
        applies to tripwire rules.
        """
        fn = self._require("NET_SDK_EditSmartEventPoint")
        arr = (NET_DVR_IVE_POINT_T * len(points))(*(NET_DVR_IVE_POINT_T(X=x, Y=y) for x, y in points))
        self._check(
            fn(self._handle, command, channel, arr, len(points), int(direction)),
            "EditSmartEventPoint",
        )

    # ── On-screen AI rule overlay (needs a live/playback handle) ─

    def show_rule(self, play_handle: int, channel: int, show: bool = True) -> None:
        """Toggle the AI rule overlay on a live/playback window (``NET_SDK_ShowRule``, 1.3.2+)."""
        fn = self._require("NET_SDK_ShowRule")
        self._check(fn(play_handle, self._handle, channel, show), "ShowRule")

    def show_rule_boxes(self, play_handle: int, rules: list[list[int]]) -> None:
        """Draw up to four rule boxes on a play window (``NET_SDK_ShowRuleBoxList``, 1.3.2+).

        Each rule is a flat list of up to 12 ints (x1,y1..x6,y6); short lists are
        zero-padded, extra rules past four are ignored (SDK ``MAX_RULE_NUMBER``).
        """
        fn = self._require("NET_SDK_ShowRuleBoxList")
        rule_list = RULE_POINT_LIST()
        for i, coords in enumerate(rules[:4]):
            padded = (list(coords) + [0] * 12)[:12]
            rule_list.rule_point[i] = RULE_POINT(*padded)
        self._check(fn(play_handle, rule_list), "ShowRuleBoxList")

    # ── Two-way audio ───────────────────────────────────────────

    def start_voice_talk(
        self,
        on_audio: Callable[[bytes, int], None],
        *,
        channel: int = -1,
        need_raw: bool = False,
    ) -> int:
        """Start two-way audio and stream device audio to ``on_audio`` (``NET_SDK_StartVoiceComTalk``, 1.3.2+).

        ``on_audio`` is invoked as ``on_audio(pcm_or_encoded_bytes, audio_flag)``.
        Returns the voice handle. The callback reference is retained on the
        session for the life of the handle so it is not garbage-collected while
        the SDK still holds it.
        """
        fn = self._require("NET_SDK_StartVoiceComTalk")

        def _trampoline(_handle, buf_ptr, buf_size, audio_flag, _user):
            data = ct.string_at(buf_ptr, buf_size) if buf_ptr and buf_size else b""
            on_audio(data, audio_flag)

        cb = TALK_DATA_CALLBACK(_trampoline)
        handle = fn(self._handle, need_raw, cb, None, channel)
        if handle <= 0:
            raise NetSdkError("StartVoiceComTalk", self._sdk_last_error())
        # Keep both the ctypes thunk and the Python target alive.
        self._voice_callbacks[handle] = (cb, on_audio)
        return handle

    def _sdk_last_error(self) -> SdkError | int:
        code = sdk._lib.NET_SDK_GetLastError()  # type: ignore[union-attr]
        return SdkError(code) if code in SdkError._value2member_map_ else code


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
        connect_retry_count: int = 3,
        reconnect_interval: int = 0,
        recv_timeout: int | None = None,
    ) -> None:
        if connect_timeout <= 300:
            raise ValueError("connect_timeout must be greater than 300ms")
        if not 1 <= connect_retry_count <= 10:
            raise ValueError("connect_retry_count must be between 1 and 10")
        if recv_timeout is not None:
            warnings.warn(
                "recv_timeout never controlled a receive timeout; "
                "NET_SDK_SetConnectTime's second argument is a retry count. "
                "Use connect_retry_count instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        with _PROCESS_SDK_LOCK:
            if _PROCESS_SUBSCRIBE_OWNER is not None:
                raise NetSdkCapabilityError(
                    "Cannot initialize another NetSdkClient while a process-global subscription callback is active."
                )
            self._sdk_path = sdk_path
            self._connect_timeout = connect_timeout
            self._connect_retry_count = connect_retry_count
            self._reconnect_interval = reconnect_interval
            # Retain the global subscription thunk (see subscribe_v2).
            self._subscribe_callback: tuple[object, object] | None = None
            self._subscribe_callback_condition = threading.Condition()
            self._subscribe_callback_inflight = 0
            self._subscribe_callback_closing = False
            self._active_plate_stream: PlateEventStream | None = None
            self._lib = load_sdk(sdk_path=sdk_path)
            sdk.bind(self._lib)
            if not self._lib.NET_SDK_Init():
                raise NetSdkError("NET_SDK_Init failed")
            self._lib.NET_SDK_SetConnectTime(connect_timeout, connect_retry_count)
            if reconnect_interval > 0:
                self._lib.NET_SDK_SetReconnect(reconnect_interval, True)
            else:
                self._lib.NET_SDK_SetReconnect(0, False)
            _LIVE_CLIENTS.add(self)
            logger.debug("NetSDK initialized (v%s)", self.sdk_version())

    def __enter__(self) -> NetSdkClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Release SDK resources."""
        if self._lib is not None:
            if self._active_plate_stream is not None and not self._active_plate_stream.closed:
                try:
                    self._active_plate_stream.close()
                except Exception:
                    logger.exception("Failed to close plate-event stream during NetSDK cleanup")
            if self._subscribe_callback is not None:
                try:
                    self.subscribe_v2(None)
                except Exception:
                    logger.exception("Failed to clear smart-event callback during NetSDK cleanup")
            with _PROCESS_SDK_LOCK:
                global _PROCESS_SUBSCRIBE_OWNER
                self._lib.NET_SDK_Cleanup()
                self._lib = None
                with self._subscribe_callback_condition:
                    self._subscribe_callback_condition.wait_for(
                        lambda: self._subscribe_callback_inflight == 0,
                        timeout=_CALLBACK_DRAIN_TIMEOUT_SECONDS,
                    )
                    if self._subscribe_callback_inflight == 0:
                        self._subscribe_callback = None
                    else:
                        logger.error(
                            "Retaining smart-event callback thunk after cleanup because %d callback(s) remain in flight",
                            self._subscribe_callback_inflight,
                        )
                    self._subscribe_callback_closing = False
                if _PROCESS_SUBSCRIBE_OWNER is self:
                    _PROCESS_SUBSCRIBE_OWNER = None
                _LIVE_CLIENTS.discard(self)
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
        if timeout_ms <= 300:
            raise ValueError("temporary connect timeout must be greater than 300ms")

        self._lib.NET_SDK_SetConnectTime(
            timeout_ms,
            self._connect_retry_count,
        )
        try:
            yield
        finally:
            self._lib.NET_SDK_SetConnectTime(
                self._connect_timeout,
                self._connect_retry_count,
            )

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

    # ── Event subscription (process-global) ─────────────────────

    def subscribe_v2(
        self,
        on_event: Callable[[int, int, int, bytes], None] | None,
        *,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
        on_rejected: Callable[[str], None] | None = None,
    ) -> None:
        """Register the process-wide push callback (``NET_SDK_SetSubscribCallBack_V2``, 1.3.2+).

        ``on_event`` is invoked as ``on_event(user_id, channel_id, command, payload)``
        for each subscription push. Pass ``None`` to clear the callback. The thunk
        is retained on the owning client. The SDK exposes one process-global
        callback slot, so replacement is rejected until the owner clears it.
        """
        global _PROCESS_SUBSCRIBE_OWNER
        with _PROCESS_SDK_LOCK:
            fn = getattr(self._lib, "NET_SDK_SetSubscribCallBack_V2", None)
            if fn is None:
                raise NetSdkCapabilityError(
                    "Loaded TVT NetSDK does not export NET_SDK_SetSubscribCallBack_V2 (needs the 1.3.2+ device SDK)."
                )
            if max_payload_bytes < 1 or max_payload_bytes > 64 * 1024 * 1024:
                raise ValueError("max_payload_bytes must be between 1 byte and 64 MiB")
            if on_event is None:
                if _PROCESS_SUBSCRIBE_OWNER not in (None, self):
                    raise NetSdkCapabilityError("Only the process-global callback owner may clear the callback.")
                with self._subscribe_callback_condition:
                    self._subscribe_callback_closing = True
                if not fn(SUBSCRIBE_CALLBACK_V2(0), None):
                    with self._subscribe_callback_condition:
                        self._subscribe_callback_closing = False
                    raise NetSdkError("SetSubscribCallBack_V2", self._sdk_error(self._last_error()))
                with self._subscribe_callback_condition:
                    drained = self._subscribe_callback_condition.wait_for(
                        lambda: self._subscribe_callback_inflight == 0,
                        timeout=_CALLBACK_DRAIN_TIMEOUT_SECONDS,
                    )
                    if not drained:
                        self._subscribe_callback_closing = False
                        raise NetSdkError(
                            "SetSubscribCallBack_V2 cleared, but in-flight callbacks did not quiesce within 5 seconds"
                        )
                # Clear only after the SDK confirms deregistration. Dropping the
                # final ctypes reference first could leave a live native callback
                # pointing at reclaimed Python memory.
                self._subscribe_callback = None
                self._subscribe_callback_closing = False
                if _PROCESS_SUBSCRIBE_OWNER is self:
                    _PROCESS_SUBSCRIBE_OWNER = None
                return
            if _PROCESS_SUBSCRIBE_OWNER is not None or self._subscribe_callback is not None:
                raise NetSdkCapabilityError(
                    "A process-global smart-event callback is already active; clear it before replacement."
                )
            if len(_LIVE_CLIENTS) != 1 or self not in _LIVE_CLIENTS:
                raise NetSdkCapabilityError(
                    "Smart-event callbacks require exactly one live NetSdkClient in the process."
                )

            def _trampoline(user_id, channel_id, command, buf_ptr, buf_len, _user):
                with self._subscribe_callback_condition:
                    self._subscribe_callback_inflight += 1
                try:
                    with self._subscribe_callback_condition:
                        if self._subscribe_callback_closing:
                            return
                    if buf_len > max_payload_bytes:
                        reason = f"subscription payload length {buf_len} exceeds limit {max_payload_bytes}"
                        if on_rejected is not None:
                            on_rejected(reason)
                        else:
                            logger.warning(reason)
                        return
                    data = ct.string_at(buf_ptr, buf_len) if buf_ptr and buf_len else b""
                    on_event(user_id, channel_id, command, data)
                except Exception:
                    # ctypes callbacks must never leak exceptions across the C ABI.
                    logger.exception("TVT subscription callback handler raised")
                finally:
                    with self._subscribe_callback_condition:
                        self._subscribe_callback_inflight -= 1
                        self._subscribe_callback_condition.notify_all()

            cb = SUBSCRIBE_CALLBACK_V2(_trampoline)
            if not fn(cb, None):
                raise NetSdkError("SetSubscribCallBack_V2", self._sdk_error(self._last_error()))
            self._subscribe_callback = (cb, on_event)
            with self._subscribe_callback_condition:
                self._subscribe_callback_closing = False
            _PROCESS_SUBSCRIBE_OWNER = self

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
            sdk_error = self._sdk_error(code)
            credential_errors = {
                SdkError.LOGIN_REFUSED,
                SdkError.NOENOUGH_AUTH,
                SdkError.PASSWORD_ERROR,
                SdkError.PASSWORD_FORMAT_ERROR,
                SdkError.USERNOTEXIST,
                SdkError.USER_ERROR_NO_USER,
                SdkError.USER_ERROR_USER_OR_PASSWORD_IS_NULL,
            }
            error_type = NetSdkCredentialRejectedError if sdk_error in credential_errors else NetSdkError
            raise error_type(
                f"Login to {host}:{port} as {username}",
                sdk_error,
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

    def update_node_encode_verified(
        self,
        *,
        method: ConnectionMethod = "direct",
        username: str,
        password: str,
        channel: int,
        profile: Literal["continuous", "event"],
        expected: dict[str, object],
        changes: dict[str, object],
        expected_codec: str,
        codec: str | None = None,
        host: str | None = None,
        port: int = 9008,
        identifier: str | None = None,
        timeout: float | None = None,
        nat_server: str | None = None,
        nat_port: int | None = None,
        connect_type: ConnectType | int | str = ConnectType.NAT20,
        fallback_to_direct: bool = True,
    ) -> NodeEncodeUpdateResult:
        """Guard, apply, and independently verify one encode-profile patch.

        TVT may cache ``queryNodeEncodeInfo`` for the lifetime of a logged-in
        session. Verification therefore always uses a newly authenticated
        session. A baseline mismatch is returned as ``conflict`` without a
        write; a post-write mismatch is explicitly ambiguous so callers do not
        automatically retry a mutation whose device outcome is uncertain.
        """
        writable_fields = {
            "resolution",
            "fps",
            "bitrate_type",
            "quality",
            "max_bitrate",
            "audio",
        }
        if channel < 1:
            raise ValueError("channel must be a 1-based positive integer")
        if profile not in {"continuous", "event"}:
            raise ValueError("profile must be 'continuous' or 'event'")
        if set(expected) != writable_fields:
            raise ValueError("expected must contain the complete writable encode profile")
        if set(changes) - writable_fields:
            raise ValueError("changes must contain only writable encode fields")
        if not expected_codec:
            raise ValueError("expected_codec must be non-empty")
        if codec is not None and not codec:
            raise ValueError("codec must be non-empty when supplied")
        if not changes and codec is None:
            raise ValueError("at least one stream or codec change is required")

        connection = {
            "method": method,
            "username": username,
            "password": password,
            "host": host,
            "port": port,
            "identifier": identifier,
            "timeout": timeout,
            "nat_server": nat_server,
            "nat_port": nat_port,
            "connect_type": connect_type,
            "fallback_to_direct": fallback_to_direct,
        }

        def _channel(session: DeviceSession) -> NodeEncodeInfo:
            item = {row.channel: row for row in session.node_encode_info()}.get(channel)
            if item is None:
                raise NetSdkError(f"queryNodeEncodeInfo: channel {channel} not found", -1)
            return item

        def _profile_state(item: NodeEncodeInfo) -> dict[str, object]:
            stream = getattr(item, profile)
            if stream is None:
                raise NetSdkError(f"queryNodeEncodeInfo: channel {channel} {profile} profile missing", -1)
            return {
                "resolution": stream.resolution,
                "fps": stream.fps,
                "bitrate_type": stream.bitrate_type,
                "quality": stream.quality,
                "max_bitrate": stream.max_bitrate,
                "audio": stream.audio,
            }

        with self.connect(**connection) as session:
            current = _channel(session)
            connection_method = session.connection_method
            if _profile_state(current) != expected or current.codec != expected_codec:
                return NodeEncodeUpdateResult(
                    status="conflict",
                    channel=current,
                    connection_method=session.connection_method,
                )
            try:
                session.set_node_encode(
                    channel,
                    continuous=changes if profile == "continuous" else None,
                    event=changes if profile == "event" else None,
                    codec=codec,
                    verify=False,
                )
            except Exception:
                return NodeEncodeUpdateResult(
                    status="write_unconfirmed",
                    channel=current,
                    connection_method=connection_method,
                )

        try:
            with self.connect(**connection) as verification_session:
                verified = _channel(verification_session)
                status = (
                    "updated"
                    if (
                        all(_profile_state(verified)[key] == value for key, value in changes.items())
                        and (codec is None or verified.codec == codec)
                    )
                    else "verification_mismatch"
                )
                return NodeEncodeUpdateResult(
                    status=status,
                    channel=verified,
                    connection_method=verification_session.connection_method,
                )
        except Exception:
            return NodeEncodeUpdateResult(
                status="verification_unavailable",
                channel=current,
                connection_method=connection_method,
            )

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
