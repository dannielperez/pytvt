"""Response models for the TVT HTTP Web API.

Plain dataclasses — no Django, no Pydantic.  Each model corresponds to
a parsed XML response from a specific API endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DeviceInfo:
    """From ``GetDeviceInfo`` (Section 2.1.2)."""

    device_name: str = ""
    device_id: str = ""
    device_model: str = ""
    serial_number: str = ""
    mac_address: str = ""
    firmware_version: str = ""
    firmware_release_date: str = ""
    boot_version: str = ""
    hardware_version: str = ""
    device_type: str = ""  # IPC, NVR, DVR, etc.
    telecontrol_id: int = 0
    video_input_num: int = 0
    video_output_num: int = 0
    audio_input_num: int = 0
    audio_output_num: int = 0
    alarm_input_num: int = 0
    alarm_output_num: int = 0


@dataclass
class ChannelInfo:
    """A single channel from ``GetChannelInfo`` (Section 2.1.4)."""

    channel_id: int = 0
    channel_name: str = ""
    ip_address: str = ""
    channel_type: str = ""  # digital, analog
    online: bool = False


@dataclass
class DiskInfo:
    """A single disk from ``GetDiskInfo`` (Section 2.1.3)."""

    disk_id: int = 0
    disk_name: str = ""
    disk_type: str = ""  # SATA, USB, eSATA, etc.
    status: str = ""  # Normal, Abnormal, NotFormatted, etc.
    capacity_mb: int = 0
    free_mb: int = 0
    property: str = ""  # ReadWrite, ReadOnly, Redundancy


@dataclass
class DateTimeInfo:
    """From ``GetDateAndTime`` (Section 2.2.1)."""

    mode: str = ""  # NTP or Manual
    local_time: str = ""  # ISO-like datetime string
    time_zone: str = ""
    ntp_server: str = ""
    ntp_port: int = 123
    ntp_interval: int = 1440  # minutes


@dataclass
class ImageConfig:
    """From ``GetImageConfig`` — subset of most-used fields (Section 3.2.1)."""

    channel_id: int = 0
    brightness: int = 0
    contrast: int = 0
    saturation: int = 0
    sharpness: int = 0
    exposure_mode: str = ""
    white_balance_mode: str = ""
    ir_cut_mode: str = ""
    wdr_enabled: bool = False
    wdr_level: int = 0


@dataclass
class VideoStreamConfig:
    """From ``GetVideoStreamConfig`` — primary fields (Section 3.3.3)."""

    channel_id: int = 0
    stream_type: str = ""  # Main, Sub, Third
    codec: str = ""  # H.264, H.265
    resolution_width: int = 0
    resolution_height: int = 0
    bitrate_type: str = ""  # CBR, VBR
    bitrate: int = 0
    max_bitrate: int = 0
    frame_rate: int = 0
    gop: int = 0
    quality: int = 0


@dataclass
class AudioStreamConfig:
    """From ``GetAudioStreamConfig`` (Section 3.3.1)."""

    channel_id: int = 0
    enabled: bool = False
    codec: str = ""  # G711A, G711U, AAC, G726
    sample_rate: int = 0
    bitrate: int = 0


@dataclass
class ImageOsdConfig:
    """From ``GetImageOsdConfig`` — simplified (Section 3.4.1)."""

    channel_id: int = 0
    time_osd_enabled: bool = False
    channel_name_enabled: bool = False
    channel_name: str = ""


@dataclass
class RecordStatus:
    """From ``GetRecordStatusInfo`` (Section 6.2.1)."""

    channel_id: int = 0
    is_recording: bool = False
    record_type: str = ""  # Manual, Schedule, Alarm, etc.


@dataclass
class RecordDateResult:
    """From ``SearchRecordDate`` (Section 6.1.2)."""

    channel_id: int = 0
    dates: list[str] = field(default_factory=list)  # YYYY-MM-DD strings


@dataclass
class RecordSegment:
    """A single recording segment from ``SearchByTime`` (Section 6.1.3)."""

    channel_id: int = 0
    start_time: str = ""
    end_time: str = ""
    record_type: str = ""


@dataclass
class SnapshotResult:
    """Result of a snapshot capture attempt."""

    success: bool = False
    image_data: bytes = b""
    content_type: str = ""
    method: str = ""  # "webapi", "webapi_by_time", "rtsp"
    error: str = ""
