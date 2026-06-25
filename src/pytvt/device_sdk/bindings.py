"""Low-level ctypes function declarations for libdvrnetsdk.so (Linux) and libNetClientSDK.dylib (macOS).

Each function is declared lazily via :func:`bind` so you can import this
module on any platform without triggering a load failure.  The actual
``ct.CDLL`` is passed in by :class:`~pytvt.device_sdk.client.NetSdkClient`.

Usage (internal)::

    from pytvt.device_sdk import bindings as sdk
    lib = load_sdk()
    sdk.bind(lib)
    sdk.NET_SDK_Init()
"""

from __future__ import annotations

import ctypes as ct

from . import types as t

# Shared library handle — set by bind()
_lib: ct.CDLL | None = None
# macOS exports C++ symbols with one leading underscore in dlsym/ctypes lookups.
# `nm` output includes an additional underscore prefix and should not be copied verbatim.
NET_CLIENT_REQUEST_MODIFY_DEVICE_IP = "_Z32NET_CLIENT_RequestModifyDeviceIpjPKcjRP11CBufferData"
NET_CLIENT_REQUEST_MODIFY_DEVICE_IP_OBSERVER = "_Z32NET_CLIENT_RequestModifyDeviceIpjPKcjPvP13CWaitObserver"


def bind(lib: ct.CDLL) -> None:
    """Bind all function prototypes to the loaded library."""
    global _lib
    _lib = lib

    # The pure NetSdkClient runtime expects NET_SDK_* signatures.
    # macOS MonitorClient exports NET_CLIENT_* with incompatible signatures,
    # so fail explicitly instead of binding wrong prototypes.
    if not hasattr(lib, "NET_SDK_Init") and hasattr(lib, "NET_CLIENT_Initial"):
        raise RuntimeError(
            "Loaded macOS NET_CLIENT_* SDK namespace. "
            "NetSdkClient currently supports NET_SDK_* runtime calls only. "
            "Use NVR HTTP API flow for IP updates, or a dedicated NET_CLIENT adapter."
        )

    # ── Init / cleanup ──────────────────────────────────────────
    lib.NET_SDK_Init.restype = ct.c_bool
    lib.NET_SDK_Init.argtypes = []

    lib.NET_SDK_Cleanup.restype = ct.c_bool
    lib.NET_SDK_Cleanup.argtypes = []

    lib.NET_SDK_SetConnectTime.restype = ct.c_bool
    lib.NET_SDK_SetConnectTime.argtypes = [ct.c_uint, ct.c_uint]

    lib.NET_SDK_SetReconnect.restype = ct.c_bool
    lib.NET_SDK_SetReconnect.argtypes = [ct.c_uint, ct.c_bool]

    # ── Generic device web-CGI over the authenticated SDK transport ──
    # Runs a device's web API command (NVR queryPlatformCfg/editPlatformCfg, or a
    # camera's ipc.com Get/Set command) through the SDK session — works LAN-direct
    # and NAT-tunneled. Optional symbol on some .so variants.
    if hasattr(lib, "NET_SDK_ApiInterface"):
        lib.NET_SDK_ApiInterface.restype = ct.c_bool
        lib.NET_SDK_ApiInterface.argtypes = [
            ct.c_long,              # login handle
            ct.c_char_p,            # request body (XML)
            ct.c_char_p,            # CGI url / command
            ct.c_void_p,            # output buffer
            ct.c_uint,              # buffer size
            ct.POINTER(ct.c_uint),  # out: bytes written
        ]

    # ── Login / logout ──────────────────────────────────────────
    lib.NET_SDK_Login.restype = ct.c_long
    lib.NET_SDK_Login.argtypes = [
        ct.c_char_p,  # sDVRIP
        ct.c_ushort,  # wDVRPort
        ct.c_char_p,  # sUserName
        ct.c_char_p,  # sPassword
        ct.POINTER(t.NET_SDK_DEVICEINFO),  # lpDeviceInfo
    ]

    lib.NET_SDK_LoginEx.restype = ct.c_long
    lib.NET_SDK_LoginEx.argtypes = [
        ct.c_char_p,  # sDVRIP / NAT server address
        ct.c_ushort,  # wDVRPort / NAT server port
        ct.c_char_p,  # sUserName
        ct.c_char_p,  # sPassword
        ct.POINTER(t.NET_SDK_DEVICEINFO),  # lpDeviceInfo
        ct.c_int,  # NET_SDK_CONNECT_TYPE
        ct.c_char_p,  # sDevSN / UID
    ]

    lib.NET_SDK_SetNat2Addr.restype = ct.c_bool
    lib.NET_SDK_SetNat2Addr.argtypes = [
        ct.c_char_p,  # sServerAddr
        ct.c_ushort,  # wDVRPort
    ]

    lib.NET_SDK_Logout.restype = ct.c_bool
    lib.NET_SDK_Logout.argtypes = [ct.c_long]

    # ── Discovery ───────────────────────────────────────────────
    lib.NET_SDK_DiscoverDevice.restype = ct.c_int
    lib.NET_SDK_DiscoverDevice.argtypes = [
        ct.POINTER(t.NET_SDK_DEVICE_DISCOVERY_INFO),
        ct.c_int,
        ct.c_int,
    ]

    # ── Device activation ───────────────────────────────────────
    lib.NET_SDK_ActiveDevice.restype = ct.c_bool
    lib.NET_SDK_ActiveDevice.argtypes = [ct.c_char_p, ct.c_int, ct.c_char_p]

    lib.NET_SDK_ActiveDeviceByMac.restype = ct.c_bool
    lib.NET_SDK_ActiveDeviceByMac.argtypes = [ct.c_char_p, ct.c_char_p]

    if hasattr(lib, "NET_SDK_SetDeviceIP"):
        lib.NET_SDK_SetDeviceIP.restype = ct.c_bool
        lib.NET_SDK_SetDeviceIP.argtypes = [
            ct.c_char_p,
            ct.c_char_p,
            ct.c_char_p,
            ct.c_char_p,
            ct.c_char_p,
            ct.c_char_p,
            ct.c_char_p,
        ]

    if hasattr(lib, "NET_SDK_ModifyDeviceNetInfo"):
        lib.NET_SDK_ModifyDeviceNetInfo.restype = ct.c_bool
        lib.NET_SDK_ModifyDeviceNetInfo.argtypes = [ct.POINTER(t.NET_SDK_DEVICE_IP_INFO)]

    # ── Device info ─────────────────────────────────────────────
    lib.NET_SDK_GetDeviceInfo.restype = ct.c_bool
    lib.NET_SDK_GetDeviceInfo.argtypes = [
        ct.c_long,
        ct.POINTER(t.NET_SDK_DEVICEINFO),
    ]

    lib.NET_SDK_GetDeviceTime.restype = ct.c_bool
    lib.NET_SDK_GetDeviceTime.argtypes = [ct.c_long, ct.POINTER(t.DD_TIME)]

    lib.NET_SDK_GetDeviceIPCInfo.restype = ct.c_bool
    lib.NET_SDK_GetDeviceIPCInfo.argtypes = [
        ct.c_long,
        ct.POINTER(t.NET_SDK_IPC_DEVICE_INFO),
        ct.c_long,
        ct.POINTER(ct.c_long),
    ]

    lib.NET_SDK_GetDeviceCHStatus.restype = ct.c_bool
    lib.NET_SDK_GetDeviceCHStatus.argtypes = [
        ct.c_long,
        ct.POINTER(t.NET_SDK_CH_DEVICE_STATUS),
        ct.c_long,
        ct.POINTER(ct.c_long),
    ]

    lib.NET_SDK_GetDeviceSupportFunction.restype = ct.c_bool
    lib.NET_SDK_GetDeviceSupportFunction.argtypes = [
        ct.c_long,
        ct.POINTER(t.NET_SDK_DEV_SUPPORT),
    ]

    lib.NET_SDK_GetSmarEventSupport.restype = ct.c_bool
    lib.NET_SDK_GetSmarEventSupport.argtypes = [
        ct.c_long,
        ct.c_long,
        ct.POINTER(t.NET_SDK_SMART_SUPPORT),
    ]

    # ── RTSP URL ────────────────────────────────────────────────
    lib.NET_SDK_GetRtspUrl.restype = ct.c_bool
    lib.NET_SDK_GetRtspUrl.argtypes = [
        ct.c_long,  # lUserID
        ct.c_long,  # lChannel
        ct.c_long,  # lStreamType
        ct.c_char_p,  # sRtspUrl (out buffer)
    ]

    # ── JPEG capture ────────────────────────────────────────────
    lib.NET_SDK_CaptureJPEGData_V2.restype = ct.c_bool
    lib.NET_SDK_CaptureJPEGData_V2.argtypes = [
        ct.c_long,  # lUserID
        ct.c_long,  # lChannel
        ct.POINTER(t.NET_SDK_JPEGPARA),  # lpJpegPara
        ct.c_char_p,  # sJpegPicBuffer
        ct.c_uint,  # dwPicSize (buf len)
        ct.POINTER(ct.c_uint),  # lpSizeReturned
    ]

    # ── PTZ ─────────────────────────────────────────────────────
    lib.NET_SDK_PTZControl_Other.restype = ct.c_bool
    lib.NET_SDK_PTZControl_Other.argtypes = [
        ct.c_long,  # lUserID
        ct.c_long,  # lChannel
        ct.c_uint,  # dwPTZCommand
        ct.c_uint,  # dwSpeed
    ]

    lib.NET_SDK_PTZPreset_Other.restype = ct.c_bool
    lib.NET_SDK_PTZPreset_Other.argtypes = [
        ct.c_long,  # lUserID
        ct.c_long,  # lChannel
        ct.c_uint,  # dwPTZPresetCmd
        ct.c_uint,  # dwPresetIndex
    ]

    lib.NET_SDK_PTZCruise_Other.restype = ct.c_bool
    lib.NET_SDK_PTZCruise_Other.argtypes = [
        ct.c_long,
        ct.c_long,
        ct.c_uint,
        ct.c_ubyte,
    ]

    # ── Alarms ──────────────────────────────────────────────────
    lib.NET_SDK_SetupAlarmChan.restype = ct.c_long
    lib.NET_SDK_SetupAlarmChan.argtypes = [ct.c_long]

    lib.NET_SDK_CloseAlarmChan.restype = ct.c_bool
    lib.NET_SDK_CloseAlarmChan.argtypes = [ct.c_long]

    lib.NET_SDK_GetAlarmOutStatus.restype = ct.c_bool
    lib.NET_SDK_GetAlarmOutStatus.argtypes = [
        ct.c_long,
        ct.POINTER(t.NET_SDK_ALRAM_OUT_STATUS),
        ct.c_long,
        ct.POINTER(ct.c_long),
    ]

    # ── Recording search ────────────────────────────────────────
    lib.NET_SDK_FindFile.restype = ct.c_longlong
    lib.NET_SDK_FindFile.argtypes = [
        ct.c_long,
        ct.c_long,
        ct.c_uint,
        ct.POINTER(t.DD_TIME),
        ct.POINTER(t.DD_TIME),
    ]

    lib.NET_SDK_FindNextFile.restype = ct.c_long
    lib.NET_SDK_FindNextFile.argtypes = [
        ct.c_longlong,
        ct.POINTER(t.NET_SDK_REC_FILE),
    ]

    lib.NET_SDK_FindClose.restype = ct.c_bool
    lib.NET_SDK_FindClose.argtypes = [ct.c_longlong]

    # ── Recording control ───────────────────────────────────────
    lib.NET_SDK_StartDVRRecord.restype = ct.c_bool
    lib.NET_SDK_StartDVRRecord.argtypes = [ct.c_long, ct.c_long, ct.c_long]

    lib.NET_SDK_StopDVRRecord.restype = ct.c_bool
    lib.NET_SDK_StopDVRRecord.argtypes = [ct.c_long, ct.c_long]

    # ── Disk management ─────────────────────────────────────────
    lib.NET_SDK_FindDisk.restype = ct.c_longlong
    lib.NET_SDK_FindDisk.argtypes = [ct.c_long]

    lib.NET_SDK_GetNextDiskInfo.restype = ct.c_bool
    lib.NET_SDK_GetNextDiskInfo.argtypes = [
        ct.c_longlong,
        ct.POINTER(t.NET_SDK_DISK_INFO),
    ]

    lib.NET_SDK_FindDiskClose.restype = ct.c_bool
    lib.NET_SDK_FindDiskClose.argtypes = [ct.c_longlong]

    lib.NET_SDK_GetNvrRecordDays.restype = ct.c_bool
    lib.NET_SDK_GetNvrRecordDays.argtypes = [
        ct.c_long,
        ct.POINTER(t.NET_SDK_NVR_DISKREC_DATE_ITEM),
        ct.c_long,
        ct.POINTER(ct.c_long),
    ]

    # ── Firmware upgrade ────────────────────────────────────────
    lib.NET_SDK_Upgrade.restype = ct.c_longlong
    lib.NET_SDK_Upgrade.argtypes = [ct.c_long, ct.c_char_p]

    lib.NET_SDK_GetUpgradeProgress.restype = ct.c_int
    lib.NET_SDK_GetUpgradeProgress.argtypes = [
        ct.c_longlong,
        ct.POINTER(ct.c_int),
    ]

    lib.NET_SDK_CloseUpgradeHandle.restype = ct.c_bool
    lib.NET_SDK_CloseUpgradeHandle.argtypes = [ct.c_longlong]

    # ── Device reboot / shutdown ────────────────────────────────
    lib.NET_SDK_RebootDVR.restype = ct.c_bool
    lib.NET_SDK_RebootDVR.argtypes = [ct.c_long]

    lib.NET_SDK_ShutDownDVR.restype = ct.c_bool
    lib.NET_SDK_ShutDownDVR.argtypes = [ct.c_long]

    # ── Time sync ───────────────────────────────────────────────
    lib.NET_SDK_ChangTime.restype = ct.c_bool
    lib.NET_SDK_ChangTime.argtypes = [ct.c_long, ct.c_uint]

    # ── Config backup / restore ─────────────────────────────────
    lib.NET_SDK_RestoreConfig.restype = ct.c_bool
    lib.NET_SDK_RestoreConfig.argtypes = [ct.c_long]

    lib.NET_SDK_GetConfigFile.restype = ct.c_bool
    lib.NET_SDK_GetConfigFile.argtypes = [ct.c_long, ct.c_char_p]

    lib.NET_SDK_SetConfigFile.restype = ct.c_bool
    lib.NET_SDK_SetConfigFile.argtypes = [ct.c_long, ct.c_char_p]

    # ── Log search ──────────────────────────────────────────────
    lib.NET_SDK_FindDVRLog.restype = ct.c_longlong
    lib.NET_SDK_FindDVRLog.argtypes = [
        ct.c_long,
        ct.c_uint,
        ct.POINTER(t.DD_TIME),
        ct.POINTER(t.DD_TIME),
    ]

    lib.NET_SDK_FindNextLog.restype = ct.c_long
    lib.NET_SDK_FindNextLog.argtypes = [
        ct.c_longlong,
        ct.POINTER(t.NET_SDK_LOG),
    ]

    lib.NET_SDK_FindLogClose.restype = ct.c_bool
    lib.NET_SDK_FindLogClose.argtypes = [ct.c_longlong]

    # ── Error ───────────────────────────────────────────────────
    lib.NET_SDK_GetLastError.restype = ct.c_uint
    lib.NET_SDK_GetLastError.argtypes = []

    # ── SDK version ─────────────────────────────────────────────
    lib.NET_SDK_GetSDKVersion.restype = ct.c_uint
    lib.NET_SDK_GetSDKVersion.argtypes = []

    lib.NET_SDK_GetSDKBuildVersion.restype = ct.c_uint
    lib.NET_SDK_GetSDKBuildVersion.argtypes = []

    # ── Logging ─────────────────────────────────────────────────
    lib.NET_SDK_SetLogToFile.restype = ct.c_bool
    lib.NET_SDK_SetLogToFile.argtypes = [
        ct.c_bool,  # bLogEnable
        ct.c_char_p,  # strLogDir
        ct.c_bool,  # bAutoDel
        ct.c_int,  # logLevel
    ]

    # ── Access control ──────────────────────────────────────────
    lib.NET_SDK_UnlockAccessControl.restype = ct.c_bool
    lib.NET_SDK_UnlockAccessControl.argtypes = [ct.c_long, ct.c_long]

    # NET_CLIENT_* compatibility is intentionally not auto-aliased here because
    # call signatures differ from NET_SDK_* and can crash the process.


