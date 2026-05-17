"""PlatformSDK-backed management-server backend for pytvt.

This backend targets the TVT NVMS PlatformSDK (libPlatClientSDK.so on Linux,
PlatClientSDK.lib/.dll on Windows).  The PlatformSDK uses a fundamentally
different API surface from the NVR device SDK (libdvrnetsdk.so) that the
legacy sdk.py targets.

PlatformSDK API contract (from PlatSDK_Guid.h, SDK date 2025-01-15):
----------------------------------------------------------------------
  Plat_InitializeEx(const char* szExePath, int nSpecificType) -> bool
  Plat_SetMessageCBEx(fMessageCallback cb, void* pUser) -> bool
  Plat_LoginEx(const char* szHost, unsigned short nPort,
               const char* szUser, const char* szPassword) -> int (loginID)
  Plat_LogOutEx(int lLoginID) -> bool
  Plat_UnInitializeEx() -> bool

Device list arrives asynchronously via the registered message callback:
  MSGTYPE_CONNECT_NTF (1)   — login success / failure
  MSGTYPE_RESLIST_NTF (2)   — device node create / update / delete

The backend blocks on login until PLAT_LOGIN_SUCCESS or PLAT_LOGIN_FAIL
using a threading.Event with a configurable timeout.

Symbol source: nm -D libPlatClientSDK.so | grep " T "
Confirmed exports: Plat_InitializeEx, Plat_LoginEx, Plat_LogOutEx,
                   Plat_UnInitializeEx, Plat_SetMessageCBEx (via PlatSDK_Guid)
"""

from __future__ import annotations

import ctypes
import ctypes as ct
import logging
import platform
import threading
from pathlib import Path
from typing import Any

from .base import BaseManagementBackend
from .context import CapabilityMap, PlatformIdentity, SDKContext, SDKIdentity
from .exceptions import (
    CapabilityNotAvailable,
    ManagementAuthError,
    ManagementNotAuthenticatedError,
    ProtocolError,
    SessionExpired,
    TransportError,
    UnsupportedOnPlatformError,
)
from .models import (
    AlarmSubscription,
    DeviceStatus,
    ManagedChannel,
    ManagedDevice,
    ServerInfo,
)
from . import platform_constants as pc
from .platform_models import (
    PlatformAlarmZone,
    PlatformResource,
    PlatformServer,
)
from .sdk_namespace import NamespacedLibrary, SdkNamespace, make_namespaced_library

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SDK-defined constants (from SDKDefs.h)
# ---------------------------------------------------------------------------

MSGTYPE_CONNECT_NTF = 1
MSGTYPE_RESLIST_NTF = 2
MSGTYPE_AISERVER = 10
MSGTYPE_STORAGESERVER = 14
MSGTYPE_ALARMHOST = 19
MSGTYPE_ALARMZONE = 20
MSGTYPE_SERVER_CONNECT_NTF = 35

# enPlat_ConnectState
PLAT_NOLOGIN = 2
PLAT_CONNECTING = 3
PLAT_CONNECT_FAIL = 4
PLAT_CONNECT_SUCCESS = 5
PLAT_LOGINING = 6
PLAT_LOGIN_FAIL = 7
PLAT_LOGIN_SUCCESS = 8

# enPlat_NodeOptType
NODEOPTTYPE_CREATE = 0
NODEOPTTYPE_DELETE = 1
NODEOPTTYPE_CHANGENAME = 2
NODEOPTTYPE_UPDATESTATE = 3

# enPlat_NodeType
NODETYPE_NONE = 0
NODETYPE_AREA = 1
NODETYPE_DEVICE = 2
NODETYPE_CHANNEL = 3

# Login timeout (seconds) while waiting for async PLAT_LOGIN_SUCCESS/FAIL
_LOGIN_TIMEOUT = 15.0

# ---------------------------------------------------------------------------
# ctypes struct definitions (from SDKDefs.h)
# ---------------------------------------------------------------------------

class _GUID_ST(ct.Structure):
    """GUID_ST from SDKDefs.h — 16 bytes total."""
    _fields_ = [
        ("Data1", ct.c_uint),
        ("Data2", ct.c_ushort),
        ("Data3", ct.c_ushort),
        ("Data4", ct.c_ubyte * 8),
    ]


class _Login_MsgResult(ct.Structure):
    """stLogin_MsgResult from SDKDefs.h."""
    _fields_ = [
        ("nLoginID", ct.c_int),
        ("nConnectState", ct.c_int),
        ("nErrorID", ct.c_uint),
    ]


class _Server_Connect_MsgResult(ct.Structure):
    """stServer_Connect_MsgResult from SDKDefs.h."""

    _fields_ = [
        ("nServerID", _GUID_ST),
        ("nServerType", ct.c_uint),
        ("nConnectState", ct.c_int),
    ]


class _Plat_ResNodeInfo(ct.Structure):
    """stPlat_ResNodeInfo from SDKDefs.h — device/channel node descriptor."""
    _fields_ = [
        ("ulNodeID", ct.c_uint),
        ("guidNodeID", _GUID_ST),
        ("ulParentID", ct.c_uint),
        ("guidParentID", _GUID_ST),
        ("szName", ct.c_char * 256),
        ("nNodeType", ct.c_int),
        ("nDevType", ct.c_int),
        ("nOnline", ct.c_int),
        ("nChlCount", ct.c_int),
        ("usSensorInNum", ct.c_ushort),
        ("usAlarmOutNum", ct.c_ushort),
        ("nChlNO", ct.c_int),
        ("bisSupportFaceMatch", ct.c_bool),
        ("szIp", ct.c_char * 32),
        ("byReserve", ct.c_ubyte * 3),
    ]


class _Plat_ResListMsg(ct.Structure):
    """stPlat_ResListMsg from SDKDefs.h — async resource list message."""
    _fields_ = [
        ("nStructSize", ct.c_int),
        ("nOptType", ct.c_int),
        ("bFinish", ct.c_bool),
        ("ulNodeID", ct.c_uint),
        ("nConnState", ct.c_int),
        ("szName", ct.c_char * 64),
        ("szIp", ct.c_char * 32),
        ("nBufLen", ct.c_int),
        ("lpNodeinfo", ct.POINTER(_Plat_ResNodeInfo)),
    ]


# --- AI (Intelligent Analysis) server list message -------------------------
class _Plat_AnsiServerInfo(ct.Structure):
    _pack_ = 4
    _fields_ = [
        ("ulNodeID", ct.c_uint),
        ("szName", ct.c_char * 256),
        ("guid", _GUID_ST),
    ]


