"""Semantic mappings for PlatformSDK integer constants.

All mappings derive from the TVT PlatformSDK headers (SDKDefs.h, Plat_Def.h,
AccountManageData.h, NVMSRightDefine.h) dated 2025-01-15 and the GUIDemoEx /
CLIDemo reference examples.  Values are verified against live callback output
against a reference TVT management server where feasible.

Every public mapping MUST:
  * fall back to ``"unknown"`` for unrecognized values (never raise),
  * round-trip via ``name_of()`` helpers,
  * expose the raw integer alongside the semantic name when used in normalization.

Adding new constants
--------------------
Only add values that are observed in the SDK headers or demonstrated by
SDK demos.  Do not guess — empirical-only values should live in the backend
module as heuristics and be documented there.
"""

from __future__ import annotations

from typing import Final, Mapping

# ---------------------------------------------------------------------------
# PlatformSDK message types (Plat_MsgType — SDKDefs.h)
# ---------------------------------------------------------------------------

MSGTYPE_CONNECT_NTF: Final = 1
MSGTYPE_RESLIST_NTF: Final = 2
MSGTYPE_LIVEPLAY: Final = 3
MSGTYPE_CONFIG: Final = 6
MSGTYPE_PLAYBACK_NTF: Final = 7
MSGTYPE_AISERVER: Final = 10           # Intelligent analysis server list (stPlat_AnsiServerListMsg)
MSGTYPE_STORAGESERVER: Final = 14      # Storage server list (stPlat_StorageServerListMsg)
MSGTYPE_CHLUNDERSTORAGE: Final = 15    # Channels attached under a storage server
MSGTYPE_STORAGE_INFO: Final = 18       # Disk/storage detail (StorageInfo XML blob)
MSGTYPE_ALARMHOST: Final = 19          # Alarm host list (stPlat_AlarmHostListMsg)
MSGTYPE_ALARMZONE: Final = 20          # Alarm zone list (stPlat_AlarmZoneListMsg)
MSGTYPE_SERVER_CONNECT_NTF: Final = 35 # Generic server online/offline ntf
MSGTYPE_AUTHOURITY_GROUP_INFO: Final = 39  # Authority group info

_MSGTYPE_NAMES: Final[Mapping[int, str]] = {
    MSGTYPE_CONNECT_NTF: "connect",
    MSGTYPE_RESLIST_NTF: "resource_list",
    MSGTYPE_LIVEPLAY: "live_play",
    MSGTYPE_CONFIG: "config",
    MSGTYPE_PLAYBACK_NTF: "playback",
    MSGTYPE_AISERVER: "ai_server",
    MSGTYPE_STORAGESERVER: "storage_server",
    MSGTYPE_CHLUNDERSTORAGE: "channel_under_storage",
    MSGTYPE_STORAGE_INFO: "storage_info",
    MSGTYPE_ALARMHOST: "alarm_host",
    MSGTYPE_ALARMZONE: "alarm_zone",
    MSGTYPE_SERVER_CONNECT_NTF: "server_connect",
    MSGTYPE_AUTHOURITY_GROUP_INFO: "authority_group",
}


def msgtype_name(value: int) -> str:
    """Return lower_snake_case name for a PlatformSDK message type id."""
    return _MSGTYPE_NAMES.get(int(value), "unknown")


# ---------------------------------------------------------------------------
# Connect state (enPlat_ConnectState — SDKDefs.h)
# ---------------------------------------------------------------------------

PLAT_NOLOGIN: Final = 2
PLAT_CONNECTING: Final = 3
PLAT_CONNECT_FAIL: Final = 4
PLAT_CONNECT_SUCCESS: Final = 5
PLAT_LOGINING: Final = 6
PLAT_LOGIN_FAIL: Final = 7
PLAT_LOGIN_SUCCESS: Final = 8
PLAT_DISCONNECT: Final = 9

_CONNECT_STATE_NAMES: Final[Mapping[int, str]] = {
    PLAT_NOLOGIN: "nologin",
    PLAT_CONNECTING: "connecting",
    PLAT_CONNECT_FAIL: "connect_fail",
    PLAT_CONNECT_SUCCESS: "connect_success",
    PLAT_LOGINING: "logining",
    PLAT_LOGIN_FAIL: "login_fail",
    PLAT_LOGIN_SUCCESS: "login_success",
    PLAT_DISCONNECT: "disconnect",
}


def connect_state_name(value: int) -> str:
    """Return lower_snake_case semantic name for an enPlat_ConnectState value."""
    return _CONNECT_STATE_NAMES.get(int(value), "unknown")


def connect_state_is_online(value: int) -> bool:
    """Return True if the value represents an 'up' state (login_success or connect_success)."""
    return int(value) in (PLAT_LOGIN_SUCCESS, PLAT_CONNECT_SUCCESS)


