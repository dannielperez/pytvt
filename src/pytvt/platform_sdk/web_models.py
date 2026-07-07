"""Typed DTOs for web (``/service/*``) management-server reads.

These dataclasses represent the pytvt-side normalized shape of data read
through :class:`~pytvt.platform_sdk.web_backend.WebManagementBackend`. Field
names are provisional (documented per-class where not field-verified)
until the PR that implements the corresponding ``/service/*`` endpoint call
validates them against real server responses (see
``docs/ai/backlog/tvt-mgmt-integration.md``, TVT-5..TVT-10). They are
intentionally read-only, carry the raw payload alongside semantic fields,
and never include credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlatformAlarmRecord:
    """A single alarm/event record read from the management web API.

    Provisional: exact alarm-list field names are unverified until TVT-5
    maps a real ``/service/*`` alarm-listing endpoint response.
    """

    alarm_id: str
    alarm_type: str = ""
    device_id: str = ""
    channel_id: str = ""
    occurred_at: str = ""  # raw server timestamp text; parse once the field format is confirmed
    raw_data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "alarm_id": self.alarm_id,
            "alarm_type": self.alarm_type,
            "device_id": self.device_id,
            "channel_id": self.channel_id,
            "occurred_at": self.occurred_at,
        }


@dataclass(frozen=True)
class PlatformServerStatus:
    """A management-server (or managed sub-server) status via the web API.

    Provisional: exact status field mapping is unverified until TVT-6 maps a
    real ``/service/*`` server-status endpoint response.
    """

    guid: str
    name: str = ""
    kind: str = ""  # transfer|storage|ai|alarm_host|tv_wall|management|unknown
    online: bool | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "guid": self.guid,
            "name": self.name,
            "kind": self.kind,
            "online": self.online,
        }


@dataclass(frozen=True)
class PlatformDeviceStatus:
    """A managed device's operational status via the web API.

    Provisional: exact status field mapping is unverified until TVT-6/TVT-7b
    maps a real ``/service/*`` device-status endpoint response.
    """

    device_id: str
    online: bool | None = None
    last_seen_at: str = ""  # raw server timestamp text; parse once the field format is confirmed
    raw_data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "online": self.online,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(frozen=True)
class PlatformAcsStatus:
    """An access-control-server (ACS) status via the web API.

    Provisional: exact status field mapping is unverified until the PR that
    adds ACS-status read support maps a real endpoint response.
    """

    guid: str
    name: str = ""
    online: bool | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "guid": self.guid,
            "name": self.name,
            "online": self.online,
        }


@dataclass(frozen=True)
class PlatformLogEntry:
    """A management-server operation/system log entry via the web API.

    Provisional: exact log field mapping is unverified until TVT-9 maps a
    real ``/service/*`` log-listing endpoint response.
    """

    log_id: str
    log_type: str = ""
    message: str = ""
    occurred_at: str = ""  # raw server timestamp text; parse once the field format is confirmed
    operator: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "log_id": self.log_id,
            "log_type": self.log_type,
            "message": self.message,
            "occurred_at": self.occurred_at,
            "operator": self.operator,
        }
