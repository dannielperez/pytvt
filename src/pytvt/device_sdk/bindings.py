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
            ct.c_long,  # login handle
            ct.c_char_p,  # request body (XML)
            ct.c_char_p,  # CGI url / command
            ct.c_void_p,  # output buffer
            ct.c_uint,  # buffer size
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

    # ── NetSDK 1.3.2 additions ──────────────────────────────────
    # All optional: older libdvrnetsdk.so drops (<1.3.2) do not export them, so
    # each is bound only when present. Wrappers that call them raise
    # NetSdkCapabilityError on libraries that lack the symbol.
    _bind_v132(lib)

    # NET_CLIENT_* compatibility is intentionally not auto-aliased here because
    # call signatures differ from NET_SDK_* and can crash the process.


def _bind_v132(lib: ct.CDLL) -> None:
    """Bind function prototypes new in the TVT NetSDK 1.3.2 device drop.

    Every symbol is guarded with ``hasattr`` so :func:`bind` stays compatible
    with older SDK binaries that predate these calls.
    """
    # ── Access control (door / gate) ────────────────────────────
    if hasattr(lib, "NET_SDK_UnlockAccessControlEx"):
        # UNLOCK_PARAM is passed by value.
        lib.NET_SDK_UnlockAccessControlEx.restype = ct.c_bool
        lib.NET_SDK_UnlockAccessControlEx.argtypes = [ct.c_long, ct.c_long, t.UNLOCK_PARAM]

    if hasattr(lib, "NET_SDK_RollingGateControl"):
        # ROLLING_GATE_EXECUTE (unsigned int enum) passed by value.
        lib.NET_SDK_RollingGateControl.restype = ct.c_bool
        lib.NET_SDK_RollingGateControl.argtypes = [ct.c_long, ct.c_uint]

    if hasattr(lib, "NET_SDK_GetCallLog"):
        # queryParam / num / totalNum are C++ references -> pointers on the ABI.
        lib.NET_SDK_GetCallLog.restype = ct.c_bool
        lib.NET_SDK_GetCallLog.argtypes = [
            ct.c_long,  # lUserID
            ct.POINTER(t.CALL_RECORD_QUERY_PARAM),  # const queryParam&
            ct.POINTER(t.CALL_RECORD),  # pRecord (out array)
            ct.c_uint,  # maxNum
            ct.POINTER(ct.c_uint),  # num& (out)
            ct.POINTER(ct.c_uint),  # totalNum& (out)
        ]

    # ── User accounts ───────────────────────────────────────────
    if hasattr(lib, "NET_SDK_GetDeviceUsers"):
        lib.NET_SDK_GetDeviceUsers.restype = ct.c_bool
        lib.NET_SDK_GetDeviceUsers.argtypes = [
            ct.c_long,
            ct.POINTER(t.NET_SDK_USER_INFO),
            ct.POINTER(ct.c_long),  # pUserCount (in: capacity, out: count)
        ]

    if hasattr(lib, "NET_SDK_ModifyIntegrateUser"):
        lib.NET_SDK_ModifyIntegrateUser.restype = ct.c_bool
        lib.NET_SDK_ModifyIntegrateUser.argtypes = [ct.c_long, ct.c_char_p, ct.c_char_p]

    # ── NVR channel enumeration ─────────────────────────────────
    if hasattr(lib, "NET_SDK_GetNvrChlInfo"):
        lib.NET_SDK_GetNvrChlInfo.restype = ct.c_bool
        lib.NET_SDK_GetNvrChlInfo.argtypes = [
            ct.c_long,
            ct.c_char_p,  # chlId (GUID string)
            ct.POINTER(t.NVRChlInfoStruct),
        ]

    if hasattr(lib, "NET_SDK_QueryOnlineChlList"):
        lib.NET_SDK_QueryOnlineChlList.restype = ct.c_bool
        lib.NET_SDK_QueryOnlineChlList.argtypes = [
            ct.c_long,
            ct.POINTER(t.NVRChlListStruct),
            ct.POINTER(ct.c_int),  # outSize
        ]

    # ── Recording status / device ───────────────────────────────
    if hasattr(lib, "NET_SDK_GetRecordStatus"):
        lib.NET_SDK_GetRecordStatus.restype = ct.c_long  # count, <0 error
        lib.NET_SDK_GetRecordStatus.argtypes = [
            ct.c_long,
            ct.POINTER(t.NET_SDK_RECORD_STATUS),
            ct.c_long,  # maxNum
        ]

    if hasattr(lib, "NET_SDK_GetRecordStatusEx"):
        lib.NET_SDK_GetRecordStatusEx.restype = ct.c_long  # count, <0 error
        lib.NET_SDK_GetRecordStatusEx.argtypes = [
            ct.c_long,
            ct.POINTER(t.NET_SDK_RECORD_STATUS_EX),
            ct.c_long,  # maxNum
        ]

    if hasattr(lib, "NET_SDK_GetRecordDevice"):
        lib.NET_SDK_GetRecordDevice.restype = ct.c_uint  # count
        lib.NET_SDK_GetRecordDevice.argtypes = [
            ct.c_long,
            ct.POINTER(t.NET_SDK_RECORD_DEVICE),
            ct.c_uint,  # maxNum
        ]

    if hasattr(lib, "NET_SDK_GetPlayBackSyncHandle"):
        lib.NET_SDK_GetPlayBackSyncHandle.restype = ct.c_longlong  # POINTERHANDLE
        lib.NET_SDK_GetPlayBackSyncHandle.argtypes = [ct.c_long, ct.c_long]

    # ── Thermal snapshot ────────────────────────────────────────
    if hasattr(lib, "NET_SDK_CaptureThermalJpeg"):
        lib.NET_SDK_CaptureThermalJpeg.restype = ct.c_bool
        lib.NET_SDK_CaptureThermalJpeg.argtypes = [
            ct.c_long,  # lUserID
            ct.c_long,  # lChannel
            ct.c_long,  # dwResolution
            ct.c_char_p,  # sJpegPicBuffer
            ct.c_uint,  # dwPicBufSize
            ct.POINTER(ct.c_uint),  # lpSizeReturned
        ]

    # ── Cloud upgrade ───────────────────────────────────────────
    if hasattr(lib, "NET_SDK_CloudUpgrade"):
        lib.NET_SDK_CloudUpgrade.restype = ct.c_bool
        lib.NET_SDK_CloudUpgrade.argtypes = [ct.c_long, ct.c_char_p]

    if hasattr(lib, "NET_SDK_CloudUpgradeNode"):
        lib.NET_SDK_CloudUpgradeNode.restype = ct.c_bool
        lib.NET_SDK_CloudUpgradeNode.argtypes = [ct.c_long, ct.c_long, ct.c_char_p]

    if hasattr(lib, "NET_SDK_GetCloudUpgradeInfo"):
        lib.NET_SDK_GetCloudUpgradeInfo.restype = ct.c_bool
        lib.NET_SDK_GetCloudUpgradeInfo.argtypes = [
            ct.c_long,
            ct.POINTER(t.CLOUD_UPGRADE_INFO),
            ct.c_long,  # lBuffSize
            ct.POINTER(ct.c_long),  # pCuiCount
        ]

    # ── Smart-event config ──────────────────────────────────────
    if hasattr(lib, "NET_SDK_GetSmartEventConfig"):
        lib.NET_SDK_GetSmartEventConfig.restype = ct.c_bool
        lib.NET_SDK_GetSmartEventConfig.argtypes = [
            ct.c_long,  # lUserID
            ct.c_uint,  # dwCommand
            ct.c_long,  # lChannel
            ct.c_void_p,  # lpOutBuffer
            ct.c_uint,  # dwOutBufferSize
            ct.POINTER(ct.c_uint),  # lpBytesReturned
        ]

    if hasattr(lib, "NET_SDK_EditSmartEventConfig"):
        lib.NET_SDK_EditSmartEventConfig.restype = ct.c_bool
        lib.NET_SDK_EditSmartEventConfig.argtypes = [
            ct.c_long,  # lUserID
            ct.c_uint,  # dwCommand
            ct.c_long,  # lChannel
            ct.c_void_p,  # lpInBuffer
            ct.c_uint,  # dwInBufferSize
        ]

    if hasattr(lib, "NET_SDK_EditSmartEventPoint"):
        # size_t is 8 bytes on the 64-bit target; TripwireDirection is an int enum.
        lib.NET_SDK_EditSmartEventPoint.restype = ct.c_bool
        lib.NET_SDK_EditSmartEventPoint.argtypes = [
            ct.c_long,  # lUserID
            ct.c_uint,  # dwCommand
            ct.c_long,  # lChannel
            ct.POINTER(t.NET_DVR_IVE_POINT_T),  # const points
            ct.c_size_t,  # pCounts
            ct.c_int,  # TripwireDirection
        ]

    # ── On-screen rule overlay (needs a live/playback handle) ────
    if hasattr(lib, "NET_SDK_ShowRule"):
        lib.NET_SDK_ShowRule.restype = ct.c_bool
        lib.NET_SDK_ShowRule.argtypes = [
            ct.c_longlong,  # POINTERHANDLE lPlayHandle
            ct.c_long,  # lUserID
            ct.c_long,  # lChannel
            ct.c_bool,  # bShow
        ]

    if hasattr(lib, "NET_SDK_ShowRuleBoxList"):
        # RULE_POINT_LIST passed by value.
        lib.NET_SDK_ShowRuleBoxList.restype = ct.c_bool
        lib.NET_SDK_ShowRuleBoxList.argtypes = [ct.c_longlong, t.RULE_POINT_LIST]

    # ── Two-way audio / subscription callbacks ──────────────────
    if hasattr(lib, "NET_SDK_StartVoiceComTalk"):
        lib.NET_SDK_StartVoiceComTalk.restype = ct.c_longlong  # POINTERHANDLE
        lib.NET_SDK_StartVoiceComTalk.argtypes = [
            ct.c_long,  # lUserID
            ct.c_bool,  # bNeedCBNoEncData
            t.TALK_DATA_CALLBACK,  # fVoiceDataCallBack
            ct.c_void_p,  # pUser
            ct.c_long,  # lChannel
        ]

    if hasattr(lib, "NET_SDK_SetSubscribCallBack_V2"):
        lib.NET_SDK_SetSubscribCallBack_V2.restype = ct.c_bool
        lib.NET_SDK_SetSubscribCallBack_V2.argtypes = [t.SUBSCRIBE_CALLBACK_V2, ct.c_void_p]


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
