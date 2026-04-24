# PlatformSDK backend — capability matrix

The PlatformSDK backend wraps TVT's `libPlatClientSDK.so` /
`PlatClientSDK.dll` (NVMS management server SDK, 20250115 release) via
`ctypes`.  It is selected by passing `backend_mode="platform_sdk"` and
`platform_sdk_path="/path/to/libPlatClientSDK.so"` to `ManagementClient`.

## Live-validated against a reference management server (2026-04-23)

Capability matrix reflects behavior observed end-to-end on a reference TVT
NVMS PlatformSDK 20250115 deployment.  Row counts below are from that
deployment and are intended as order-of-magnitude evidence rather than a
guaranteed contract.

| Capability | ManagementClient method | Status | Notes |
|---|---|---|---|
| Login | `login()` | ✅ live | `Plat_LoginEx` + async `MSGTYPE_CONNECT_NTF` |
| Resources (raw nodes) | `list_resources()` | ✅ live | ~8.8k rows on reference server |
| Areas | `list_areas()` | ✅ live | ~170 areas |
| Devices (normalized) | `list_devices_normalized()` | ✅ live | ~220 devices |
| Channels (normalized) | `list_channels_normalized()` | ✅ live | ~5.4k channels |
| Resource tree | `list_resources_tree()` | ✅ live | parent/child hierarchy |
| Find resource by GUID | `find_resource_by_guid()` | ✅ live | |
| Find resource by name | `find_resource_by_name()` | ✅ live | substring match |
| All servers (merged) | `list_servers()` | ✅ live | 2 on ref server (1 AI, 1 alarm-host) |
| Transfer servers | `list_transfer_servers()` | ✅ live | heuristic: connect_events w/o typed callback |
| Storage servers | `list_storage_servers()` | ✅ live | none deployed on ref server |
| Alarm servers (hosts) | `list_alarm_servers()` | ✅ live | 1 alarm host |
| Intelligent-analysis servers | `list_intelligent_analysis_servers()` | ✅ live | 1 AI server |
| Alarm zones | `list_alarm_zones()` | ✅ live | 1 zone |
| Server connect events (raw) | `list_server_connection_events()` | ✅ live | 21 events |
| Access servers | `list_access_servers()` | ⏸ placeholder | no callback/MSGTYPE exposed in 20250115 |
| TV-wall servers | `list_tv_wall_servers()` | ⏸ placeholder | goes through XML RPC |

## Not yet reachable

These methods raise `CapabilityNotAvailable` with an explanation.

| Method | Blocker |
|---|---|
| `list_users()` | `Plat_GetAllUserInfo` takes `std::list<Plat_UserInfo>&` (C++ ABI). |
| `list_permission_groups()` | `Plat_GetAllAuthGroupInfo` same blocker + `std::map` in struct. |
| `list_logs()` + variants | No public SDK symbol in 20250115 headers; needs HTTP fallback. |
| `list_tv_walls()` | Only reachable through `Plat_RequestKbTvWallOptionEx` (XML). |
| `list_alarm_events()`, `list_active_alarms()` | Needs live MSGTYPE mapping validation. |

Resolving these requires either a small C++ shim library that flattens the
STL types into C-ABI arrays, or the management-server REST endpoints used by
the web UI.

## Write scaffolding (dry-run only)

These exist for API shape completeness but refuse to perform real mutations:

* `create_user`
* `create_permission_group`
* `create_transfer_server`
* `create_tv_wall`
* `add_device`

All accept `dry_run=True` (default) and return a plan dict.  Passing
`dry_run=False` raises `CapabilityNotAvailable` until each path is
individually bound and live-tested with explicit review.

## Live validation

Credentials are loaded from environment variables (optionally from a `.env`
file in the working directory) — they are never accepted as CLI flags, to
avoid leaking into shell history.  See [`.env.example`](../.env.example)
for the full list.

```bash
# Required env vars:
#   TVT_MGMT_IP, TVT_MGMT_PORT, TVT_MGMT_USERNAME,
#   TVT_MGMT_PASSWORD, TVT_PLATFORM_SDK

python tools/validate_platform_inventory.py [--json] [--sample 3] \
    [--topology] [--health] [--alarms] [--summary]
```

The script redacts sensitive keys (`password`, `token`, …) in all output.

## Constant semantics

`pytvt.management.platform_constants` exposes name lookups and bitmask
decoders for:

* `MSGTYPE_*` — PlatformSDK message-type ids
* `enPlat_ConnectState` — login/connection states
* `enPlat_NodeType` — area/device/channel/sensor
* `enPlat_DevType` — dvr/nvr/ipc/hikvision/dahua/onvif
* `enPlat_AlarmHostType` — hikvision/km/alean/dsc/paradox/bosch
* `PlatAuthGroup` — system and area permission bitmasks

Unknown values always fall back to `"unknown"` (or `"unknown_<n>"` for
server types) so downstream code never sees a bare integer.
