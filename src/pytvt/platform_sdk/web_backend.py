"""``WebManagementBackend`` — the ``/service/*`` web management API backend.

Wires :class:`~pytvt.platform_sdk.web_session.WebSession` (TVT-2, the real
reqLogin/doLogin handshake) into the :class:`BaseManagementBackend` contract.
``login``/``diagnostics``/``get_context``/``load_sdk``/``close`` are fully
implemented; ``get_server_statuses``/``get_device_statuses``/
``get_acs_statuses`` (TVT-6) are implemented. Every other read method
(device/channel enumeration, alarm, operation/status logs) raises
:class:`CapabilityNotAvailable` until its own PR (TVT-5, TVT-8, TVT-9,
TVT-10, see ``docs/ai/backlog/tvt-mgmt-integration.md``) maps the real
endpoint response.
"""

from __future__ import annotations

import platform
from datetime import datetime
from typing import Any

from .base import BaseManagementBackend
from .context import CapabilityMap, PlatformIdentity, SDKContext, SDKIdentity
from .exceptions import CapabilityNotAvailable, ManagementNotAuthenticatedError, ProtocolError
from .models import AlarmSubscription, DeviceStatus, ManagedChannel, ManagedDevice, ServerInfo
from .web_models import PlatformAcsStatus, PlatformServerStatus
from .web_session import DEFAULT_TIMEOUT, WebSession, WebTransport

_READS_NOT_IMPLEMENTED_MSG = (
    "Web management-server read not implemented yet. "
    "See docs/ai/backlog/tvt-mgmt-integration.md (TVT-5..TVT-10) for the slice that adds it."
)

# Endpoints confirmed in docs/ai/knowledge/vendor-boundaries/tvt-nvms-web-service-api.md.
_SERVER_STATUS_PATH = "/service/SystemStatus/getServerStatusList"
_DEVICE_STATUS_PATH = "/service/SystemStatus/getDeviceStatusList"
_ACS_STATUS_PATH = "/service/SystemStatus/getAcsSystemStatusList"

# Server-status fields ARE confirmed (name/ip/port/type/stateType/last*Time), but no
# `guid`-shaped field was observed — the guid/name lookup below falls back tolerantly.
# Device/ACS-status item field names are NOT field-verified live (see the KB doc) —
# try each candidate in order and fall back to "" so a shape surprise degrades to an
# empty field rather than a KeyError.
_GUID_KEYS = ("guid", "id", "serverGuid", "deviceGuid", "acsGuid")
_NAME_KEYS = ("name",)
_KIND_KEYS = ("type", "kind")
_STATE_KEYS = ("stateType", "state", "status")
_ONLINE_TRUE_VALUES = frozenset({"online", "1", "true"})
_ONLINE_FALSE_VALUES = frozenset({"offline", "0", "false"})
_DEVICE_ID_KEYS = ("id", "deviceId", "guid", "sn", "serialNumber")
_LAST_SEEN_KEYS = ("lastOnLineTime", "lastSeenTime", "lastSeen", "time")
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _first_present(item: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return ""


def _derive_online(item: dict[str, str], keys: tuple[str, ...]) -> bool | None:
    """Tolerant online/offline read of a state-like field; unknown codes -> None.

    ``stateType``/``state``/``status`` values are not field-verified beyond the
    confirmed presence of ``stateType`` on server-status rows — only the
    unambiguous online/offline spellings are mapped; anything else stays
    unknown rather than risk mis-classifying an unrecognized status code.
    """
    value = _first_present(item, keys).strip().lower()
    if value in _ONLINE_TRUE_VALUES:
        return True
    if value in _ONLINE_FALSE_VALUES:
        return False
    return None


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, _TIMESTAMP_FORMAT)
    except ValueError:
        return None


