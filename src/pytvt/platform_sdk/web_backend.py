"""``WebManagementBackend`` — the ``/service/*`` web management API backend.

Wires :class:`~pytvt.platform_sdk.web_session.WebSession` (TVT-2, the real
reqLogin/doLogin handshake) into the :class:`BaseManagementBackend` contract.
``login``/``diagnostics``/``get_context``/``load_sdk``/``close`` are fully
implemented; ``list_alarm_events``/``list_active_alarms`` (TVT-5),
``get_server_statuses``/``get_device_statuses``/``get_acs_statuses`` (TVT-6),
``list_operation_logs``/``list_status_logs`` (TVT-9), and
``list_users``/``list_roles``/``list_permission_groups`` (TVT-8) are implemented.
Every other read method (device/channel enumeration) raises
:class:`CapabilityNotAvailable` until its own PR (TVT-8, TVT-10, see
``docs/ai/backlog/tvt-mgmt-integration.md``) maps the real endpoint response.
"""

from __future__ import annotations

import platform
from datetime import datetime
from typing import Any

from .base import BaseManagementBackend
from .context import CapabilityMap, PlatformIdentity, SDKContext, SDKIdentity
from .exceptions import (
    CapabilityNotAvailable,
    ManagementNotAuthenticatedError,
    ProtocolError,
    SessionExpired,
    TransportError,
)
from .models import AlarmSubscription, DeviceStatus, ManagedChannel, ManagedDevice, ServerInfo
from .platform_constants import decode_area_rights, decode_system_rights, redact_sensitive
from .platform_models import PlatformPermissionGroup, PlatformUser
from .web_models import (
    PlatformAcsStatus,
    PlatformAlarmRecord,
    PlatformLogEntry,
    PlatformServerStatus,
)
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
_OPERATION_LOG_PATH = "/service/SystemMaintain/getLog"
_LOG_EVENT_DICTIONARY_PATH = "/service/SystemMaintain/getLogEventDictionary"
_STATUS_LOG_PATH = "/service/SystemStatus/getStateLog"
_USER_LIST_PATH = "/service/User/getUserList"
_ROLE_LIST_PATH = "/service/User/getRoleList"
_AUTH_GROUP_LIST_PATH = "/service/User/getAuthGroupList"

# Item field names are NOT field-verified live for the log endpoints (see the KB doc) —
# try each candidate in order and fall back to "" so a shape surprise degrades to an
# empty field rather than a KeyError.
_LOG_ID_KEYS = ("id", "logId", "no", "serialNo", "seq")
_LOG_TYPE_KEYS = ("type", "logType", "eventType")
_LOG_MESSAGE_KEYS = ("content", "desc", "description", "message", "detail")
_LOG_TIME_KEYS = ("time", "logTime", "occurTime", "operateTime")
_LOG_OPERATOR_KEYS = ("user", "userName", "operator", "account")
_DICTIONARY_CODE_KEYS = ("type", "id", "code")
_DICTIONARY_TEXT_KEYS = ("name", "desc", "description", "text")
_USER_NAME_KEYS = ("userName", "username", "name", "account")
_USER_ENABLED_KEYS = ("enabled", "enable", "isEnabled", "status")
_USER_GROUP_KEYS = ("permissionGroup", "authGroup", "roleName", "role", "groupName")
_USER_MAC_BIND_KEYS = ("macBindStatus", "macBind", "bindMac", "macBound")
_USER_MAC_KEYS = ("macAddress", "mac", "bindMacAddress")
_USER_EMAIL_KEYS = ("email", "mail")
_USER_DESCRIPTION_KEYS = ("description", "desc", "remark", "memo")
_USER_ID_KEYS = ("userId", "id", "uid")
_PASSWORD_KEYS = ("password", "passwd", "pwd")
_GROUP_ID_KEYS = ("groupId", "roleId", "authGroupId", "id")
_GROUP_NAME_KEYS = ("name", "groupName", "roleName", "authGroupName")
_SYSTEM_PERMISSION_MASK_KEYS = ("systemAndOperateRright", "systemAndOperateRight", "systemPermission", "systemRight")
_AREA_PERMISSION_MASK_KEYS = ("areaRight", "areaPermission", "areaPermissionMask")


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