class _Plat_AnsiServerListMsg(ct.Structure):
    """stPlat_AnsiServerListMsg from SDKDefs.h."""
    _pack_ = 4
    _fields_ = [
        ("nStructSize", ct.c_int),
        ("nOptType", ct.c_int),
        ("ulNodeID", ct.c_uint),
        ("guid", _GUID_ST),
        ("szName", ct.c_char * 64),
        ("nBufLen", ct.c_int),
        ("bFinshed", ct.c_bool),
        ("byReserve", ct.c_ubyte * 3),
        ("lpNodeinfo", ct.POINTER(_Plat_AnsiServerInfo)),
    ]


# --- Storage server list message ------------------------------------------
class _Plat_StorageServerInfo(ct.Structure):
    _pack_ = 4
    _fields_ = [
        ("szName", ct.c_char * 256),
        ("guid", _GUID_ST),
    ]


class _Plat_StorageServerListMsg(ct.Structure):
    """stPlat_StorageServerListMsg from SDKDefs.h."""
    _pack_ = 4
    _fields_ = [
        ("nStructSize", ct.c_int),
        ("nOptType", ct.c_int),
        ("guid", _GUID_ST),
        ("szName", ct.c_char * 64),
        ("nBufLen", ct.c_int),
        ("bFinshed", ct.c_byte),
        ("byReserve", ct.c_ubyte * 3),
        ("lpNodeinfo", ct.POINTER(_Plat_StorageServerInfo)),
    ]


# --- Alarm host list message ----------------------------------------------
class _Plat_AlarmHostInfo(ct.Structure):
    _pack_ = 4
    _fields_ = [
        ("szName", ct.c_char * 256),
        ("alarmHostType", ct.c_int),
        ("subSystemNum", ct.c_int),
        ("zoneNum", ct.c_int),
        ("alarmOutNum", ct.c_int),
        ("ipOrSn", ct.c_ubyte * 48),
        ("port", ct.c_ushort),
        ("byReserve", ct.c_ubyte * 2),
        ("guid", _GUID_ST),
    ]


class _Plat_AlarmHostListMsg(ct.Structure):
    """stPlat_AlarmHostListMsg from SDKDefs.h."""
    _pack_ = 4
    _fields_ = [
        ("nStructSize", ct.c_int),
        ("nOptType", ct.c_int),
        ("guid", _GUID_ST),
        ("szName", ct.c_char * 64),
        ("nBufLen", ct.c_int),
        ("bFinshed", ct.c_byte),
        ("byReserve", ct.c_ubyte * 3),
        ("lpNodeinfo", ct.POINTER(_Plat_AlarmHostInfo)),
    ]


# --- Alarm zone list message ----------------------------------------------
class _Plat_AlarmZoneInfo(ct.Structure):
    _pack_ = 4
    _fields_ = [
        ("szName", ct.c_char * 256),
        ("alarmZoneType", ct.c_int),
        ("guid", _GUID_ST),
        ("hostGuid", _GUID_ST),
    ]


class _Plat_AlarmZoneListMsg(ct.Structure):
    """stPlat_AlarmZoneListMsg from SDKDefs.h."""
    _pack_ = 4
    _fields_ = [
        ("nStructSize", ct.c_int),
        ("nOptType", ct.c_int),
        ("guid", _GUID_ST),
        ("szName", ct.c_char * 64),
        ("nBufLen", ct.c_int),
        ("bFinshed", ct.c_byte),
        ("byReserve", ct.c_ubyte * 3),
        ("lpNodeinfo", ct.POINTER(_Plat_AlarmZoneInfo)),
    ]


# ---------------------------------------------------------------------------
# Callback type
# ---------------------------------------------------------------------------