# ---------------------------------------------------------------------------
# Node types (enPlat_NodeType — SDKDefs.h)
# ---------------------------------------------------------------------------

NODETYPE_NONE: Final = 0
NODETYPE_AREA: Final = 1
NODETYPE_DEVICE: Final = 2
NODETYPE_CHANNEL: Final = 3
NODETYPE_SENSOR: Final = 4

_NODE_TYPE_NAMES: Final[Mapping[int, str]] = {
    NODETYPE_NONE: "none",
    NODETYPE_AREA: "area",
    NODETYPE_DEVICE: "device",
    NODETYPE_CHANNEL: "channel",
    NODETYPE_SENSOR: "sensor",
}


def node_type_name(value: int) -> str:
    return _NODE_TYPE_NAMES.get(int(value), "unknown")


# ---------------------------------------------------------------------------
# Device types (enPlat_DevType — SDKDefs.h)
# ---------------------------------------------------------------------------

DEVTYPE_NONE: Final = 0
DEVTYPE_DVR: Final = 1
DEVTYPE_MDVR: Final = 2
DEVTYPE_NVR: Final = 3
DEVTYPE_IPC: Final = 4
DEVTYPE_HIK: Final = 5
DEVTYPE_DAHUA: Final = 6
DEVTYPE_ONVIF: Final = 7

_DEV_TYPE_NAMES: Final[Mapping[int, str]] = {
    DEVTYPE_NONE: "none",
    DEVTYPE_DVR: "dvr",
    DEVTYPE_MDVR: "mdvr",
    DEVTYPE_NVR: "nvr",
    DEVTYPE_IPC: "ipc",
    DEVTYPE_HIK: "hikvision",
    DEVTYPE_DAHUA: "dahua",
    DEVTYPE_ONVIF: "onvif",
}


def device_type_name(value: int) -> str:
    return _DEV_TYPE_NAMES.get(int(value), "unknown")


# ---------------------------------------------------------------------------
# Node operation types (enPlat_NodeOptType — SDKDefs.h)
# ---------------------------------------------------------------------------

NODEOPTTYPE_CREATE: Final = 0
NODEOPTTYPE_DELETE: Final = 1
NODEOPTTYPE_CHANGENAME: Final = 2
NODEOPTTYPE_UPDATESTATE: Final = 3
NODEOPTTYPE_UPDATE_DEVAREA: Final = 4

_NODEOPT_NAMES: Final[Mapping[int, str]] = {
    NODEOPTTYPE_CREATE: "create",
    NODEOPTTYPE_DELETE: "delete",
    NODEOPTTYPE_CHANGENAME: "rename",
    NODEOPTTYPE_UPDATESTATE: "update_state",
    NODEOPTTYPE_UPDATE_DEVAREA: "update_area",
}


def node_opt_name(value: int) -> str:
    return _NODEOPT_NAMES.get(int(value), "unknown")


# ---------------------------------------------------------------------------
# Server types (MSGTYPE_SERVER_CONNECT_NTF — empirical mapping)
#
# The SDK headers declare `unsigned int nServerType` but DO NOT expose a public
# enum for the numeric values.  The header only notes the server-connect
# callback covers "transfer, storage, intelligent-analysis etc." servers.
# We carry a heuristic mapping derived from GUIDemoEx resource categories and
# default to "unknown_<n>" so callers always see the raw value.
#
# WARNING: these values are *empirically observed* and subject to change once
# TVT publishes an authoritative enum.  Do NOT rely on them for any
# destructive / write operations.
# ---------------------------------------------------------------------------

SERVER_TYPE_MANAGEMENT: Final = 0      # Observed: main management server / null-GUID event

_SERVER_TYPE_NAMES: Final[Mapping[int, str]] = {
    SERVER_TYPE_MANAGEMENT: "management",
}


def server_type_name(value: int) -> str:
    """Return a semantic name for an nServerType value.

    Returns ``"management"`` for 0 (empirically the login server itself) and
    falls back to ``f"unknown_{value}"`` for all other ids so downstream
    consumers never see a bare integer without context.
    """
    iv = int(value)
    return _SERVER_TYPE_NAMES.get(iv, f"unknown_{iv}")


# ---------------------------------------------------------------------------
# Alarm host types (enPlat_AlarmHostType — SDKDefs.h)
# ---------------------------------------------------------------------------

ALARMHOST_HK_NET_MODEL: Final = 0
ALARMHOST_KM_NET_MODEL: Final = 1
ALARMHOST_ALEAN_NET_MODEL: Final = 2
ALARMHOST_DSCPC4401_NET_MODEL: Final = 3
ALARMHOST_PARADOX_NET_MODEL: Final = 4
ALARMHOST_BOSCH7400_NET_MODEL: Final = 5

