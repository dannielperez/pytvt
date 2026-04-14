# NAT Capability Notes

These findings were taken from the vendor Linux SDK attached during implementation work and are the basis for pytvt's first AutoNAT integration.

## Public Header Surface

Header inspected:
- `SDK/C++ Linux/NetSdk.cpp.linux.1.3.2.202601161500/include/DVR_NET_SDK.h`

Relevant declarations:
- `NET_SDK_Login(char* sDVRIP, WORD wDVRPort, char* sUserName, char* sPassword, LPNET_SDK_DEVICEINFO lpDeviceInfo)`
- `NET_SDK_LoginEx(char* sDVRIP, WORD wDVRPort, char* sUserName, char* sPassword, LPNET_SDK_DEVICEINFO lpDeviceInfo, NET_SDK_CONNECT_TYPE eConnectType, const char* sDevSN = NULL)`
- `NET_SDK_SetNat2Addr(char* sServerAddr, WORD wDVRPort)`

Relevant enums:
- `NET_SDK_CONNECT_TCP = 0`
- `NET_SDK_CONNECT_NAT = 1`
- `NET_SDK_CONNECT_NAT20 = 2`

Interpretation:
- The vendor SDK already exposes a NAT-capable login path through `NET_SDK_LoginEx`.
- The NAT login call accepts a device serial / UID in `sDevSN`.
- NAT2.0 can be configured explicitly with `NET_SDK_SetNat2Addr` before login.

## Companion NAT Library

Shared libraries present in the Linux SDK bundle:
- `Linux/bin/libdvrnetsdk.so`
- `Linux/bin/libNatClientSDK.so`
- `Linux/bin/libNatClientSDK.so.1`

Exported NAT functions observed in `libNatClientSDK.so.1`:
- `NAT_CLIENT_Init`
- `NAT_CLIENT_Start`
- `NAT_CLIENT_Stop`
- `NAT_CLIENT_ConnectDev`
- `NAT_CLIENT_GetDevInfo`
- `NAT_CLIENT_QueryDevInfo`
- `NAT_CLIENT_GetNatAccessToken`
- `NAT_CLIENT_ConnectNatServer`
- `NAT_CLIENT_DisConnectNatServer`
- `NAT_CLIENT_SetNotifier`
- `NAT_CLIENT_SetNatServerNotifier`

Interpretation:
- The SDK ships a separate NAT runtime and the main NetSDK depends on it for P2P transport.
- For pytvt's first implementation, wrapping `NET_SDK_LoginEx` is the safest path.
- The lower-level `NAT_CLIENT_*` surface remains documented here for future work, but is not required for the first production AutoNAT path.

## Implementation Decision

First implementation in pytvt uses:
- `NET_SDK_LoginEx(..., NET_SDK_CONNECT_NAT20, sDevSN)` for NAT login
- Optional `NET_SDK_SetNat2Addr(...)` when callers need an explicit NAT2 endpoint
- Explicit validation that `libNatClientSDK.so` is present before any NAT login attempt

Deferred work:
- Direct wrapping of `NAT_CLIENT_*`
- Reverse engineering cloud bootstrap details outside the public SDK contract
- Pure-Python NAT traversal implementation