def _parse_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "enabled", "enable", "online"}


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
                "Server/device/ACS status reads are implemented (TVT-6).",
                "Operation/status log reads are implemented (TVT-9).",
                "Users/roles/permission-group reads are implemented (TVT-8); credentials are never returned.",
                "Device/channel enumeration is not yet implemented — their own PRs add them.",
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

    def _log_event_dictionary(self, session: WebSession) -> dict[str, str]:
        """Best-effort code->text decode for log entries; empty on any failure.

        This is an enrichment lookup, not the primary read — a vendor server
        that rejects or omits this endpoint must not break log listing itself.
        """
        try:
            envelope = session.request(_LOG_EVENT_DICTIONARY_PATH)
        except (TransportError, ProtocolError, SessionExpired):
            return {}
        if not envelope.ok:
            return {}
        dictionary: dict[str, str] = {}
        for item in envelope.items:
            code = _first_present(item, _DICTIONARY_CODE_KEYS)
            text = _first_present(item, _DICTIONARY_TEXT_KEYS)
            if code and text:
                dictionary[code] = text
        return dictionary

    @staticmethod
    def _log_entry_from_item(item: dict[str, str], index: int, event_dictionary: dict[str, str]) -> PlatformLogEntry:
        log_id = _first_present(item, _LOG_ID_KEYS) or str(index)
        log_type_code = _first_present(item, _LOG_TYPE_KEYS)
        return PlatformLogEntry(
            log_id=log_id,
            log_type=event_dictionary.get(log_type_code, log_type_code),
            message=_first_present(item, _LOG_MESSAGE_KEYS),
            occurred_at=_first_present(item, _LOG_TIME_KEYS),
            operator=_first_present(item, _LOG_OPERATOR_KEYS),
            raw_data=dict(item),
        )

    def _list_logs(
        self,
        path: str,
        *,
        start_time: str,
        end_time: str,
        page_index: int,
        page_size: int,
    ) -> list[PlatformLogEntry]:
        session = self._require_session()
        form = {"pageIndex": str(page_index), "pageSize": str(page_size)}
        if start_time:
            form["startTime"] = start_time
        if end_time:
            form["endTime"] = end_time
        envelope = session.request(path, form=form)
        if not envelope.ok:
            raise ProtocolError(f"{path} failed: status={envelope.status!r} errorCode={envelope.error_code!r}")
        event_dictionary = self._log_event_dictionary(session)
        return [self._log_entry_from_item(item, index, event_dictionary) for index, item in enumerate(envelope.items)]

    def list_operation_logs(
        self,
        *,
        start_time: str = "",
        end_time: str = "",
        page_index: int = 1,
        page_size: int = 100,
    ) -> list[PlatformLogEntry]:
        """List operator/system maintenance log entries (``SystemMaintain/getLog``)."""
        return self._list_logs(
            _OPERATION_LOG_PATH,
            start_time=start_time,
            end_time=end_time,
            page_index=page_index,
            page_size=page_size,
        )

    def list_status_logs(
        self,
        *,
        start_time: str = "",
        end_time: str = "",
        page_index: int = 1,
        page_size: int = 100,
    ) -> list[PlatformLogEntry]:
        """List device/server state-change log entries (``SystemStatus/getStateLog``)."""
        return self._list_logs(
            _STATUS_LOG_PATH,
            start_time=start_time,
            end_time=end_time,
            page_index=page_index,
            page_size=page_size,
        )

    def _list_authority_items(self, path: str) -> list[dict[str, str]]:
        session = self._require_session()
        envelope = session.request(path)
        if not envelope.ok:
            raise ProtocolError(f"{path} failed: status={envelope.status!r} errorCode={envelope.error_code!r}")
        return envelope.items

    @staticmethod
    def _user_from_item(item: dict[str, str]) -> PlatformUser:
        password_value = _first_present(item, _PASSWORD_KEYS)
        return PlatformUser(
            username=_first_present(item, _USER_NAME_KEYS),
            enabled=_parse_bool(_first_present(item, _USER_ENABLED_KEYS)),
            permission_group=_first_present(item, _USER_GROUP_KEYS),
            mac_bind_status=_parse_bool(_first_present(item, _USER_MAC_BIND_KEYS)),
            mac_address=_first_present(item, _USER_MAC_KEYS),
            email=_first_present(item, _USER_EMAIL_KEYS),
            description=_first_present(item, _USER_DESCRIPTION_KEYS),
            user_id=_parse_int(_first_present(item, _USER_ID_KEYS)),
            has_password=bool(password_value),
        )

    @staticmethod
    def _permission_group_from_item(item: dict[str, str], index: int) -> PlatformPermissionGroup:
        system_mask = _parse_int(_first_present(item, _SYSTEM_PERMISSION_MASK_KEYS))
        area_mask = _parse_int(_first_present(item, _AREA_PERMISSION_MASK_KEYS))
        return PlatformPermissionGroup(
            group_id=_parse_int(_first_present(item, _GROUP_ID_KEYS)) or index,
            name=_first_present(item, _GROUP_NAME_KEYS),
            system_permission_mask=system_mask,
            system_permissions=decode_system_rights(system_mask),
            area_permission_mask=area_mask,
            area_permissions=decode_area_rights(area_mask),
            raw_data=redact_sensitive(dict(item)),
        )

    def list_users(self) -> list[PlatformUser]:
        """List management-server users (``User/getUserList``), without credentials."""
        return [self._user_from_item(item) for item in self._list_authority_items(_USER_LIST_PATH)]

    def list_roles(self) -> list[PlatformPermissionGroup]:
        """List role records (``User/getRoleList``) as permission-group DTOs."""
        return [
            self._permission_group_from_item(item, index)
            for index, item in enumerate(self._list_authority_items(_ROLE_LIST_PATH))
        ]

    def list_permission_groups(self) -> list[PlatformPermissionGroup]:
        """List authority groups (``User/getAuthGroupList``) as permission-group DTOs."""
        return [
            self._permission_group_from_item(item, index)
            for index, item in enumerate(self._list_authority_items(_AUTH_GROUP_LIST_PATH))
        ]

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