_ALARMHOST_NAMES: Final[Mapping[int, str]] = {
    ALARMHOST_HK_NET_MODEL: "hikvision_net",
    ALARMHOST_KM_NET_MODEL: "km_net",
    ALARMHOST_ALEAN_NET_MODEL: "alean_net",
    ALARMHOST_DSCPC4401_NET_MODEL: "dsc_pc4401",
    ALARMHOST_PARADOX_NET_MODEL: "paradox_net",
    ALARMHOST_BOSCH7400_NET_MODEL: "bosch_7400",
}


def alarm_host_type_name(value: int) -> str:
    return _ALARMHOST_NAMES.get(int(value), "unknown")


# ---------------------------------------------------------------------------
# System / operate permission bitmasks (PlatAuthGroup — SDKDefs.h)
#
# systemAndOperateRright packs BOTH system-level and operate-level permission
# bits in one 64-bit value.  These constants preserve the SDK naming for
# crosswalk with demo sources.
# ---------------------------------------------------------------------------

PLAT_SYSTEM_RIGHT_RESOURCE_CONFIG: Final = 0x01
PLAT_SYSTEM_RIGHT_SERVER_CONFIG: Final = 0x02
PLAT_SYSTEM_RIGHT_REC_CONFIG: Final = 0x04
PLAT_SYSTEM_RIGHT_ALARM_CONFIG: Final = 0x08
PLAT_SYSTEM_RIGHT_USERRIGHT_CONFIG: Final = 0x0010
PLAT_SYSTEM_RIGHT_EMAP_CONFIG: Final = 0x0020
PLAT_SYSTEM_RIGHT_TVWALL_CONFIG: Final = 0x0040
PLAT_SYSTEM_RIGHT_SYSTEM_CONFIG: Final = 0x0080
PLAT_SYSTEM_RIGHT_SYSTEM_BACKUP_STORE: Final = 0x0100
PLAT_SYSTEM_RIGHT_LOG: Final = 0x0200
PLAT_SYSTEM_RIGHT_EMAP_OPERATE: Final = 0x0400
PLAT_SYSTEM_RIGHT_TVWALL_OPERATE: Final = 0x0800
PLAT_SYSTEM_RIGHT_PMS_CONFIG: Final = 0x1000
PLAT_SYSTEM_RIGHT_FACEDEPLOY_CONFIG: Final = 0x2000
PLAT_SYSTEM_RIGHT_FACEATTEND_CONFIG: Final = 0x4000
PLAT_SYSTEM_RIGHT_GUEST_CONFIG: Final = 0x8000
PLAT_SYSTEM_RIGHT_CPC_CONFIG: Final = 0x10000
PLAT_SYSTEM_RIGHT_OATTEND_CONFIG: Final = 0x20000
PLAT_SYSTEM_RIGHT_ACCESS_CONFIG: Final = 0x40000
PLAT_SYSTEM_RIGHT_ALBUM_OPERATE: Final = 0x80000
PLAT_SYSTEM_RIGHT_THERMAL_IMAGE: Final = 0x100000
PLAT_SYSTEM_RIGHT_CHLGROUP_ADD_OPERATE: Final = 0x200000
PLAT_SYSTEM_RIGHT_CHLGROUP_DEL_OPERATE: Final = 0x400000
PLAT_SYSTEM_RIGHT_ORGANIZATION: Final = 0x800000

_SYSTEM_RIGHT_NAMES: Final[Mapping[int, str]] = {
    PLAT_SYSTEM_RIGHT_RESOURCE_CONFIG: "resource_config",
    PLAT_SYSTEM_RIGHT_SERVER_CONFIG: "server_config",
    PLAT_SYSTEM_RIGHT_REC_CONFIG: "record_config",
    PLAT_SYSTEM_RIGHT_ALARM_CONFIG: "alarm_config",
    PLAT_SYSTEM_RIGHT_USERRIGHT_CONFIG: "user_rights_config",
    PLAT_SYSTEM_RIGHT_EMAP_CONFIG: "emap_config",
    PLAT_SYSTEM_RIGHT_TVWALL_CONFIG: "tvwall_config",
    PLAT_SYSTEM_RIGHT_SYSTEM_CONFIG: "system_config",
    PLAT_SYSTEM_RIGHT_SYSTEM_BACKUP_STORE: "system_backup_store",
    PLAT_SYSTEM_RIGHT_LOG: "log",
    PLAT_SYSTEM_RIGHT_EMAP_OPERATE: "emap_operate",
    PLAT_SYSTEM_RIGHT_TVWALL_OPERATE: "tvwall_operate",
    PLAT_SYSTEM_RIGHT_PMS_CONFIG: "parking_config",
    PLAT_SYSTEM_RIGHT_FACEDEPLOY_CONFIG: "face_deploy_config",
    PLAT_SYSTEM_RIGHT_FACEATTEND_CONFIG: "face_attend_config",
    PLAT_SYSTEM_RIGHT_GUEST_CONFIG: "guest_config",
    PLAT_SYSTEM_RIGHT_CPC_CONFIG: "cpc_config",
    PLAT_SYSTEM_RIGHT_OATTEND_CONFIG: "oattend_config",
    PLAT_SYSTEM_RIGHT_ACCESS_CONFIG: "access_control_config",
    PLAT_SYSTEM_RIGHT_ALBUM_OPERATE: "album_operate",
    PLAT_SYSTEM_RIGHT_THERMAL_IMAGE: "thermal_image",
    PLAT_SYSTEM_RIGHT_CHLGROUP_ADD_OPERATE: "channel_group_add",
    PLAT_SYSTEM_RIGHT_CHLGROUP_DEL_OPERATE: "channel_group_delete",
    PLAT_SYSTEM_RIGHT_ORGANIZATION: "organization",
}


