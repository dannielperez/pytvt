"""``WebManagementBackend`` — the ``/service/*`` web management API backend.

Wires :class:`~pytvt.platform_sdk.web_session.WebSession` (TVT-2, the real
reqLogin/doLogin handshake) into the :class:`BaseManagementBackend` contract.
``login``/``diagnostics``/``get_context``/``load_sdk``/``close`` are fully
implemented; ``list_alarm_events``/``list_active_alarms`` (TVT-5) are
implemented. Every other read method (device/channel enumeration, status,
operation/status logs) raises :class:`CapabilityNotAvailable` until its own
PR (TVT-6, TVT-8, TVT-9, TVT-10, see
``docs/ai/backlog/tvt-mgmt-integration.md``) maps the real endpoint
response.
"""

from __future__ import annotations

import platform
from typing import Any

from .base import BaseManagementBackend
from .context import CapabilityMap, PlatformIdentity, SDKContext, SDKIdentity
from .exceptions import CapabilityNotAvailable, ManagementNotAuthenticatedError, ProtocolError
from .models import AlarmSubscription, DeviceStatus, ManagedChannel, ManagedDevice, ServerInfo
from .web_models import PlatformAlarmRecord
from .web_session import DEFAULT_TIMEOUT, WebSession, WebTransport

_READS_NOT_IMPLEMENTED_MSG = (
    "Web management-server read not implemented yet. "
    "See docs/ai/backlog/tvt-mgmt-integration.md (TVT-5..TVT-10) for the slice that adds it."
)

# Endpoints confirmed in docs/ai/knowledge/vendor-boundaries/tvt-nvms-web-service-api.md.
_ALARM_LIST_PATH = "/service/Alarm/getAlarmInfoList"
# The KB endpoint catalog pairs getNodeList with "active alarms" (vs. getAlarmInfoList
# for the historical event log) — the node/zone list is the closest documented match
# for "what's currently alarming" and is unpaginated (a live snapshot, not a log).
_ALARM_NODE_LIST_PATH = "/service/Alarm/getNodeList"

# Item field names are NOT field-verified live for the alarm endpoints (see the KB
# doc) — try each candidate in order and fall back to "" so a shape surprise
# degrades to an empty field rather than a KeyError.
_ALARM_ID_KEYS = ("alarmId", "id", "no", "serialNo", "guid")
_ALARM_TYPE_KEYS = ("type", "alarmType", "eventType")
_ALARM_DEVICE_ID_KEYS = ("deviceId", "devId", "deviceGuid")
_ALARM_CHANNEL_ID_KEYS = ("channelId", "chnId", "channel")
_ALARM_TIME_KEYS = ("time", "alarmTime", "occurTime")


def _first_present(item: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return ""


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
                "Alarm reads are implemented (TVT-5).",
                "Device/channel enumeration, status, and log reads are not yet implemented — their own PRs add them.",
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

    @staticmethod
    def _alarm_record_from_item(item: dict[str, str], index: int) -> PlatformAlarmRecord:
        alarm_id = _first_present(item, _ALARM_ID_KEYS) or str(index)
        return PlatformAlarmRecord(
            alarm_id=alarm_id,
            alarm_type=_first_present(item, _ALARM_TYPE_KEYS),
            device_id=_first_present(item, _ALARM_DEVICE_ID_KEYS),
            channel_id=_first_present(item, _ALARM_CHANNEL_ID_KEYS),
            occurred_at=_first_present(item, _ALARM_TIME_KEYS),
            raw_data=dict(item),
        )

    def _list_alarm_records(self, path: str, *, form: dict[str, str] | None) -> list[PlatformAlarmRecord]:
        session = self._require_session()
        envelope = session.request(path, form=form)
        if not envelope.ok:
            raise ProtocolError(f"{path} failed: status={envelope.status!r} errorCode={envelope.error_code!r}")
        return [self._alarm_record_from_item(item, index) for index, item in enumerate(envelope.items)]

    def list_alarm_events(self, *, page_index: int = 1, page_size: int = 100) -> list[PlatformAlarmRecord]:
        """List historical alarm events (``Alarm/getAlarmInfoList``), paginated."""
        form = {"pageIndex": str(page_index), "pageSize": str(page_size)}
        return self._list_alarm_records(_ALARM_LIST_PATH, form=form)

    def list_active_alarms(self) -> list[PlatformAlarmRecord]:
        """List currently-active alarm nodes/zones (``Alarm/getNodeList``)."""
        return self._list_alarm_records(_ALARM_NODE_LIST_PATH, form=None)

    def get_server_info(self) -> ServerInfo:
        raise CapabilityNotAvailable(_READS_NOT_IMPLEMENTED_MSG)

    def list_devices(self) -> list[ManagedDevice]:
        raise CapabilityNotAvailable(_READS_NOT_IMPLEMENTED_MSG)

    def list_channels(self) -> list[ManagedChannel]:
        raise CapabilityNotAvailable(_READS_NOT_IMPLEMENTED_MSG)

    def get_device_statuses(self) -> list[DeviceStatus]:
        raise CapabilityNotAvailable(_READS_NOT_IMPLEMENTED_MSG)

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
