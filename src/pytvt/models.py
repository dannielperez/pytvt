"""Data models and exceptions for the pytvt package.

Two model groups live here:

1. **Scanner models** — typed structures for the bulk-scan pipeline
   (``ScannerConfig``, ``DeviceEntry``, ``CameraInfo``, ``ScanResult``).
2. **NVR API models** — structures returned by the NVR web CGI client
   (``Channel``, ``User``, ``RtspServerConfig``, etc.).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# ── Exceptions ───────────────────────────────────────────────────────


class NvrApiError(Exception):
    """Raised when the NVR web API returns an error."""

    def __init__(self, message: str, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


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

    At minimum ``ip`` is required; every other field has a sensible default.
    """

    ip: str
    site: str = ""
    hostname: str = ""
    mac: str = ""
    port: int = 0  # 0 means "use ScannerConfig.port"
    manufacturer: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> DeviceEntry:
        """Construct from a raw JSON dict, ignoring unknown keys."""
        return cls(
            ip=d["ip"],
            site=d.get("site", ""),
            hostname=d.get("hostname", ""),
            mac=d.get("mac", ""),
            port=int(d.get("port", 0)),
            manufacturer=d.get("manufacturer", ""),
        )

    def effective_port(self, config: ScannerConfig) -> int:
        """Return the port to connect to, falling back to config default."""
        return self.port or config.port


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
