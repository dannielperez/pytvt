"""Data models and exceptions for the pytvt package.

Two model groups live here:

1. **Scanner models** — typed structures for the bulk-scan pipeline
   (``ScannerConfig``, ``DeviceEntry``, ``CameraInfo``, ``ScanResult``).
2. **NVR API models** — structures returned by the NVR web CGI client
   (``Channel``, ``User``, ``RtspServerConfig``, etc.).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

# ── Exceptions ───────────────────────────────────────────────────────


class NvrApiError(Exception):
    """Raised when the NVR web API returns an error."""

    def __init__(self, message: str, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class PlatformAccessDisabledError(NvrApiError):
    """Raised when editing Platform Access config while the feature is disabled.

    The NVR rejects ``editPlatformCfg`` with errorCode=536870943 when
    Platform Access (Integration → Platform Access) has never been enabled.
    Enable it in the NVR web UI or via ``set_platform_access(enabled=True, ...)``
    with a valid ``report_id`` first.
    """


class NvrApiResponseShapeError(NvrApiError):
    """Raised when a response has a success status but an UNRECOGNIZED shape.

    Firmware drift can return ``<status>success</status>`` with a payload whose
    structure the parser does not recognize (e.g. a missing ``<content>``
    container, or a declared ``total`` that does not match the parsed item
    count). Returning an empty list in that case is a silent false-empty.
    Callers should treat this as a *fetch failure*, NOT a genuine
    empty result, so it must be distinguishable from ``NvrApiError`` (a reported
    API error) — hence its own subclass.
    """


# ── Scanner models ───────────────────────────────────────────────────


@dataclass
class ScannerConfig:
    """Runtime configuration for the bulk scanner.

    Built by :func:`pytvt.config.load_config` from env vars, a JSON file,
    and CLI overrides (in that precedence order).
    """

    username: str = "admin"
    password: str = ""
    port: int = 6036
    timeout: int = 10
    max_channels: int = 64
    concurrency: int = 4
    api_url: str = "http://localhost:3000"
    sdk_path: str | None = None
    scan_script: str | None = None


@dataclass
class DeviceEntry:
    """An NVR from the inventory JSON fed to the scanner.

    At minimum ``ip`` or ``identifier`` is required; every other field has a
    sensible default.
    """

    ip: str = ""
    site: str = ""
    hostname: str = ""
    mac: str = ""
    port: int = 0  # 0 means "use ScannerConfig.port"
    manufacturer: str = ""
    identifier: str = ""
    connection_method: str = ""
    nat_server: str = ""
    nat_port: int = 0
    connection_preference: str = ""  # "nat", "direct", or "auto"
    last_connection_method: str = ""
    nat_capable: bool | None = None  # None = unknown

    @classmethod
    def from_dict(cls, d: dict) -> DeviceEntry:
        """Construct from a raw JSON dict, ignoring unknown keys."""
        identifier = str(d.get("identifier") or d.get("id") or d.get("uid") or d.get("serial") or d.get("sn") or "")
        return cls(
            ip=d.get("ip", ""),
            site=d.get("site", ""),
            hostname=d.get("hostname", ""),
            mac=d.get("mac", ""),
            port=int(d.get("port", 0)),
            manufacturer=d.get("manufacturer", ""),
            identifier=identifier,
            connection_method=str(d.get("connection_method") or d.get("method") or ""),
            nat_server=str(d.get("nat_server") or ""),
            nat_port=int(d.get("nat_port", 0) or 0),
            connection_preference=str(d.get("connection_preference") or d.get("prefer") or ""),
            last_connection_method=str(d.get("last_connection_method") or ""),
            nat_capable=d.get("nat_capable"),
        )

    def effective_port(self, config: ScannerConfig) -> int:
        """Return the port to connect to, falling back to config default."""
        return self.port or config.port

    @property
    def effective_connection_method(self) -> str:
        """Return the preferred connection method for this entry."""
        method = self.connection_method.strip().lower()
        if method in {"direct", "nat"}:
            return method
        pref = self.connection_preference.strip().lower()
        if pref in {"direct", "nat"}:
            return pref
        return "nat" if self.identifier else "direct"

    @property
    def connect_target(self) -> str:
        """Return the best human-readable target for logs and UIs."""
        return self.ip or self.identifier


@dataclass
class CameraInfo:
    """A single camera/IPC channel within a :class:`ScanResult`."""

    channel: int | str = ""
    name: str = ""
    address: str = ""
    port: int | str = ""
    status: str = ""
    protocol: str = ""
    model: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> CameraInfo:
        """Construct from a dict returned by the protocol scanner or SDK."""
        return cls(
            channel=d.get("channel", ""),
            name=d.get("name", ""),
            address=d.get("address", ""),
            port=d.get("port", ""),
            status=d.get("status", ""),
            protocol=d.get("protocol", ""),
            model=d.get("model", ""),
        )

    @property
    def has_address(self) -> bool:
        """True when the camera has a non-empty IP address."""
        return bool(str(self.address).strip())


@dataclass
class ScanResult:
    """Outcome of scanning a single NVR.

    Every backend (protocol, SDK HTTP, SDK local) returns one of these.
    Use :meth:`for_device` to create a pre-populated failure result, then
    fill in the success fields.
    """

    site: str = ""
    hostname: str = ""
    nvr_ip: str = ""
    nvr_mac: str = ""
    nvr_port: int = 0
    success: bool = False
    device_name: str = ""
    device_model: str = ""
    serial_number: str = ""
    firmware: str = ""
    total_channels: int = 0
    cameras: list[CameraInfo] = field(default_factory=list)
    error: str | None = None
    backend: str = ""
    device_info: dict = field(default_factory=dict)

    # ── Factories ────────────────────────────────────────────────

    @classmethod
    def for_device(
        cls,
        device: DeviceEntry,
        config: ScannerConfig,
        *,
        backend: str = "",
    ) -> ScanResult:
        """Create a base (failure) result pre-populated with device metadata."""
        return cls(
            site=device.site,
            hostname=device.hostname,
            nvr_ip=device.ip,
            nvr_mac=device.mac,
            nvr_port=device.effective_port(config),
            backend=backend,
        )

    # ── Helpers ──────────────────────────────────────────────────

    @property
    def camera_count(self) -> int:
        """Number of cameras that have an IP address."""
        return sum(1 for c in self.cameras if c.has_address)

    def to_dict(self) -> dict:
        """Serialise to a plain dict (JSON-safe)."""
        d = asdict(self)
        # asdict already recurses into nested dataclasses
        return d


# ── NVR web API models ───────────────────────────────────────────────


@dataclass
class RtspServerConfig:
    """RTSP server settings from ``queryRTSPServer``."""

    enabled: bool
    port: int
    auth_type: str  # 'Digest' or 'Basic'
    anonymous_access: bool


@dataclass
class ApiServerConfig:
    """API server settings from ``queryApiServer``."""

    enabled: bool
    auth_type: str  # 'Digest' or 'Basic'


@dataclass
class PortConfig:
    """Network port configuration from ``queryNetPortCfg``."""

    http_port: int
    https_port: int
    server_port: int  # TVT protocol port (default 6036)
    rtsp_port: int  # RTSP port (default 554)
    pos_port: int
    auto_report_port: int


@dataclass
class PlatformAccessConfig:
    """Platform Access (Auto Report) settings from ``queryAutoReportCfg``.

    This controls whether the NVR registers itself with a central
    management server (NVMS5000 / CMS) via the TVT platform protocol.

    Corresponds to: Function Panel → Integration → Platform Access
    in the NVR web UI.
    """

    enabled: bool
    server_address: str
    port: int  # default 2009
    report_id: str


@dataclass
class Channel:
    """A camera channel registered on the NVR (from ``queryDevList``)."""

    chl_num: int  # 1-indexed channel number
    name: str  # display name (e.g. 'IP Camera')
    ip: str  # IPC camera's own IP address
    port: int  # IPC protocol port (typically 9008)
    dev_id: str  # NVR internal device ID
    model: str  # e.g. 'TD-9544S4-C'
    manufacturer: str  # e.g. 'TVT'
    protocol: str  # e.g. 'TVT'
    online: bool = True
    add_type: str = ""
    poe_index: int = 0
    chl_type: str = ""
    access_type: str = ""
    auto_report_id: str = ""
    guid: str = ""  # channel GUID ({0000000N-...}) used by per-channel AI commands


@dataclass
class NvrLanFreeDevice:
    """A device discovered by the NVR on its local LAN."""

    ip: str
    mask: str
    gateway: str
    mac: str
    port: int
    http_port: int
    protocol: str
    manufacturer: str
    model: str
    name: str = ""
    serial_number: str = ""
    local_eth_name: str = ""
    sub_ip: str = ""
    sub_ip_netmask: str = ""
    activated: bool | None = None
    activate_status: str = ""
    industry_product_type: str = ""
    device_type: str = ""


LanFreeDevice = NvrLanFreeDevice


@dataclass
class User:
    """An NVR user account (from ``queryUserList``)."""

    user_id: str
    username: str
    user_type: str  # 'default_admin', 'normal', etc.
    enabled: bool
    auth_group: str = ""  # e.g. 'Administrator'
    email: str = ""
    bind_mac: bool = False
    mac: str = "00:00:00:00:00:00"


@dataclass
class PasswordSecurity:
    """Password complexity policy from ``queryPasswordSecurity``."""

    min_strength: str  # weak, medium, strong, stronger
    expiration_days: int  # 0 = never expires
    allowed_levels: list[str] = field(default_factory=list)  # available strength tiers


# ── AI / Face models (Function Panel → AI Event → Face Recognition) ───
#
# TVT NVRs can run *back-end* (NVR-side) analytics on an ordinary camera —
# the web UI's "Enable Detection by NVR" toggle — by allocating a slice of the
# recorder's finite AI-compute pool to a channel. These models cover the
# HTTP-CGI surface that drives that page: the resource pool, the per-channel
# face-detection switch, the face database, and retrieved face events.


@dataclass
class AiResourceChannel:
    """One channel's slice of the NVR AI-compute pool (``queryAIResourceDetail``)."""

    chl_id: str  # channel GUID ({0000000N-...})
    name: str = ""
    event_types: list[str] = field(default_factory=list)  # e.g. ['faceMatch', 'faceDetect']
    connect_state: str = ""
    resource: int = 0  # resource units this channel currently occupies


