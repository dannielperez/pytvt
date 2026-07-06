# pytvt — Field Learnings (validated 2026-06-23)

From a live WAN-outage response: pulled the NVMS platform inventory and repointed
57 NVRs' Platform Access to a DDNS over AutoNAT. See also
`src/pytvt/sdk/nat_capabilities.md` (detailed AutoNAT notes).

## PlatformSDK (management/transfer server)
- `libPlatClientSDK.so` is **x86_64 Linux only** (no macOS build) → run in a container.
  Companion .so set (libNetCommon, libNodeManager, libShareLib, libuuid, libcrypto.so.1.1)
  must be on `LD_LIBRARY_PATH`; the trimmed vendor bundle omits deps.
- Login: dedicated/admin account, port **6003**.
- **BUG (FIXED 1.2.1) — online status was wrong:** `list_devices_normalized()` `.online`
  reflected the **create-time** notification only (reads all-offline). Live status arrives in
  separate `update_state` notifications carrying `nConnState` keyed by `ulNodeID`, which the
  normalizer **dropped**. `list_resources_normalized()` now merges the latest
  `update_state.nConnState==1` per node to compute live online (`TestLiveOnlineMerge`).
  Note: `list_resources()` must still **settle** (poll until node count stable) after login or
  it returns 0.
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

## Encode & record config over the SDK handle (validated 2026-07-01, fleet-live)
- The record/encode config pages are reachable via `api_call` (ApiInterface) — the CGI command
  names were the missing piece (guessing gave `error=11`; recover them from `strings libdvrnetsdk.so`).
  Now first-class `DeviceSession` methods:
  - **`node_encode_info()`** → `queryNodeEncodeInfo` per channel. Two main-stream profiles:
    `<an>` = **continuous** (schedule/24x7), `<ae>` = **event** (motion/alarm/AI). Attrs:
    `res, fps, bitType(VBR|CBR), level(low|medium|higher), QoI(max kbps), audio(ON|OFF)`; codec in
    sibling `<main enct=h264|h265|h265p aGOP mGOP>`. The list form returns **only `<name>`** unless
    you pass `<requireField><name/><mainCaps/><main/><an/><ae/><mainStreamQualityNote/></requireField>`.
  - **`set_node_encode(ch, continuous={…}, event={…}, codec=…)`** → `editNodeEncodeInfo` (read-modify-
    write, verified). Writes `<an>` and `<ae>` as **one item each** under `url="editNodeEncodeInfo"`.
  - **`record_schedule()`** → `queryRecordScheduleList`: per-channel `scheduleRec/motionRec/alarmRec/
    intelligentRec` (=AI) switches. (Some NVMS-9000 report these all-false while still recording 24x7 —
    record MODE is represented elsewhere on those; edit path for mode not yet mapped.)
- **Device CGI XML is NOT well-formed** — camera names routinely contain raw `&` etc. Parse api_call
  responses with the lenient regex helpers (`_xml_items/_xml_attrs/_xml_status`), NEVER ElementTree.
- **Nominal encode ≠ storage:** effective bitrate is scene + codec driven; `h265p` ≫ `h265` for size.
  Don't "clone a healthy NVR's profile" to save space — measure disk usage (`disk_info`+`recording_days`).

## TODO
- Fix `list_devices_normalized` live-online merge (above).
- First-class `api_interface()` / `query_platform_access()` / `set_platform_access()` over NAT.
- Multi-NAT-endpoint helper (iterate `dev-nat20.autonatglb.com` / `.autonat.us`).
- Map the record-**mode** edit path (24x7+AI radio) — `editManualRecord` seen; schedule-edit TBD.