# void (PLAT_CALL *fMessageCallback)(int lLoginID, int lMsgType,
#                                    unsigned char *szBuf, int nLen, void *pUser)
_fMessageCallback = ct.CFUNCTYPE(
    None,           # return void
    ct.c_int,       # lLoginID
    ct.c_int,       # lMsgType
    ct.POINTER(ct.c_ubyte),  # szBuf
    ct.c_int,       # nLen
    ct.c_void_p,    # pUser
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _SDKLoadError(Exception):
    """Internal — library failed to load."""


def _load_library(sdk_path: str) -> ctypes.CDLL:
    path = Path(sdk_path)
    if not path.exists():
        raise _SDKLoadError(f"SDK path does not exist: {sdk_path!r}")
    if not path.is_file():
        raise _SDKLoadError(f"SDK path is not a file: {sdk_path!r}")
    try:
        lib = ctypes.CDLL(str(path))
    except OSError as exc:
        raise _SDKLoadError(f"ctypes could not load SDK: {exc}") from exc
    return lib


def _detect_sdk_machine(sdk_path: str) -> str | None:
    try:
        with open(sdk_path, "rb") as fh:
            header = fh.read(20)
    except OSError:
        return None
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return None
    e_machine = int.from_bytes(header[18:20], byteorder="little", signed=False)
    return {0x03: "x86", 0x3E: "x86_64", 0x28: "arm", 0xB7: "aarch64"}.get(
        e_machine, f"elf_machine_{e_machine}"
    )



def _normalize_os_family() -> str:
    s = (platform.system() or "").lower()
    if s.startswith("darwin"):
        return "macos"
    return s if s in {"linux", "windows"} else "unknown"


def _plat_node_to_dict(node: _Plat_ResNodeInfo) -> dict[str, Any]:
    return {
        "ulNodeID": node.ulNodeID,
        "ulParentID": node.ulParentID,
        "guidNodeID": _guid_to_hex(node.guidNodeID),
        "guidParentID": _guid_to_hex(node.guidParentID),
        "szName": node.szName.decode("utf-8", errors="replace").rstrip("\x00").strip(),
        "nNodeType": node.nNodeType,
        "nDevType": node.nDevType,
        "nOnline": node.nOnline,
        "nChlCount": node.nChlCount,
        "nChlNO": node.nChlNO,
        "usSensorInNum": node.usSensorInNum,
        "usAlarmOutNum": node.usAlarmOutNum,
        "bisSupportFaceMatch": bool(node.bisSupportFaceMatch),
        "szIp": node.szIp.decode("utf-8", errors="replace").rstrip("\x00").strip(),
    }


def _guid_to_hex(guid: _GUID_ST) -> str:
    """Return deterministic GUID hex text for logging and dictionary keys."""

    data4 = "".join(f"{x:02x}" for x in guid.Data4)
    return f"{guid.Data1:08x}-{guid.Data2:04x}-{guid.Data3:04x}-{data4}"


def _decode_cstr(raw: bytes, *, encoding: str = "utf-8") -> str:
    """Decode a ctypes char buffer, strip NUL padding and whitespace."""
    return raw.decode(encoding, errors="replace").rstrip("\x00").strip()


def _resource_to_model(node: dict[str, Any]) -> PlatformResource:
    """Normalize a raw MSGTYPE_RESLIST_NTF node dict into a PlatformResource."""
    node_type = int(node.get("nNodeType", 0))
    dev_type = int(node.get("nDevType", 0))
    online_val = node.get("nOnline", None)
    if node_type in (pc.NODETYPE_DEVICE, pc.NODETYPE_CHANNEL) and online_val is not None:
        online: bool | None = int(online_val) == 1
    else:
        online = None
    return PlatformResource(
        node_id=int(node.get("ulNodeID", 0)),
        parent_id=int(node.get("ulParentID", 0)),
        name=str(node.get("szName", "") or ""),
        node_type=node_type,
        node_type_name=pc.node_type_name(node_type),
        device_type=dev_type,
        device_type_name=pc.device_type_name(dev_type),
        online=online,
        ip=str(node.get("szIp", "") or ""),
        channel_count=int(node.get("nChlCount", 0) or 0),
        channel_number=int(node.get("nChlNO", -1) or -1),
        supports_face_match=bool(node.get("bisSupportFaceMatch", False)),
        raw_data=dict(node),
    )


# ---------------------------------------------------------------------------
# Session state — holds everything accumulated during async login + enumeration
# ---------------------------------------------------------------------------

class _PlatSessionState:
    """Thread-safe container for async callback state during a PlatformSDK session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._login_event = threading.Event()
        self._list_done_event = threading.Event()
        self.login_success: bool = False
        self.login_error_id: int = 0
        self.connect_state: int = PLAT_NOLOGIN
        # Nodes received via MSGTYPE_RESLIST_NTF (type NODETYPE_DEVICE)
        self.device_nodes: list[dict[str, Any]] = []
        # All nodes (including channels) for richer diagnostics
        self.all_nodes: list[dict[str, Any]] = []
        # Server connectivity callbacks (MSGTYPE_SERVER_CONNECT_NTF)
        self.server_connect_events: list[dict[str, Any]] = []
        # AI / intelligent-analysis server callbacks (MSGTYPE_AISERVER)
        self.ai_servers: dict[str, dict[str, Any]] = {}
        # Storage server callbacks (MSGTYPE_STORAGESERVER)
        self.storage_servers: dict[str, dict[str, Any]] = {}
        # Alarm host callbacks (MSGTYPE_ALARMHOST)
        self.alarm_hosts: dict[str, dict[str, Any]] = {}
        # Alarm zone callbacks (MSGTYPE_ALARMZONE)
        self.alarm_zones: dict[str, dict[str, Any]] = {}

    def on_connect_ntf(self, login_id: int, login_result: _Login_MsgResult) -> None:
        with self._lock:
            self.connect_state = login_result.nConnectState
            if login_result.nConnectState == PLAT_LOGIN_SUCCESS:
                self.login_success = True
                self._login_event.set()
            elif login_result.nConnectState == PLAT_LOGIN_FAIL:
                self.login_success = False
                self.login_error_id = login_result.nErrorID
                self._login_event.set()

    def on_reslist_ntf(self, list_msg: _Plat_ResListMsg) -> None:
        with self._lock:
            if list_msg.nOptType == NODEOPTTYPE_CREATE and list_msg.lpNodeinfo:
                node = list_msg.lpNodeinfo.contents
                node_dict = _plat_node_to_dict(node)
                node_dict["_opt_type"] = "create"
                self.all_nodes.append(node_dict)
                # Collect top-level devices (not areas, not channels)
                if node.nNodeType == NODETYPE_DEVICE:
                    self.device_nodes.append(node_dict)
            elif list_msg.nOptType == NODEOPTTYPE_UPDATESTATE:
                # State update — node ID + online state only
                self.all_nodes.append({
                    "_opt_type": "update_state",
                    "ulNodeID": list_msg.ulNodeID,
                    "nConnState": list_msg.nConnState,
                    "szName": list_msg.szName.decode("utf-8", errors="replace").strip("\x00").strip(),
                    "szIp": list_msg.szIp.decode("utf-8", errors="replace").strip("\x00").strip(),
                })
            if list_msg.bFinish:
                self._list_done_event.set()

    def on_server_connect_ntf(self, payload: _Server_Connect_MsgResult) -> None:
        with self._lock:
            self.server_connect_events.append(
                {
                    "server_guid": _guid_to_hex(payload.nServerID),
                    "server_type": int(payload.nServerType),
                    "connect_state": int(payload.nConnectState),
                }
            )

    def on_ai_server_ntf(self, msg: "_Plat_AnsiServerListMsg") -> None:
        guid = _guid_to_hex(msg.guid)
        opt = int(msg.nOptType)
        with self._lock:
            if opt == NODEOPTTYPE_DELETE:
                self.ai_servers.pop(guid, None)
                return
            if not msg.lpNodeinfo:
                # Name-only/partial update path
                name = _decode_cstr(bytes(msg.szName))
                existing = self.ai_servers.setdefault(guid, {"guid": guid})
                if name:
                    existing["name"] = name
                return
            info = msg.lpNodeinfo.contents
            self.ai_servers[guid] = {
                "guid": guid,
                "node_id": int(info.ulNodeID),
                "name": _decode_cstr(bytes(info.szName)),
            }

    def on_storage_server_ntf(self, msg: "_Plat_StorageServerListMsg") -> None:
        guid = _guid_to_hex(msg.guid)
        opt = int(msg.nOptType)
        with self._lock:
            if opt == NODEOPTTYPE_DELETE:
                self.storage_servers.pop(guid, None)
                return
            if not msg.lpNodeinfo:
                name = _decode_cstr(bytes(msg.szName))
                existing = self.storage_servers.setdefault(guid, {"guid": guid})
                if name:
                    existing["name"] = name
                return
            info = msg.lpNodeinfo.contents
            self.storage_servers[guid] = {
                "guid": guid,
                "name": _decode_cstr(bytes(info.szName)),
            }

    def on_alarm_host_ntf(self, msg: "_Plat_AlarmHostListMsg") -> None:
        guid = _guid_to_hex(msg.guid)
        opt = int(msg.nOptType)
        with self._lock:
            if opt == NODEOPTTYPE_DELETE:
                self.alarm_hosts.pop(guid, None)
                return
            if not msg.lpNodeinfo:
                name = _decode_cstr(bytes(msg.szName))
                existing = self.alarm_hosts.setdefault(guid, {"guid": guid})
                if name:
                    existing["name"] = name
                return
            info = msg.lpNodeinfo.contents
            ip_or_sn = bytes(info.ipOrSn).rstrip(b"\x00").decode(
                "utf-8", errors="replace"
            ).strip()
            self.alarm_hosts[guid] = {
                "guid": guid,
                "name": _decode_cstr(bytes(info.szName)),
                "alarm_host_type": int(info.alarmHostType),
                "sub_system_num": int(info.subSystemNum),
                "zone_num": int(info.zoneNum),
                "alarm_out_num": int(info.alarmOutNum),
                "ip_or_sn": ip_or_sn,
                "port": int(info.port),
            }

    def on_alarm_zone_ntf(self, msg: "_Plat_AlarmZoneListMsg") -> None:
        guid = _guid_to_hex(msg.guid)
        opt = int(msg.nOptType)
        with self._lock:
            if opt == NODEOPTTYPE_DELETE:
                self.alarm_zones.pop(guid, None)
                return
            if not msg.lpNodeinfo:
                return
            info = msg.lpNodeinfo.contents
            self.alarm_zones[guid] = {
                "guid": guid,
                "host_guid": _guid_to_hex(info.hostGuid),
                "name": _decode_cstr(bytes(info.szName)),
                "zone_type": int(info.alarmZoneType),
            }

    def wait_for_login(self, timeout: float = _LOGIN_TIMEOUT) -> bool:
        """Block until PLAT_LOGIN_SUCCESS or PLAT_LOGIN_FAIL.  Returns True on success."""
        self._login_event.wait(timeout=timeout)
        return self.login_success

    def wait_for_device_list(self, timeout: float = 10.0) -> bool:
        """Block until bFinish=True from MSGTYPE_RESLIST_NTF."""
        return self._list_done_event.wait(timeout=timeout)


# ---------------------------------------------------------------------------
# PlatformSDKClient — active session wrapper
# ---------------------------------------------------------------------------

class PlatformSDKClient:
    """Manages a single authenticated session using the TVT PlatformSDK."""

    def __init__(self, ns_lib: NamespacedLibrary, host: str, port: int) -> None:
        self._ns = ns_lib
        self._host = host
        self._port = port
        self._login_id: int | None = None
        self._state: _PlatSessionState | None = None
        # We must keep a reference to the callback to prevent GC
        self._cb_ref: Any = None
        self._authenticated = False

    def _require_fn(self, capability: str) -> Any:
        """Resolve, bind, and return the ctypes function for *capability*."""
        return self._ns.bind_function(capability)

    def login(self, username: str, password: str) -> None:
        """Authenticate and wait for PLAT_LOGIN_SUCCESS or raise."""
        state = _PlatSessionState()
        self._state = state

        # Use NamespacedLibrary for: init (shared), set_message_callback (PLAT-only), login (shared name, PLAT binding)
        init_fn = self._ns.bind_function("init")
        # set_message_callback uses the PLAT-specific fMessageCallback type — override argtypes
        set_cb_fn = self._ns.bind_function(
            "set_message_callback",
            argtypes=(_fMessageCallback, ct.c_void_p),
            restype=ct.c_bool,
        )
        login_fn = self._ns.bind_function("login")

        if not self._ns.call_init():
            raise TransportError("Plat_InitializeEx returned failure")

        def _message_cb(
            login_id: int,
            msg_type: int,
            buf: Any,
            buf_len: int,
            _user: Any,
        ) -> None:
            try:
                if msg_type == MSGTYPE_CONNECT_NTF and buf_len >= ct.sizeof(_Login_MsgResult):
                    result = ct.cast(buf, ct.POINTER(_Login_MsgResult)).contents
                    state.on_connect_ntf(login_id, result)
                elif msg_type == MSGTYPE_RESLIST_NTF and buf_len >= ct.sizeof(_Plat_ResListMsg):
                    list_msg = ct.cast(buf, ct.POINTER(_Plat_ResListMsg)).contents
                    state.on_reslist_ntf(list_msg)
                elif (
                    msg_type == MSGTYPE_SERVER_CONNECT_NTF
                    and buf_len >= ct.sizeof(_Server_Connect_MsgResult)
                ):
                    server_msg = ct.cast(buf, ct.POINTER(_Server_Connect_MsgResult)).contents
                    state.on_server_connect_ntf(server_msg)
                elif (
                    msg_type == MSGTYPE_AISERVER
                    and buf_len >= ct.sizeof(_Plat_AnsiServerListMsg)
                ):
                    ai_msg = ct.cast(buf, ct.POINTER(_Plat_AnsiServerListMsg)).contents
                    state.on_ai_server_ntf(ai_msg)
                elif (
                    msg_type == MSGTYPE_STORAGESERVER
                    and buf_len >= ct.sizeof(_Plat_StorageServerListMsg)
                ):
                    ss_msg = ct.cast(buf, ct.POINTER(_Plat_StorageServerListMsg)).contents
                    state.on_storage_server_ntf(ss_msg)
                elif (
                    msg_type == MSGTYPE_ALARMHOST
                    and buf_len >= ct.sizeof(_Plat_AlarmHostListMsg)
                ):
                    ah_msg = ct.cast(buf, ct.POINTER(_Plat_AlarmHostListMsg)).contents
                    state.on_alarm_host_ntf(ah_msg)
                elif (
                    msg_type == MSGTYPE_ALARMZONE
                    and buf_len >= ct.sizeof(_Plat_AlarmZoneListMsg)
                ):
                    az_msg = ct.cast(buf, ct.POINTER(_Plat_AlarmZoneListMsg)).contents
                    state.on_alarm_zone_ntf(az_msg)
            except Exception:
                logger.exception("PlatformSDK callback raised unexpectedly")

        # Keep a strong reference so GC doesn't collect while in use
        cb = _fMessageCallback(_message_cb)
        self._cb_ref = cb

        if not set_cb_fn(cb, None):
            raise TransportError("Plat_SetMessageCBEx returned failure")

        login_id = login_fn(
            self._host.encode("utf-8"),
            self._port,
            username.encode("utf-8"),
            password.encode("utf-8"),
        )
        if login_id < 0:
            raise ManagementAuthError(
                f"Plat_LoginEx returned invalid login ID {login_id} for {self._host}:{self._port}"
            )

        self._login_id = login_id
        logger.debug("PlatformSDK: Plat_LoginEx returned login_id=%d; waiting for async auth", login_id)

        if not state.wait_for_login(timeout=_LOGIN_TIMEOUT):
            connect_state = state.connect_state
            error_id = state.login_error_id
            raise ManagementAuthError(
                f"PlatformSDK login timed out or failed for {self._host}:{self._port} "
                f"(connect_state={connect_state}, error_id={error_id})"
            )

        self._authenticated = True
        logger.debug("PlatformSDK: login successful; login_id=%d", login_id)

        # Request device list — it arrives via callback; wait up to 10s
        state.wait_for_device_list(timeout=10.0)

    def get_server_info(self) -> ServerInfo:
        """Return basic server info from the login state."""
        if not self._authenticated or self._state is None:
            raise SessionExpired("PlatformSDK session not authenticated.")
        return ServerInfo(
            host=self._host,
            port=self._port,
            model="",
            firmware="",
            serial_number="",
            raw_data={
                "sdk_family": "platform_sdk",
                "login_id": self._login_id,
                "connect_state": self._state.connect_state,
                "node_count": len(self._state.all_nodes),
            },
        )

    def list_devices(self) -> list[ManagedDevice]:
        """Return device nodes received via MSGTYPE_RESLIST_NTF callback."""
        if not self._authenticated or self._state is None:
            raise SessionExpired("PlatformSDK session not authenticated.")
        devices: list[ManagedDevice] = []
        for node in self._state.device_nodes:
            node_id = str(node.get("ulNodeID", ""))
            name = node.get("szName") or node_id
            ip = node.get("szIp", "")
            online = node.get("nOnline", 0)
            status = "online" if online == 1 else "offline"
            raw = dict(node)
            raw["sdk_family"] = "platform_sdk"
            devices.append(ManagedDevice(
                device_id=node_id,
                name=name,
                ip_address=ip,
                status=status,
                raw_data=raw,
            ))
        return devices

    def list_channels(self) -> list[ManagedChannel]:
        """Return channel nodes received via MSGTYPE_RESLIST_NTF callback."""
        if not self._authenticated or self._state is None:
            raise SessionExpired("PlatformSDK session not authenticated.")
        from .models import ManagedChannel
        channels: list[ManagedChannel] = []
        for node in self._state.all_nodes:
            if node.get("nNodeType") != NODETYPE_CHANNEL:
                continue
            channels.append(ManagedChannel(
                channel_id=str(node.get("ulNodeID", "")),
                name=node.get("szName", ""),
                device_id=str(node.get("ulParentID", "")),
                raw_data=dict(node),
            ))
        return channels

    def get_device_statuses(self) -> list[DeviceStatus]:
        """Return online/offline status from accumulated node state."""
        if not self._authenticated or self._state is None:
            raise SessionExpired("PlatformSDK session not authenticated.")
        statuses: list[DeviceStatus] = []
        for node in self._state.device_nodes:
            node_id = str(node.get("ulNodeID", ""))
            online_val = node.get("nOnline", 0)
            online = online_val == 1
            statuses.append(DeviceStatus(
                device_id=node_id,
                online=online,
                raw_data={"_source": "reslist_ntf", "nOnline": online_val},
            ))
        return statuses

    def list_resources(self) -> list[dict[str, Any]]:
        """Return raw resource nodes accumulated from MSGTYPE_RESLIST_NTF."""

        if not self._authenticated or self._state is None:
            raise SessionExpired("PlatformSDK session not authenticated.")
        return [dict(row) for row in self._state.all_nodes]

    # --- Normalized resource views ---------------------------------------

    def list_resources_normalized(self) -> list[PlatformResource]:
        """Return normalized PlatformResource models for every known node."""

        if not self._authenticated or self._state is None:
            raise SessionExpired("PlatformSDK session not authenticated.")
        # Only nodes created via NODEOPTTYPE_CREATE carry a full _Plat_ResNodeInfo
        # payload.  Status-updates (_opt_type == "update_state") don't, so skip.
        out: list[PlatformResource] = []
        for raw in self._state.all_nodes:
            if raw.get("_opt_type") != "create":
                continue
            out.append(_resource_to_model(raw))
        return out

    def list_areas(self) -> list[PlatformResource]:
        return [r for r in self.list_resources_normalized() if r.node_type == pc.NODETYPE_AREA]

    def list_devices_normalized(self) -> list[PlatformResource]:
        return [r for r in self.list_resources_normalized() if r.node_type == pc.NODETYPE_DEVICE]

    def list_channels_normalized(self) -> list[PlatformResource]:
        return [r for r in self.list_resources_normalized() if r.node_type == pc.NODETYPE_CHANNEL]

    def list_resources_tree(self) -> list[dict[str, Any]]:
        """Return a hierarchical tree of resources keyed by parent_id."""
        resources = self.list_resources_normalized()
        by_parent: dict[int, list[dict[str, Any]]] = {}
        by_id: dict[int, dict[str, Any]] = {}
        for r in resources:
            payload = r.as_dict()
            payload["children"] = []
            by_id[r.node_id] = payload
            by_parent.setdefault(r.parent_id, []).append(payload)
        roots: list[dict[str, Any]] = []
        for r in resources:
            node = by_id[r.node_id]
            if r.parent_id in by_id and r.parent_id != r.node_id:
                by_id[r.parent_id]["children"].append(node)
            else:
                roots.append(node)
        return roots

    def find_resource_by_guid(self, guid: str) -> PlatformResource | None:
        needle = guid.strip().lower()
        for r in self.list_resources_normalized():
            if r.raw_data.get("guidNodeID", "").lower() == needle:
                return r
        return None

    def find_resource_by_name(self, name: str) -> list[PlatformResource]:
        needle = name.strip().lower()
        return [r for r in self.list_resources_normalized() if needle in r.name.lower()]

    # --- Server enumeration (callback-driven) ---------------------------

    def list_server_connection_events(self) -> list[dict[str, Any]]:
        """Return raw server-connect callback rows from MSGTYPE_SERVER_CONNECT_NTF."""

        if not self._authenticated or self._state is None:
            raise SessionExpired("PlatformSDK session not authenticated.")
        return [dict(row) for row in self._state.server_connect_events]

    def _latest_server_events_by_guid(self) -> dict[str, dict[str, Any]]:
        assert self._state is not None  # narrowed by caller
        latest: dict[str, dict[str, Any]] = {}
        for event in self._state.server_connect_events:
            guid = str(event.get("server_guid", "")).strip()
            if not guid:
                continue
            latest[guid] = dict(event)
        return latest

    def list_servers(self) -> list[PlatformServer]:
        """Return every known server across all PlatformSDK callback channels.

        Merges MSGTYPE_SERVER_CONNECT_NTF (generic connection state) with the
        type-specific AI/storage/alarm-host callbacks to produce a single
        deduplicated, semantically-labelled list keyed by GUID.
        """
        if not self._authenticated or self._state is None:
            raise SessionExpired("PlatformSDK session not authenticated.")

        events = self._latest_server_events_by_guid()
        servers: dict[str, PlatformServer] = {}

        def _make(
            guid: str,
            *,
            name: str,
            kind: str,
            server_type: int,
            extra_raw: dict[str, Any] | None = None,
            ip: str = "",
            port: int = 0,
        ) -> PlatformServer:
            event = events.get(guid, {})
            connect_state = int(event.get("connect_state", -1))
            raw_data = {
                "connect_event": dict(event),
                **(extra_raw or {}),
            }
            return PlatformServer(
                guid=guid,
                name=name,
                kind=kind,
                server_type=int(event.get("server_type", server_type)),
                server_type_name=pc.server_type_name(
                    int(event.get("server_type", server_type))
                ),
                connect_state=connect_state,
                connect_state_name=pc.connect_state_name(connect_state)
                if connect_state >= 0
                else "unknown",
                online=pc.connect_state_is_online(connect_state)
                if connect_state >= 0
                else None,
                ip=ip,
                port=port,
                raw_data=raw_data,
            )

        for guid, payload in self._state.ai_servers.items():
            servers[guid] = _make(
                guid,
                name=payload.get("name", ""),
                kind="ai",
                server_type=-1,
                extra_raw={"source": "ai_server_ntf", **payload},
            )
        for guid, payload in self._state.storage_servers.items():
            servers[guid] = _make(
                guid,
                name=payload.get("name", ""),
                kind="storage",
                server_type=-1,
                extra_raw={"source": "storage_server_ntf", **payload},
            )
        for guid, payload in self._state.alarm_hosts.items():
            servers[guid] = _make(
                guid,
                name=payload.get("name", ""),
                kind="alarm_host",
                server_type=-1,
                ip=payload.get("ip_or_sn", ""),
                port=int(payload.get("port", 0) or 0),
                extra_raw={"source": "alarm_host_ntf", **payload},
            )

        # Include server-connect events that had no accompanying typed callback.
        # For these, we don't know the semantic kind so we label as "unknown".
        for guid, event in events.items():
            if guid in servers:
                continue
            server_type = int(event.get("server_type", -1))
            # guid_ST_NULL (all zeros) typically represents the management
            # server's own heartbeat event.
            is_null_guid = guid.replace("-", "").strip("0") == ""
            kind = "management" if is_null_guid else "unknown"
            servers[guid] = _make(
                guid,
                name="Management Server" if is_null_guid else "",
                kind=kind,
                server_type=server_type,
                extra_raw={"source": "server_connect_ntf"},
            )
        return list(servers.values())

    def list_transfer_servers(self) -> list[PlatformServer]:
        """Return servers that do not match any typed callback — heuristic only.

        The SDK does not surface an explicit "transfer server" list; transfer
        servers appear only in MSGTYPE_SERVER_CONNECT_NTF without any name.
        This method returns every server-connect row that is NOT also present
        in the storage / ai / alarm-host callback buckets and has a non-null
        GUID, annotating them as ``kind="transfer"``.
        """
        typed_guids: set[str] = set()
        for bucket in (
            self._state.ai_servers if self._state else {},
            self._state.storage_servers if self._state else {},
            self._state.alarm_hosts if self._state else {},
        ):
            typed_guids.update(bucket.keys())

        result: list[PlatformServer] = []
        for s in self.list_servers():
            if s.kind != "unknown":
                continue
            result.append(
                PlatformServer(
                    guid=s.guid,
                    name=s.name,
                    kind="transfer",
                    server_type=s.server_type,
                    server_type_name=s.server_type_name,
                    connect_state=s.connect_state,
                    connect_state_name=s.connect_state_name,
                    online=s.online,
                    ip=s.ip,
                    port=s.port,
                    channel_count=s.channel_count,
                    raw_data=s.raw_data,
                )
            )
        return result

    def list_storage_servers(self) -> list[PlatformServer]:
        return [s for s in self.list_servers() if s.kind == "storage"]

    def list_access_servers(self) -> list[PlatformServer]:
        """Access control servers.

        The PlatformSDK does not expose a dedicated access-server callback in
        the 20250115 headers; this is a placeholder returning an empty list
        until TVT publishes the relevant MSGTYPE and struct.
        """
        return []

    def list_alarm_servers(self) -> list[PlatformServer]:
        return [s for s in self.list_servers() if s.kind == "alarm_host"]

    def list_tv_wall_servers(self) -> list[PlatformServer]:
        """TV wall servers.

        Not exposed in the 20250115 callback API; returns ``[]`` until a
        confirmed MSGTYPE is identified.  GUI requests happen through
        ``Plat_RequestKbTvWallOptionEx`` which is an XML RPC, not a callback.
        """
        return []

    def list_intelligent_analysis_servers(self) -> list[PlatformServer]:
        return [s for s in self.list_servers() if s.kind == "ai"]

    # --- Alarm-host sub-entities ----------------------------------------

    def list_alarm_zones(self) -> list[PlatformAlarmZone]:
        if not self._authenticated or self._state is None:
            raise SessionExpired("PlatformSDK session not authenticated.")
        return [
            PlatformAlarmZone(
                guid=z["guid"],
                host_guid=z.get("host_guid", ""),
                name=z.get("name", ""),
                zone_type=int(z.get("zone_type", -1)),
                raw_data=dict(z),
            )
            for z in self._state.alarm_zones.values()
        ]

    # --- Write scaffolding (dry-run only) -------------------------------

    def create_user(self, *, dry_run: bool = True, **payload: Any) -> dict[str, Any]:
        """Scaffold for Plat_CreateUser.  Dry-run only; not yet implemented."""
        if not dry_run:
            raise CapabilityNotAvailable(
                "create_user: live call not yet implemented. "
                "Plat_CreateUser takes a C++ Plat_UserInfo struct; binding "
                "requires validated ctypes layout and a safety review."
            )
        return {"dry_run": True, "operation": "create_user", "payload": payload}

    def create_permission_group(
        self, *, dry_run: bool = True, **payload: Any
    ) -> dict[str, Any]:
        if not dry_run:
            raise CapabilityNotAvailable(
                "create_permission_group: live call not yet implemented. "
                "Plat_CreateAuthourityGroup requires validated struct binding."
            )
        return {"dry_run": True, "operation": "create_permission_group", "payload": payload}

    def create_transfer_server(
        self, *, dry_run: bool = True, **payload: Any
    ) -> dict[str, Any]:
        if not dry_run:
            raise CapabilityNotAvailable(
                "create_transfer_server: PlatformSDK does not expose this via a "
                "documented symbol in the 20250115 headers."
            )
        return {"dry_run": True, "operation": "create_transfer_server", "payload": payload}

    def create_tv_wall(self, *, dry_run: bool = True, **payload: Any) -> dict[str, Any]:
        if not dry_run:
            raise CapabilityNotAvailable(
                "create_tv_wall: TV-wall operations go through "
                "Plat_RequestKbTvWallOptionEx (XML); not yet bound."
            )
        return {"dry_run": True, "operation": "create_tv_wall", "payload": payload}

    def add_device(self, *, dry_run: bool = True, **payload: Any) -> dict[str, Any]:
        if not dry_run:
            raise CapabilityNotAvailable(
                "add_device: PlatformSDK write path for device enrollment is "
                "not yet bound; live-test before enabling."
            )
        return {"dry_run": True, "operation": "add_device", "payload": payload}

    # --- Not-yet-reachable read APIs (documented blockers) --------------

    def list_users(self) -> list[Any]:
        """Return a list of PlatformUser records — NOT YET IMPLEMENTED.

        Blocker: ``Plat_GetAllUserInfo`` takes a ``std::list<Plat_UserInfo>&``
        parameter which is part of the C++ ABI and not callable from ctypes.
        A bridge in C++ (a thin shim library) is required before this can be
        wired up safely.
        """
        raise CapabilityNotAvailable(
            "list_users: Plat_GetAllUserInfo uses std::list<Plat_UserInfo>&; "
            "requires a C++ shim or alternative HTTP endpoint."
        )

    def list_permission_groups(self) -> list[Any]:
        """Return permission groups — NOT YET IMPLEMENTED.

        Blocker: ``Plat_GetAllAuthGroupInfo`` takes ``std::list<Plat_PermisinInfo>&``
        and ``Plat_PermisinInfo`` embeds multiple ``std::map`` fields. Same
        C++-ABI constraint as ``list_users``.  A callback-driven path via
        ``MSGTYPE_AUTHOURITY_GROUP_INFO`` may be possible once the payload
        struct is confirmed.
        """
        raise CapabilityNotAvailable(
            "list_permission_groups: Plat_GetAllAuthGroupInfo requires C++ shim; "
            "or unpacking MSGTYPE_AUTHOURITY_GROUP_INFO payload (not yet validated)."
        )

    def list_logs(
        self,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        main_type: int | None = None,
        subtype: int | None = None,
        limit: int = 500,
    ) -> list[Any]:
        """Return log entries — NOT YET IMPLEMENTED.

        The 20250115 PlatformSDK headers do not export a stable log-query
        symbol (``Plat_QueryPassRecord*`` covers parking only).  Log retrieval
        likely requires a management-server HTTP API call instead.
        """
        raise CapabilityNotAvailable(
            "list_logs: no PlatformSDK symbol for general log query; "
            "requires management-server HTTP endpoint integration."
        )

    def list_alarm_logs(self, **kwargs: Any) -> list[Any]:
        raise CapabilityNotAvailable(
            "list_alarm_logs: not exposed by PlatformSDK 20250115; use HTTP API."
        )

    def list_operation_logs(self, **kwargs: Any) -> list[Any]:
        raise CapabilityNotAvailable(
            "list_operation_logs: not exposed by PlatformSDK 20250115; use HTTP API."
        )

    def list_exception_logs(self, **kwargs: Any) -> list[Any]:
        raise CapabilityNotAvailable(
            "list_exception_logs: not exposed by PlatformSDK 20250115; use HTTP API."
        )

    def list_tv_walls(self) -> list[Any]:
        raise CapabilityNotAvailable(
            "list_tv_walls: TV-wall queries go through Plat_RequestKbTvWallOptionEx "
            "(XML request/response); not yet bound."
        )

    def list_alarm_events(self) -> list[Any]:
        raise CapabilityNotAvailable(
            "list_alarm_events: live alarm stream needs MSGTYPE_* mapping "
            "validation against the server."
        )

    def list_active_alarms(self) -> list[Any]:
        raise CapabilityNotAvailable(
            "list_active_alarms: not yet implemented."
        )

    def subscribe_alarms(self) -> AlarmSubscription:
        raise CapabilityNotAvailable(
            "Alarm subscription via PlatformSDK callback is not yet implemented; "
            "callback payload semantics require live validation."
        )

    def close(self) -> None:
        login_id = self._login_id
        self._login_id = None
        self._authenticated = False

        if login_id is not None:
            try:
                # Use shared call_logout from NamespacedLibrary
                self._ns.call_logout(login_id)
            except Exception:
                logger.exception("Plat_LogOutEx raised for login_id=%d", login_id)

        try:
            self._ns.call_uninit()
        except Exception:
            logger.exception("Plat_UnInitializeEx raised during close")

        self._cb_ref = None
        self._state = None


# ---------------------------------------------------------------------------
# PlatformSdkManagementBackend  — BaseManagementBackend implementation
# ---------------------------------------------------------------------------

class PlatformSdkManagementBackend(BaseManagementBackend):
    """PlatformSDK-backed backend for TVT NVMS management server.

    Uses libPlatClientSDK.so (Linux) or PlatClientSDK.dll (Windows) via ctypes.
    Implements the Plat_* API surface with callback-driven device enumeration.
    """

    def __init__(
        self,
        host: str,
        port: int = 6003,
        sdk_path: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.sdk_path = (sdk_path or "").strip()
        self._ns_lib: NamespacedLibrary | None = None
        self._load_error: str | None = None
        self._client: PlatformSDKClient | None = None
        self._authenticated = False

        if self.sdk_path:
            try:
                raw_lib = _load_library(self.sdk_path)
                self._ns_lib = make_namespaced_library(raw_lib, SdkNamespace.PLAT)
                logger.debug("PlatformSDK loaded from %s", self.sdk_path)
            except _SDKLoadError as exc:
                self._load_error = str(exc)
                logger.warning("PlatformSDK backend unavailable: %s", exc)

    # ------------------------------------------------------------------
    # Capability probes
    # ------------------------------------------------------------------

    def load_sdk(self) -> bool:
        return self._ns_lib is not None

    def supports_sdk(self) -> bool:
        if self._ns_lib is None:
            return False
        return self._ns_lib.login_path_ready() and self._ns_lib.has_capability("set_message_callback")

    def supports_native_protocol(self) -> bool:
        return False

    def diagnostics(self) -> dict[str, Any]:
        os_family = _normalize_os_family()
        sdk_machine = _detect_sdk_machine(self.sdk_path) if self.sdk_path else None
        runtime_machine = platform.machine() or None
        arch_compatible: bool | None = None
        if runtime_machine and sdk_machine:
            arch_compatible = runtime_machine == sdk_machine

        ns_diag = self._ns_lib.as_dict() if self._ns_lib else {}
        login_ready = self.supports_sdk()
        return {
            "backend": "platform_sdk",
            "sdk_family": "management_sdk",
            "sdk_path": self.sdk_path,
            "load_success": self._ns_lib is not None,
            "load_error": self._load_error,
            "os_family": os_family,
            "sdk_machine": sdk_machine,
            "runtime_machine": runtime_machine,
            "architecture_compatible": arch_compatible,
            "login_path_ready": login_ready,
            "plat_symbol_presence": ns_diag.get("capabilities", {}),
            "authenticated": self._authenticated,
        }

    def get_context(self) -> SDKContext:
        os_family = _normalize_os_family()
        sdk_machine = _detect_sdk_machine(self.sdk_path) if self.sdk_path else None
        login_ready = self.supports_sdk()
        capabilities = CapabilityMap(
            supports_init=login_ready,
            supports_login=login_ready,
            supports_login_ex=login_ready,
            supports_logout=login_ready,
            supports_device_enumeration=login_ready,
            supports_alarm_subscription=False,
            supports_management_server_login=login_ready,
        )
        sdk_name = Path(self.sdk_path).name if self.sdk_path else None
        notes = [
            f"PlatformSDK backend: namespace={SdkNamespace.PLAT.value}",
            "Device list arrives via MSGTYPE_RESLIST_NTF async callback.",
        ]
        if self._ns_lib:
            notes.append(f"Capabilities: {self._ns_lib.probe_namespace_capabilities()}")
        return SDKContext(
            platform=PlatformIdentity(
                os_family=os_family,
                arch=sdk_machine or platform.machine() or None,
                runtime_kind="native",
            ),
            sdk=SDKIdentity(
                vendor="tvt",
                sdk_name=sdk_name,
                sdk_family="management_sdk",
                sdk_version="20250115",
            ),
            product_scope={"management_server"},
            capabilities=capabilities,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Internal guard
    # ------------------------------------------------------------------

    def _require_lib(self) -> NamespacedLibrary:
        os_family = _normalize_os_family()
        if os_family not in ("linux", "windows"):
            raise UnsupportedOnPlatformError(
                f"PlatformSDK backend requires Linux or Windows; current platform is {os_family}."
            )
        if self._ns_lib is None:
            reason = self._load_error or "No SDK path configured"
            raise CapabilityNotAvailable(f"PlatformSDK is not available: {reason}")
        if not self.supports_sdk():
            raise CapabilityNotAvailable(
                "PlatformSDK loaded but required Plat_* symbols are missing."
            )
        return self._ns_lib

    def _require_session(self) -> PlatformSDKClient:
        if self._client is None or not self._authenticated:
            raise ManagementNotAuthenticatedError(
                "PlatformSdkManagementBackend: not authenticated. Call login() first."
            )
        return self._client

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def login(self, username: str, password: str, device_id: str | None = None) -> bool:
        ns_lib = self._require_lib()
        client = PlatformSDKClient(ns_lib, self.host, self.port)
        try:
            client.login(username, password)
        except (ManagementAuthError, TransportError, CapabilityNotAvailable):
            client.close()
            raise
        self._client = client
        self._authenticated = True
        return True

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.exception("PlatformSdkManagementBackend close() raised")
            finally:
                self._client = None
                self._authenticated = False

    # ------------------------------------------------------------------
    # Management operations
    # ------------------------------------------------------------------

    def get_server_info(self) -> ServerInfo:
        return self._require_session().get_server_info()

    def list_devices(self) -> list[ManagedDevice]:
        return self._require_session().list_devices()

    def list_channels(self) -> list[ManagedChannel]:
        return self._require_session().list_channels()

    def get_device_statuses(self) -> list[DeviceStatus]:
        return self._require_session().get_device_statuses()

    def subscribe_alarms(self) -> AlarmSubscription:
        return self._require_session().subscribe_alarms()

    def list_resources(self) -> list[dict[str, Any]]:
        return self._require_session().list_resources()

    def list_server_connection_events(self) -> list[dict[str, Any]]:
        return self._require_session().list_server_connection_events()

    def list_transfer_servers(self) -> list[PlatformServer]:
        return self._require_session().list_transfer_servers()

    # -- Normalized resource views --
    def list_resources_normalized(self) -> list[PlatformResource]:
        return self._require_session().list_resources_normalized()

    def list_areas(self) -> list[PlatformResource]:
        return self._require_session().list_areas()

    def list_devices_normalized(self) -> list[PlatformResource]:
        return self._require_session().list_devices_normalized()

    def list_channels_normalized(self) -> list[PlatformResource]:
        return self._require_session().list_channels_normalized()

    def list_resources_tree(self) -> list[dict[str, Any]]:
        return self._require_session().list_resources_tree()

    def find_resource_by_guid(self, guid: str) -> PlatformResource | None:
        return self._require_session().find_resource_by_guid(guid)

    def find_resource_by_name(self, name: str) -> list[PlatformResource]:
        return self._require_session().find_resource_by_name(name)

    # -- Server enumeration --
    def list_servers(self) -> list[PlatformServer]:
        return self._require_session().list_servers()

    def list_storage_servers(self) -> list[PlatformServer]:
        return self._require_session().list_storage_servers()

    def list_access_servers(self) -> list[PlatformServer]:
        return self._require_session().list_access_servers()

    def list_alarm_servers(self) -> list[PlatformServer]:
        return self._require_session().list_alarm_servers()

    def list_tv_wall_servers(self) -> list[PlatformServer]:
        return self._require_session().list_tv_wall_servers()

    def list_intelligent_analysis_servers(self) -> list[PlatformServer]:
        return self._require_session().list_intelligent_analysis_servers()

    def list_alarm_zones(self) -> list[PlatformAlarmZone]:
        return self._require_session().list_alarm_zones()

    # -- Write scaffolds (dry_run by default) --
    def create_user(self, *, dry_run: bool = True, **payload: Any) -> dict[str, Any]:
        return self._require_session().create_user(dry_run=dry_run, **payload)

    def create_permission_group(self, *, dry_run: bool = True, **payload: Any) -> dict[str, Any]:
        return self._require_session().create_permission_group(dry_run=dry_run, **payload)

    def create_transfer_server(self, *, dry_run: bool = True, **payload: Any) -> dict[str, Any]:
        return self._require_session().create_transfer_server(dry_run=dry_run, **payload)

    def create_tv_wall(self, *, dry_run: bool = True, **payload: Any) -> dict[str, Any]:
        return self._require_session().create_tv_wall(dry_run=dry_run, **payload)

    def add_device(self, *, dry_run: bool = True, **payload: Any) -> dict[str, Any]:
        return self._require_session().add_device(dry_run=dry_run, **payload)

    # -- Not-yet-reachable read APIs (explicitly raise) --
    def list_users(self) -> list[Any]:
        return self._require_session().list_users()

    def list_permission_groups(self) -> list[Any]:
        return self._require_session().list_permission_groups()

    def list_logs(self, **kwargs: Any) -> list[Any]:
        return self._require_session().list_logs(**kwargs)

    def list_alarm_logs(self, **kwargs: Any) -> list[Any]:
        return self._require_session().list_alarm_logs(**kwargs)

    def list_operation_logs(self, **kwargs: Any) -> list[Any]:
        return self._require_session().list_operation_logs(**kwargs)

    def list_exception_logs(self, **kwargs: Any) -> list[Any]:
        return self._require_session().list_exception_logs(**kwargs)

    def list_tv_walls(self) -> list[Any]:
        return self._require_session().list_tv_walls()

    def list_alarm_events(self) -> list[Any]:
        return self._require_session().list_alarm_events()

    def list_active_alarms(self) -> list[Any]:
        return self._require_session().list_active_alarms()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "PlatformSdkManagementBackend":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