def decode_system_rights(mask: int) -> list[str]:
    """Return the list of active system-permission names encoded in ``mask``.

    Unknown bits are reported as ``f"unknown_bit_<hex>"`` so that nothing is
    silently lost.  Bits the SDK reserves for operate-permissions live in a
    separate space and should be decoded with :func:`decode_operate_rights`.
    """
    mv = int(mask)
    names: list[str] = []
    remaining = mv
    for bit, name in _SYSTEM_RIGHT_NAMES.items():
        if mv & bit:
            names.append(name)
            remaining &= ~bit
    # Only emit unknown bits for the 32 LSB of the system slot; higher bits
    # may belong to operate rights packed in the same integer.
    for bit in range(32):
        mask_bit = 1 << bit
        if remaining & mask_bit:
            names.append(f"unknown_bit_0x{mask_bit:x}")
    return sorted(names)


# ---------------------------------------------------------------------------
# Area permission bitmasks (SDKDefs.h — separate namespace from system rights)
# ---------------------------------------------------------------------------

PLAT_AREA_RIGHT_PTZ: Final = 0x01
PLAT_AREA_RIGHT_LIVE: Final = 0x02
PLAT_AREA_RIGHT_PLAYBACK: Final = 0x04
PLAT_AREA_RIGHT_BACKUP: Final = 0x08
PLAT_AREA_RIGHT_RECORD: Final = 0x10
PLAT_AREA_RIGHT_CHANNEL_CONFIG: Final = 0x20
PLAT_AREA_RIGHT_TALKBACK: Final = 0x40
PLAT_AREA_RIGHT_VIEW_LOG: Final = 0x80
PLAT_AREA_RIGHT_DEVICE_CONFIG: Final = 0x100
PLAT_AREA_RIGHT_SOUND: Final = 0x200

_AREA_RIGHT_NAMES: Final[Mapping[int, str]] = {
    PLAT_AREA_RIGHT_PTZ: "ptz",
    PLAT_AREA_RIGHT_LIVE: "live",
    PLAT_AREA_RIGHT_PLAYBACK: "playback",
    PLAT_AREA_RIGHT_BACKUP: "backup",
    PLAT_AREA_RIGHT_RECORD: "record",
    PLAT_AREA_RIGHT_CHANNEL_CONFIG: "channel_config",
    PLAT_AREA_RIGHT_TALKBACK: "talkback",
    PLAT_AREA_RIGHT_VIEW_LOG: "view_log",
    PLAT_AREA_RIGHT_DEVICE_CONFIG: "device_config",
    PLAT_AREA_RIGHT_SOUND: "sound",
}


def decode_area_rights(mask: int) -> list[str]:
    mv = int(mask)
    names: list[str] = [name for bit, name in _AREA_RIGHT_NAMES.items() if mv & bit]
    return sorted(names)


# ---------------------------------------------------------------------------
# Sensitive field redaction
# ---------------------------------------------------------------------------

#: Field names (case-insensitive) that must never be returned from normalized APIs.
SENSITIVE_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
    }
)


def redact_sensitive(payload: dict) -> dict:
    """Return a shallow copy of ``payload`` with sensitive fields redacted.

    Redacted fields are replaced with the literal ``"<redacted>"`` rather than
    stripped so that the *presence* of a credential is still visible to
    operators auditing the output.
    """
    redacted: dict = {}
    for key, value in payload.items():
        if isinstance(key, str) and key.lower() in SENSITIVE_FIELD_NAMES:
            redacted[key] = "<redacted>" if value else ""
        else:
            redacted[key] = value
    return redacted