def _create_net_client_compatibility_layer(lib: ct.CDLL) -> None:
    """Deprecated placeholder for NET_CLIENT support.

    NET_CLIENT_* to NET_SDK_* aliasing is unsafe because signatures differ.
    Keep this symbol to avoid import breakage in downstream callers.
    """
    return None


def bind_device_ip_modify(lib: ct.CDLL) -> str:
    """Bind only the validated native IP modification symbol and return its name."""
    if hasattr(lib, "NET_SDK_SetDeviceIP"):
        lib.NET_SDK_SetDeviceIP.restype = ct.c_bool
        lib.NET_SDK_SetDeviceIP.argtypes = [
            ct.c_char_p,
            ct.c_char_p,
            ct.c_char_p,
            ct.c_char_p,
            ct.c_char_p,
            ct.c_char_p,
            ct.c_char_p,
        ]
        return "NET_SDK_SetDeviceIP"
    if hasattr(lib, "NET_SDK_ModifyDeviceNetInfo"):
        lib.NET_SDK_ModifyDeviceNetInfo.restype = ct.c_bool
        lib.NET_SDK_ModifyDeviceNetInfo.argtypes = [ct.POINTER(t.NET_SDK_DEVICE_IP_INFO)]
        return "NET_SDK_ModifyDeviceNetInfo"
    if hasattr(lib, NET_CLIENT_REQUEST_MODIFY_DEVICE_IP):
        func = getattr(lib, NET_CLIENT_REQUEST_MODIFY_DEVICE_IP)
        func.restype = ct.c_bool
        func.argtypes = [ct.c_uint, ct.c_char_p, ct.c_uint, ct.POINTER(ct.c_void_p)]
        return "NET_CLIENT_RequestModifyDeviceIp"
    if hasattr(lib, NET_CLIENT_REQUEST_MODIFY_DEVICE_IP_OBSERVER):
        func = getattr(lib, NET_CLIENT_REQUEST_MODIFY_DEVICE_IP_OBSERVER)
        func.restype = ct.c_bool
        func.argtypes = [ct.c_uint, ct.c_char_p, ct.c_uint, ct.c_void_p, ct.c_void_p]
        return "NET_CLIENT_RequestModifyDeviceIp"
    raise RuntimeError(
        "Loaded TVT SDK does not export NET_SDK_SetDeviceIP, "
        "NET_SDK_ModifyDeviceNetInfo, or NET_CLIENT_RequestModifyDeviceIp."
    )