@dataclass
class AiResource:
    """NVR AI-analytics compute pool (``queryAIResourceDetail``).

    ``supported_event_types`` is what the recorder can run at all (e.g.
    ``faceMatch``, ``faceDetect``, ``tripwire``, ``perimeter``);
    ``total_occupancy`` is how much of the pool is already spoken for.
    """

    supported_event_types: list[str] = field(default_factory=list)
    total_occupancy: float = 0.0  # percent of the AI pool already allocated
    channels: list[AiResourceChannel] = field(default_factory=list)


@dataclass
class NvrFaceDetectionConfig:
    """NVR-side ("Enable Detection by NVR") face detection for one channel.

    From ``queryBackFaceMatch``. ``enabled`` is the page's Detection checkbox;
    ``schedule_id`` is the arming schedule GUID (e.g. the 24x7 schedule).
    """

    chl_id: str
    enabled: bool
    schedule_id: str = ""


@dataclass
class FaceDbGroup:
    """A face-database group (``queryFacePersonnalInfoGroupList``).

    ``group_type`` is one of ``allow`` / ``reject`` / ``limited`` (the match
    lists the recognition engine compares detected faces against).
    """

    group_id: str
    name: str = ""
    group_type: str = ""
    face_count: int = 0


@dataclass
class FacePerson:
    """A person enrolled in a face-database group (``queryFacePersonnalInfoList``).

    Item fields vary by firmware; ``person_id``/``name`` are the stable ones and
    the rest of the record is kept in ``extra`` for callers that need it.
    """

    person_id: str
    name: str = ""
    group_id: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class AlarmServerConfig:
    """NVR "Alarm Server" push configuration (``queryAlarmServerParam``).

    The Alarm Server action makes the NVR push alarm frames — including AI/face
    events — to ``address``:``port`` (optionally at ``url``) in ``data_format``
    (``XML``/``JSON``). ``alarm_types`` is the decimal list of alarm-type codes
    that get pushed (e.g. ``16`` = face match; see
    :data:`~pytvt.alarm_protocol.TVT_ALARM_CODES`). Pair with
    :class:`~pytvt.alarm_server.AlarmServer` to receive the pushes.
    """

    enabled: bool
    address: str = ""
    url: str = ""
    port: int = 80
    data_format: str = "XML"
    schedule_id: str = ""
    alarm_types: list[int] = field(default_factory=list)
    device_id: str = ""
    token: str = ""
    heartbeat_enabled: bool = False
    heartbeat_interval: int = 10


@dataclass
class FaceEvent:
    """A detected/recognized face from a search.

    ``search_face_events`` returns the event *index* (channel, ``img_id``,
    ``frame_time``); the cropped-face JPEG is then fetched on demand with
    :meth:`~pytvt.xml_api.NvrClient.get_face_snapshot` (they are stored
    separately on the recorder, not inline). ``matched`` is True when the face
    hit a database group.
    """

    chl_id: str
    channel: int = 0
    timestamp: str = ""  # frame time, "YYYY-MM-DD HH:MM:SS:NNNNNNN" (UTC)
    occurred_at: datetime | None = None  # parsed UTC event time
    img_id: int = 0  # snapshot id, used with get_face_snapshot()
    frame_time: str = ""  # exact frameTime string to pass to get_face_snapshot()
    matched: bool = False
    group_name: str = ""
    person_name: str = ""
    similarity: float = 0.0
    snapshot: bytes = b""  # populated only if the search fetched images
    background: bytes = b""
