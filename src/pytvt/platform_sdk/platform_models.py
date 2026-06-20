"""Typed models for PlatformSDK-normalized results.

These dataclasses represent the pytvt-side normalized shape of data read from
the TVT NVMS PlatformSDK.  They are intentionally *read-only*, carry both the
raw SDK values and semantic names side-by-side, and never include credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlatformResource:
    """A single node (area / device / channel / sensor) from MSGTYPE_RESLIST_NTF."""

    node_id: int
    parent_id: int
    name: str
    node_type: int
    node_type_name: str
    device_type: int
    device_type_name: str
    online: bool | None
    ip: str
    channel_count: int
    channel_number: int
    supports_face_match: bool
    raw_data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "node_type": self.node_type,
            "node_type_name": self.node_type_name,
            "device_type": self.device_type,
            "device_type_name": self.device_type_name,
            "online": self.online,
            "ip": self.ip,
            "channel_count": self.channel_count,
            "channel_number": self.channel_number,
            "supports_face_match": self.supports_face_match,
        }


@dataclass(frozen=True)
class PlatformServer:
    """A generic server record (transfer / storage / AI / alarm / tv-wall etc.).

    Some fields may be empty when a particular server kind doesn't expose them;
    for example the generic ``MSGTYPE_SERVER_CONNECT_NTF`` only yields a GUID,
    numeric type, and connect state — no human-readable name.
    """

    guid: str
    name: str
    kind: str  # transfer|storage|ai|alarm_host|tv_wall|management|unknown
    server_type: int  # raw nServerType for SERVER_CONNECT_NTF, -1 otherwise
    server_type_name: str
    connect_state: int  # raw enPlat_ConnectState, -1 if unknown
    connect_state_name: str
    online: bool | None
    ip: str = ""
    port: int = 0
    channel_count: int = 0
    raw_data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "guid": self.guid,
            "name": self.name,
            "kind": self.kind,
            "server_type": self.server_type,
            "server_type_name": self.server_type_name,
            "connect_state": self.connect_state,
            "connect_state_name": self.connect_state_name,
            "online": self.online,
            "ip": self.ip,
            "port": self.port,
            "channel_count": self.channel_count,
        }


@dataclass(frozen=True)
class PlatformAlarmZone:
    """An alarm zone / defence zone under an alarm host."""

    guid: str
    host_guid: str
    name: str
    zone_type: int
    raw_data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "guid": self.guid,
            "host_guid": self.host_guid,
            "name": self.name,
            "zone_type": self.zone_type,
        }


@dataclass(frozen=True)
class PlatformUser:
    """A management-server user account.

    The ``password`` field from ``Plat_UserInfo`` is intentionally NOT exposed;
    only a boolean presence flag ``has_password`` is returned.
    """

    username: str
    enabled: bool
    permission_group: str
    mac_bind_status: bool
    mac_address: str
    email: str
    description: str
    user_id: int
    has_password: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "enabled": self.enabled,
            "permission_group": self.permission_group,
            "mac_bind_status": self.mac_bind_status,
            "mac_address": self.mac_address,
            "email": self.email,
            "description": self.description,
            "user_id": self.user_id,
            "has_password": self.has_password,
        }


@dataclass(frozen=True)
class PlatformPermissionGroup:
    """A permission group (``Plat_PermisinInfo``) decoded into semantic fields."""

    group_id: int
    name: str
    system_permission_mask: int
    system_permissions: list[str]
    area_permission_mask: int
    area_permissions: list[str]
    raw_data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "name": self.name,
            "system_permission_mask": self.system_permission_mask,
            "system_permissions": list(self.system_permissions),
            "area_permission_mask": self.area_permission_mask,
            "area_permissions": list(self.area_permissions),
        }
