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

## Operational Learnings — validated AutoNAT run (2026-06-23)

First confirmed end-to-end AutoNAT logins against a live fleet (60+ NVRs by serial)
from a Linux x86_64 container. Hard-won, non-obvious findings:

### 1. The NAT2 server address+port is mandatory and deployment-specific
- `error=8` (`SdkError.NETWORK_FAIL_CONNECT`) on `login_nat` almost always means the
  **NAT2 server is wrong/unset**, NOT a device/credential/network-sandbox problem.
- The correct endpoint comes from the device web UI: **Function Panel ▸ NAT ▸ Access Type /
  Visit Address**. Observed values for this fleet:
  - NAT2.0: `dev-nat20.autonatglb.com:7968`  ← use this with `connect_type=nat20`
  - NAT1.0: `d2.autonat.com:20002`
  - (Generic `autonat.com:80` / `www.autonat.us` are NOT the SDK NAT2 control endpoint — do not use.)
- Set it via `NET_SDK_SetNat2Addr(server, port)` before `NET_SDK_LoginEx(..., NAT20, sn)`.
- **The fleet spans MORE THAN ONE NAT2.0 endpoint** — observed both `dev-nat20.autonatglb.com:7968`
  and `dev-nat20.autonat.us:7968` (same port, different host) across different NVRs. A device
  only logs in against the endpoint it is registered to. Because `SetNat2Addr` is set-once per
  SDK load (see #2), iterate endpoints in **separate passes/processes**: run the whole serial
  list against endpoint A, then re-run only the still-failed serials against endpoint B, etc.
  A serial that fails `error=8` on every known endpoint is genuinely offline (not registered /
  not maintaining its cloud tunnel).
- Stability note: tearing down a NAT session can SIGSEGV (exit 139) inside the NatClient peer
  threads on cleanup; results are already captured before teardown. Prefer a fresh process per
  endpoint pass so a crash can't lose a pass.

### 2. `NET_SDK_SetNat2Addr` is GLOBAL and effectively set-once per SDK load
- Call it ONCE per process, then reuse ONE `NetSdkClient` for the whole batch and call
  `login_nat(..., nat_server=None, nat_port=None)` for every serial.
- Anti-pattern: a fresh `DeviceManager`/`NetSdkClient` per serial re-calls `SetNat2Addr`,
  which returns False on the 2nd+ call → "Failed to configure NAT2 server".

### 3. Companion libraries must be GLOBALLY preloaded (Linux)
- `libNatClientSDK.so` has unresolved externs satisfied only at process scope:
  `LD_PRELOAD="<dir>/libcrypto.so.1.1 <dir>/libShareLib.so"`.
  Without it the loader fails: `undefined symbol: BIO_ctrl`, then `SHARESDK_CreateSingleton`.
- A bare `ctypes.CDLL(lib)` may *appear* to load it (lazy binding) while the loader's
  strict `_preload_companion_libraries` correctly fails — trust the loader, fix the preload.
- Complete lib set needed: `libdvrnetsdk.so`, `libNatClientSDK.so(.1)`, `libShareLib.so`,
  `libuuid.so`, `libcrypto.so.1.1` (OpenSSL 1.1 — Debian 12 ships 3.x; copy the 1.1 .so in).

### 4. Don't validate the NAT server with a TCP probe
- A raw TCP `connect()` to `dev-nat20.autonatglb.com:7968` TIMES OUT, yet SDK NAT2.0 login
  SUCCEEDS — the protocol is UDP. TCP reachability is not a usable health check.

### 5. AutoNAT P2P DOES work from Docker Desktop on macOS
- An earlier hypothesis blamed Docker/Mac NAT traversal for `error=8`. That was a
  misdiagnosis — the only fault was the wrong NAT server. No `--network host` needed.

### 6. API field notes
- `DeviceSession.device_info()` (over NAT) returns `DeviceInfo` with `serial_number`,
  `device_name`, `product`, `firmware` — there is **no `model`** attribute (use `product`).
- `connect_type` accepts `"nat20"` / `"nat"`; NAT2.0 is the current standard for this fleet.

### 7. macOS device SDK is not a pytvt drop-in
- `tvt-macos-app-*/binaries/libNatClientSDK.dylib` etc. export C++-mangled `NET_CLIENT_*`
  symbols (e.g. `_Z26NET_CLIENT_LoginServerUnit...`), NOT the Linux `NET_SDK_*` C ABI that
  `pytvt.netsdk.bindings` targets. Running NAT natively on macOS would require a separate
  binding layer. Use the Linux .so set (in a container is fine — see #5).

### Config read/write over NAT — SOLVED via NET_SDK_ApiInterface (2026-06-23)
**The device web CGI runs over the authenticated NAT handle** — no LAN/IP needed:
```
NET_SDK_ApiInterface(LONG lUserID, char* sendXML, char* strUrl,
                     LPVOID lpOutBuffer, DWORD dwOutBufferSize, LPDWORD lpBytesReturned)  // BOOL
```
(`NET_SDK_TransparentConfig` has the identical signature.) `strUrl` = the CGI command
(`queryPlatformCfg`, `editPlatformCfg`, …); `sendXML` = the `<request>` body the web UI sends;
response XML comes back in `lpOutBuffer`. The SDK login provides auth, so `<token>null</token>`
is fine. ctypes argtypes: `[c_long, c_char_p, c_char_p, c_void_p, c_uint, POINTER(c_uint)]`,
restype `c_bool`. Use a 128 KB out buffer. **Not in older headers** — found in the manual
bundled in the `tvt-api` image at `/app/api/tvt/docs/include/DVR_NET_SDK.h` (also `.../docs/Device Net SDK manual.pdf`).
- Working tool: `data/tvt_platform_access.py` — read (default) or `--new-server ADDR [--apply]`
  to repoint Platform Access serverAddr, preserving `switch`/`port`/`reportId`, skipping
  `switch=false` devices, with post-edit re-query verification. Validated: edited+verified live.
- Platform Access query/edit XML: `<content type="list" current="NVMS5000"><item id="NVMS5000">`
  `<switch>true</switch><serverAddr>..</serverAddr><port>2009</port><reportId>NNN</reportId></item></content>`.

### (Historical) Config write over NAT — earlier dead-ends before ApiInterface was found
Goal: set each NVR's Platform Access (NVMS5000 auto-report) `serverAddr` → a DDNS, over the
AutoNAT session. Findings (2026-06-23):
- **Granular CGI (`editPlatformCfg`/`queryPlatformCfg`, `pytvt.nvr_api`)** is HTTP to the device
  web port → needs a routable IP. A pure NAT P2P session gives no IP. No HTTP-over-tunnel
  function is exported by `libdvrnetsdk.so` (probed: no `NET_SDK_SendHttpRequest`,
  `NET_SDK_*Transparent*`, `NET_SDK_SendData`, `NET_SDK_RemoteControl`, etc.).
- **Whole-config (`NET_SDK_GetConfigFile`/`SetConfigFile`)** works over NAT, BUT the exported
  config (~109 KB) is **encrypted/obfuscated** — high-entropy, only MD5-like hashes + a model
  code are readable, no plaintext `serverAddr`/hostnames/channel names. So locate-and-patch is
  not viable, and re-importing a hand-edited blob risks bricking config. Ruled out.
- **Granular SDK config**: `NET_SDK_GetDVRConfig`/`NET_SDK_SetDVRConfig` symbols DO exist in the
  .so (not bound in pytvt), but the per-block command IDs/structs aren't in the available
  headers, and the modern Platform-Access feature may not be in the legacy DVR-config command
  set. Config blocks are keyed by `DD_CONFIG_ITEM_ID` and parsed by the C++ `ConfigBlock` class
  (not a C ABI) — not callable from ctypes without significant RE.
- **Conclusion:** AutoNAT is great for REACH + READ (login, device_info), but the platform-access
  WRITE needs the device web CGI, i.e. **IP reachability** (over the site VPN / LAN), not the
  pure NAT session. Recommended repoint path: reach each device by LAN IP over its (restored)
  site WG tunnel and call `nvr_api.set_platform_access(server_address=<ddns>)` — granular,
  reversible (query→set→verify). NAT-only config write would require new RE (bind GetDVRConfig
  with the right command, or reverse the NAT→local-web-proxy the NVMS5000 client uses).
