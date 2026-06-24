# pytvt — Field Learnings (validated 2026-06-23)

From a live WAN-outage response: pulled the NVMS platform inventory and repointed
57 NVRs' Platform Access to a DDNS over AutoNAT. See also
`src/pytvt/sdk/nat_capabilities.md` (detailed AutoNAT notes).

## PlatformSDK (management/transfer server)
- `libPlatClientSDK.so` is **x86_64 Linux only** (no macOS build) → run in a container.
  Companion .so set (libNetCommon, libNodeManager, libShareLib, libuuid, libcrypto.so.1.1)
  must be on `LD_LIBRARY_PATH`; the trimmed vendor bundle omits deps.
- Login: dedicated/admin account, port **6003**.
- **BUG — online status is wrong:** `list_devices_normalized()` `.online` reflects the
  **create-time** notification only (reads all-offline). Live status arrives in separate
  `update_state` notifications carrying `nConnState` keyed by `ulNodeID`, which the normalizer
  **drops**. Fix: merge latest `update_state.nConnState==1` per node to compute live online.
  Also `list_resources()` must **settle** (poll until node count stable) after login or it
  returns 0.
- `stPlat_ResNodeInfo` has **no serial number** (only node id/name/type/online/ip/channels) —
  even in the newest header. The "IP/Domain" field is a real IP/DDNS OR a numeric **auto-report
  report ID** for "Initiatively report" devices. Device serials come from the device SDK, not here.

## AutoNAT (device SDK, reach NVRs by serial — independent of LAN/VPN)
- Works from Docker-on-Mac (earlier "Docker P2P wall" was a misdiagnosis — it was the wrong NAT server).
- **NAT2 server is deployment-specific & mandatory:** get it from the device's NAT "Visit Address".
  Observed: `dev-nat20.autonatglb.com:7968` and `dev-nat20.autonat.us:7968` (fleet spans both;
  iterate endpoints in separate passes). `error=8` = wrong/unreachable NAT server (NOT auth).
- `NET_SDK_SetNat2Addr` is **global, set-once per SDK load** → use ONE `NetSdkClient` for the whole
  batch, set it once, login each serial with `nat_server=None`.
- **Companion libs must be GLOBALLY preloaded** (Linux): `LD_PRELOAD=libcrypto.so.1.1 libShareLib.so`,
  else `libNatClientSDK` fails with `undefined symbol: BIO_ctrl` then `SHARESDK_CreateSingleton`.
  Loader's `_preload_companion_libraries` should handle this.
- `DeviceInfo` field is **`product`**, not `model`. macOS device SDK exports C++-mangled
  `NET_CLIENT_*`, not the Linux `NET_SDK_*` C ABI → not a pytvt drop-in.

## Config read/write over NAT — use NET_SDK_ApiInterface (NOT GetConfigFile)
- The whole-config blob (`GetConfigFile`) is **encrypted** — not patchable.
- **`NET_SDK_ApiInterface(lUserID, sendXML, strUrl, outBuf, outSize, &ret)`** runs the device
  **web CGI over the authenticated handle** (works over NAT, no IP): `strUrl="queryPlatformCfg"`/
  `"editPlatformCfg"`, `sendXML=<request><token>null</token>…`. Should be a first-class pytvt method.
  (Signature in the manual bundled in the `tvt-api` image: `…/docs/include/DVR_NET_SDK.h`.)
- Platform-access XML: `<content type="list" current="NVMS5000"><item id="NVMS5000"><switch>…
  </switch><serverAddr>…</serverAddr><port>2009</port><reportId>…</reportId></item></content>`.

## TODO
- Fix `list_devices_normalized` live-online merge (above).
- First-class `api_interface()` / `query_platform_access()` / `set_platform_access()` over NAT.
- Multi-NAT-endpoint helper (iterate `dev-nat20.autonatglb.com` / `.autonat.us`).
