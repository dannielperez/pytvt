"""High-level Pythonic wrapper around libdvrnetsdk.so.

Usage::

    from pytvt.netsdk import NetSdkClient

    with NetSdkClient() as sdk:
        devices = sdk.discover()
        with sdk.login("10.0.0.1", "admin", "password") as session:
            info = session.device_info()
            jpg  = session.capture_jpeg(channel=0)
            session.ptz(PtzCommand.LEFT, speed=PtzSpeed.SPEED_4, channel=0)

Requires Linux x86_64 or aarch64 with a vendor-supplied libdvrnetsdk.so.
"""

from __future__ import annotations

import ctypes as ct
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from . import bindings as sdk
from .constants import (
    AlarmType,
    DiskProperty,
    DiskStatus,
    PtzCommand,
    PtzSpeed,
    RecordType,
    SdkError,
    StreamType,
)
from .loader import load_sdk
from .types import (
    DD_TIME,
    NET_SDK_ALRAM_OUT_STATUS,
    NET_SDK_CH_DEVICE_STATUS,
    NET_SDK_DEV_SUPPORT,
    NET_SDK_DEVICE_DISCOVERY_INFO,
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


# ── Session (logged-in handle) ──────────────────────────────────────

class DeviceSession:
    """Authenticated session to a single device.

    Obtain via :meth:`NetSdkClient.login`.  Use as a context manager
    to ensure logout on exit.
    """

    def __init__(self, handle: int, client: NetSdkClient) -> None:
        self._handle = handle
        self._client = client

    def __enter__(self) -> DeviceSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.logout()

    @property
    def handle(self) -> int:
        return self._handle

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
                self._handle, buf, max_channels, ct.byref(count),
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
                self._handle, buf, max_channels, ct.byref(count),
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
                self._handle, channel, ct.byref(s),
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
                self._handle, channel, stream, buf,
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
                self._handle, channel, ct.byref(para),
                buf, buf_size, ct.byref(returned),
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
                self._handle, channel, command, speed,
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
                self._handle, channel, command, preset_index,
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
                self._handle, channel, command, cruise_index,
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
                self._handle, buf, max_outputs, ct.byref(count),
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
            self._handle, channel, record_type, ct.byref(t_start), ct.byref(t_stop),
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
                self._handle, buf, max_items, ct.byref(count),
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
            self._handle, log_type, ct.byref(t_start), ct.byref(t_stop),
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
                self._handle, file_path.encode("utf-8"),
            ),
            "GetConfigFile",
        )

    def import_config(self, file_path: str) -> None:
        """Import device configuration from a file."""
        self._check(
            sdk._lib.NET_SDK_SetConfigFile(  # type: ignore[union-attr]
                self._handle, file_path.encode("utf-8"),
            ),
            "SetConfigFile",
        )

    # ── Firmware upgrade ────────────────────────────────────────

    def upgrade(self, firmware_path: str) -> int:
        """Start firmware upgrade and return upgrade handle.

        Check progress with :meth:`NetSdkClient.upgrade_progress`.
        """
        handle = sdk._lib.NET_SDK_Upgrade(  # type: ignore[union-attr]
            self._handle, firmware_path.encode("utf-8"),
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

    # ── Version info ────────────────────────────────────────────

    def sdk_version(self) -> str:
        """Return SDK version as 'major.minor.patch' string."""
        v = self._lib.NET_SDK_GetSDKVersion()
        return f"{(v >> 24) & 0xFF}.{(v >> 16) & 0xFF}.{v & 0xFFFF}"

    def sdk_build_version(self) -> int:
        """Return SDK build number."""
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
            ip.encode("utf-8"), port, password.encode("utf-8"),
        )
        if not ok:
            raise NetSdkError("ActiveDevice", self._last_error())

    def activate_by_mac(self, mac: str, password: str) -> None:
        """Activate a device by MAC address."""
        ok = self._lib.NET_SDK_ActiveDeviceByMac(
            mac.encode("utf-8"), password.encode("utf-8"),
        )
        if not ok:
            raise NetSdkError("ActiveDeviceByMac", self._last_error())

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
        handle = self._lib.NET_SDK_Login(
            host.encode("utf-8"),
            port,
            username.encode("utf-8"),
            password.encode("utf-8"),
            ct.byref(info),
        )
        if handle < 0:
            code = self._last_error()
            raise NetSdkError(
                f"Login to {host}:{port} as {username}",
                SdkError(code) if code in SdkError._value2member_map_ else code,
            )
        logger.info(
            "Logged in to %s:%d — %s (%s) SN=%s",
            host, port,
            info.deviceName.decode("utf-8", errors="replace"),
            info.firmwareVersion.decode("utf-8", errors="replace"),
            info.szSN.decode("utf-8", errors="replace"),
        )
        return DeviceSession(handle, self)

    # ── Upgrade progress ────────────────────────────────────────

    @staticmethod
    def upgrade_progress(upgrade_handle: int) -> int:
        """Check firmware upgrade progress (0-100, or negative on error)."""
        progress = ct.c_int(0)
        ret = sdk._lib.NET_SDK_GetUpgradeProgress(  # type: ignore[union-attr]
            upgrade_handle, ct.byref(progress),
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