class WebManagementBackend(BaseManagementBackend):
    """Backend for the TVT NVMS management web API (``/service/*``).

    Args:
        host: Management-server hostname or IP.
        scheme: ``http`` or ``https``.
        port: TCP port; defaults to the scheme's standard port (see
            :class:`WebSession`).
        timeout: Per-request timeout in seconds.
        verify_tls: When False, skip TLS cert/hostname verification on the
            default transport (self-signed NVR certs are common). Ignored
            when an explicit ``transport`` is supplied.
        transport: Optional injectable :class:`WebTransport`; defaults to
            ``WebSession``'s own default (stdlib ``urllib``).

    The username/password are never stored on this instance — they are
    passed to :meth:`login`, which hands them to a fresh
    :class:`WebSession` (that in turn keeps only a password digest, per its
    own security posture).
    """

    def __init__(
        self,
        host: str,
        *,
        scheme: str = "http",
        port: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        verify_tls: bool = True,
        transport: WebTransport | None = None,
    ) -> None:
        self.host = host
        self.scheme = scheme
        self.port = port
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.transport = transport
        self._session: WebSession | None = None

    @staticmethod
    def _os_family() -> str:
        name = (platform.system() or "unknown").lower()
        return "macos" if name == "darwin" else name

    def load_sdk(self) -> bool:
        # Pure-python HTTP client — no shared library or bridge process to load.
        return True

    def get_context(self) -> SDKContext:
        return SDKContext(
            platform=PlatformIdentity(
                os_family=self._os_family(),
                arch=platform.machine() or None,
                runtime_kind="web",
            ),
            sdk=SDKIdentity(
                vendor="tvt",
                sdk_name=None,
                sdk_family="unknown",
                sdk_version=None,
            ),
            product_scope={"management_server"},
            capabilities=CapabilityMap(
                supports_init=True,
                supports_login=True,
                supports_login_ex=False,
                supports_logout=False,
                supports_device_enumeration=False,
                supports_alarm_subscription=False,
                supports_management_server_login=True,
            ),
            notes=[
                "Web backend authenticates via the documented reqLogin/doLogin handshake (TVT-1/TVT-2).",
                "Server/device/ACS status reads are implemented (TVT-6).",
                "Device/channel enumeration, alarm, and log reads are not yet implemented — their own PRs add them.",
            ],
        )

    def diagnostics(self) -> dict[str, Any]:
        context = self.get_context().as_dict()
        caps_obj = context.get("capabilities")
        caps: dict[str, Any] = caps_obj if isinstance(caps_obj, dict) else {}
        return {
            "backend": "web",
            "context": context,
            "platform": context["platform"],
            "sdk": context["sdk"],
            "product_scope": context["product_scope"],
            "capabilities": context["capabilities"],
            "notes": context["notes"],
            "capability_evidence": {
                "supports_login": {
                    "source": "protocol_spec",
                    "symbols": ["web_session.WebSession.login", "web_crypto", "web_envelope"],
                    "confirmed": True,
                },
                "supports_login_ex": {
                    "source": "backend",
                    "symbols": [],
                    "confirmed": False,
                },
                "supports_logout": {
                    "source": "backend",
                    "symbols": [],
                    "confirmed": False,
                },
                "supports_management_server_validation": {
                    "source": "protocol_spec",
                    "confirmed": True,
                    "note": "reqLogin/doLogin handshake validated against the documented protocol (TVT-1/TVT-2).",
                },
            },
            "sdk_family": "unknown",
            "supports_login": bool(caps["supports_login"]),
            "supports_login_ex": bool(caps["supports_login_ex"]),
            "supports_device_enumeration": bool(caps["supports_device_enumeration"]),
            "supports_management_server_validation": caps["supports_management_server_login"],
            "note": _READS_NOT_IMPLEMENTED_MSG,
        }

    def login(self, username: str, password: str, device_id: str | None = None) -> bool:
        del device_id
        session = WebSession(
            self.host,
            username,
            password,
            scheme=self.scheme,
            port=self.port,
            timeout=self.timeout,
            verify_tls=self.verify_tls,
            transport=self.transport,
        )
        session.login()
        self._session = session
        return session.authenticated

    def _require_session(self) -> WebSession:
        if self._session is None:
            raise ManagementNotAuthenticatedError("call login() before issuing requests")
        return self._session

    def _list_statuses(self, path: str) -> list[dict[str, str]]:
        session = self._require_session()
        envelope = session.request(path)
        if not envelope.ok:
            raise ProtocolError(f"{path} failed: status={envelope.status!r} errorCode={envelope.error_code!r}")
        return envelope.items

    def get_server_statuses(self) -> list[PlatformServerStatus]:
        """List management-server/sub-server statuses (``SystemStatus/getServerStatusList``)."""
        statuses = []
        for index, item in enumerate(self._list_statuses(_SERVER_STATUS_PATH)):
            guid = _first_present(item, _GUID_KEYS) or _first_present(item, _NAME_KEYS) or str(index)
            statuses.append(
                PlatformServerStatus(
                    guid=guid,
                    name=_first_present(item, _NAME_KEYS),
                    kind=_first_present(item, _KIND_KEYS),
                    online=_derive_online(item, _STATE_KEYS),
                    raw_data=dict(item),
                )
            )
        return statuses

    def get_acs_statuses(self) -> list[PlatformAcsStatus]:
        """List access-control-server statuses (``SystemStatus/getAcsSystemStatusList``)."""
        statuses = []
        for index, item in enumerate(self._list_statuses(_ACS_STATUS_PATH)):
            guid = _first_present(item, _GUID_KEYS) or _first_present(item, _NAME_KEYS) or str(index)
            statuses.append(
                PlatformAcsStatus(
                    guid=guid,
                    name=_first_present(item, _NAME_KEYS),
                    online=_derive_online(item, _STATE_KEYS),
                    raw_data=dict(item),
                )
            )
        return statuses

    def get_server_info(self) -> ServerInfo:
        raise CapabilityNotAvailable(_READS_NOT_IMPLEMENTED_MSG)

    def list_devices(self) -> list[ManagedDevice]:
        raise CapabilityNotAvailable(_READS_NOT_IMPLEMENTED_MSG)

    def list_channels(self) -> list[ManagedChannel]:
        raise CapabilityNotAvailable(_READS_NOT_IMPLEMENTED_MSG)

    def get_device_statuses(self) -> list[DeviceStatus]:
        """List managed-device statuses (``SystemStatus/getDeviceStatusList``)."""
        statuses = []
        for index, item in enumerate(self._list_statuses(_DEVICE_STATUS_PATH)):
            device_id = _first_present(item, _DEVICE_ID_KEYS) or str(index)
            statuses.append(
                DeviceStatus(
                    device_id=device_id,
                    online=_derive_online(item, _STATE_KEYS),
                    last_seen_at=_parse_timestamp(_first_present(item, _LAST_SEEN_KEYS)),
                    raw_data=dict(item),
                )
            )
        return statuses

    def subscribe_alarms(self) -> AlarmSubscription:
        raise CapabilityNotAvailable(_READS_NOT_IMPLEMENTED_MSG)

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def supports_sdk(self) -> bool:
        return False

    def supports_native_protocol(self) -> bool:
        return False
