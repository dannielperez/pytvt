# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] — 2026-04-24

### Added

- **PlatformSDK backend** (`pytvt.management.ManagementClient` with
  `backend_mode="platform_sdk"`). Wraps TVT NVMS `libPlatClientSDK.so` /
  `PlatClientSDK.dll` (20250115 release) through `ctypes`, adding async
  callback dispatch, resource/server enumeration, and credential-safe
  normalized models.
  - `platform_constants` — message-type / connect-state / node-type /
    device-type / alarm-host-type name tables plus permission bitmask
    decoders (`decode_system_rights`, `decode_area_rights`) and
    `redact_sensitive()`.
  - `platform_models` — frozen dataclasses `PlatformResource`,
    `PlatformServer`, `PlatformAlarmZone`, `PlatformUser`,
    `PlatformPermissionGroup` (the user model never exposes passwords).
  - Read APIs: `list_resources[_normalized|_tree]`, `list_areas`,
    `list_devices_normalized`, `list_channels_normalized`,
    `find_resource_by_guid`, `find_resource_by_name`, `list_servers`,
    `list_transfer_servers`, `list_storage_servers`, `list_access_servers`,
    `list_alarm_servers`, `list_tv_wall_servers`,
    `list_intelligent_analysis_servers`, `list_alarm_zones`,
    `list_server_connection_events`.
  - Write scaffolds (`create_user`, `create_permission_group`,
    `create_transfer_server`, `create_tv_wall`, `add_device`) default to
    `dry_run=True` and refuse real mutations.
  - Unreachable APIs (`list_users`, `list_permission_groups`,
    `list_logs*`, `list_tv_walls`, `list_alarm_events`,
    `list_active_alarms`) raise `CapabilityNotAvailable` with an
    explanation instead of failing silently.

- **Phase 2 operational-intelligence modules** (read-only, pure-data):
  - `pytvt.platform_topology` — `PlatformSite` dataclass +
    `build_site_topology()`. Groups areas/devices/channels/zones
    deterministically; orphans collected under a synthetic site.
  - `pytvt.platform_health` — `PlatformDeviceHealth` dataclass +
    `compute_device_health()`. Computes ONLINE / DEGRADED / OFFLINE from
    device/channel online flags and alarm events.
  - `pytvt.platform_alarms` — `PlatformAlarmEvent` dataclass +
    `normalize_alarm_events()`. Maps raw alarm rows into
    `intrusion | line_crossing | offline | motion | unknown` with a
    `low | medium | high` severity.
  - `pytvt.platform_classification` — heuristic `classify_resource()`
    returning `{type, confidence, signals}` using dev type + channel
    count + name patterns.
  - `pytvt.platform_capabilities` — `detect_capabilities()` probes each
    read method and reports reachability.
  - `pytvt.platform_inventory` — `get_platform_inventory_snapshot()`
    stitches all layers into a single JSON-safe dict; tolerates missing
    capabilities.

- **`tools/validate_platform_inventory.py`** — live smoke-test CLI.
  Credentials are read from environment variables (optionally from a
  `.env` file) via `python-dotenv`; passwords are never accepted as CLI
  flags. Adds `--topology`, `--health`, `--alarms`, `--summary`, and
  `--json` output flags.

- **Tests**: 35 unit tests for the PlatformSDK backend
  (`test_platform_sdk.py`) and 22 tests for the Phase 2 modules
  (`test_platform_operations.py`). All mock-based; no live dependency.

- **Docs**: `docs/platform_sdk.md` capability matrix with validation
  guidance. PII-free.

### Changed

- `ManagementClient` now exposes the read-only PlatformSDK passthroughs
  listed above via `_platform_call`, behind the pre-existing
  `backend_mode="platform_sdk"` selector. Legacy `native_protocol` and
  `sidecar` backends are unchanged.

### Security

- Snapshot / validator output is scrubbed via `redact_sensitive()` —
  fields named `password`, `passwd`, `pwd`, `secret`, `token`,
  `api_key`, `apikey` are replaced with `"<redacted>"`.
- `PlatformUser` intentionally does not expose `Plat_UserInfo.password`;
  only a boolean `has_password` flag.

## [0.6.2] — 2026-04-17

### Added

- **Platform Access (Auto Report) CGI methods** on `NvrClient`
  - `query_platform_access()` → `PlatformAccessConfig` — read the NVR's CMS/NVMS5000 registration settings
  - `set_platform_access(enabled, server_address, port, report_id)` — configure or disable platform registration
  - `PlatformAccessConfig` dataclass — `enabled`, `server_address`, `port`, `report_id`
  - CGI endpoints: `queryAutoReportCfg` / `editAutoReportCfg`
  - 6 new tests covering query, set, round-trip, defaults, disabled state, and empty fields

## [0.6.1] — 2026-04-15

### Added

- **`NatConfig` dataclass** — response model for NAT/P2P cloud relay settings
  (`enabled`, `nat_serial`, `nat_status`, `visit_address`, `security_access`).
- **`WebApiClient.get_nat_config()`** — queries `/Network/NAT` (with
  `/Network/NATConfig` fallback) to retrieve the device's cloud P2P identifier
  (e.g. "NAAC909BNQGD") and NAT registration status. Parses multiple XML field
  name variants across firmware versions.
- **`diagnostics()` top-level accessor** — lazy import to avoid heavy ctypes
  probes at package import time.

### Changed

- Version bumped to 0.6.1.

## [0.6.0] — 2026-04-14

### Added

- **AutoNAT / P2P login** — wrap the vendor SDK's `NET_SDK_LoginEx` with
  `ConnectType.NAT` and `ConnectType.NAT20` for P2P device access without
  direct IP reachability. New `login_nat()` and unified `connect()` facade on
  `NetSdkClient`.
- **`TVTClient`** — convenience alias for `NetSdkClient` with `connect()`
  support.
- **NAT companion library validation** — `ensure_nat_support()` and
  `load_sdk(require_nat=True)` verify that `libNatClientSDK.so` is present
  before attempting NAT operations.
- **NAT-specific exceptions** — `NatUnavailableError`, `NatLoginFailed`,
  `NatTimeoutError` for granular error handling.
- **`ConnectionPool`** — thread-safe session pool with configurable
  `max_active_sessions`, `idle_timeout`, `reconnect_backoff`, and
  `keepalive_interval`. Background keepalive thread probes idle sessions;
  stale sessions are evicted automatically.
- **`connect_many()`** — bulk concurrent device connection via ThreadPoolExecutor.
  Returns structured `SessionResult` per device with target, success flag,
  latency, connection method, device info, and error detail.
- **`SessionMetrics`** — per-session observability: handshake time, idle duration,
  session duration, keepalive health, reconnect count.
- **`PoolStats`** — aggregate pool counters (active, idle, connects, failures,
  reconnects).
- **`pytvt connect` CLI** — single-device SDK or AutoNAT login with
  `--nat`/`--direct`, `--id`, `--nat-server`, `--nat-port`, `--nat-type` flags.
- **`pytvt connect-many` CLI** — bulk device connection from a JSON file with
  `--concurrency`, `--nat`, `--json` output, and tabular text output.
- **`DeviceManager` NAT integration** — `from_device()` classmethod,
  `connection_method`/`identifier` properties, NAT-aware `_auto_detect()`, and
  `connect()` routing for both direct and NAT sessions.
- **`DeviceEntry` model extensions** — new fields: `identifier`,
  `connection_method`, `nat_server`, `nat_port`, `connection_preference`,
  `last_connection_method`, `nat_capable`. `from_dict()` supports aliases
  (`id`, `uid`, `serial`, `sn`, `prefer`). `effective_connection_method`
  respects explicit preference.
- **ctypes bindings** — `NET_SDK_LoginEx` and `NET_SDK_SetNat2Addr` declarations
  in `netsdk/bindings.py`.
- **SDK capability documentation** — `src/pytvt/sdk/nat_capabilities.md`.
- **47 new tests** — connection pool lifecycle, keepalive, eviction, bulk connect,
  CLI parsers, model extensions. Total test count: 583.

### Changed

- `DeviceManager._get_netsdk_session()` now uses `client.connect()` (keyword-only)
  instead of `client.login()` for unified direct/NAT routing.
- `netsdk/loader.py` refactored companion loading into `_preload_companion_libraries()`
  with structured dependency groups and explicit NAT validation.
- `DeviceSession` now carries `connection_method`, `target`, `identifier`, and
  `handshake_duration_ms` metadata.

## [0.5.1] — 2026-04-14

### Changed

- **Release hardening for PyPI** — native TVT SDK components are now loaded only
  at runtime from explicit user-provided paths or environment variables.
- **SDK path configuration** — added `TVT_SDK_PATH` and `TVT_SCAN_SCRIPT`
  support across config loading, `DeviceManager`, `NetSdkClient`, and the
  local Node.js bridge, while preserving legacy env vars for local workflows.
- **Packaging safety** — expanded build exclusions, MANIFEST rules, and
  `.gitignore` patterns to prevent native SDK binaries, headers, and local SDK
  workspaces from being published or accidentally committed.
- **Documentation** — clarified that the proprietary TVT SDK is never
  redistributed, documented runtime SDK setup, and separated SDK-backed
  features from SDK-free features.

## [0.5.0] — 2026-04-09

### Added

- **`DeviceManager` unified facade** — single API for TVT device operations with
  automatic backend selection. Probes native SDK availability first, then falls
  back to an SDK bridge service. Supports forced backend selection.
  - `device_info()`, `device_time()`, `snapshot()`, `rtsp_url()`, `ptz()`,
    `ptz_preset()`, `reboot()` — all return the same result types regardless
    of backend.
  - `available_backends()` — probe what's available on the current platform.
  - `Backend` enum, `NoBackendAvailable` exception.
- **`is_netsdk_available()` probe** — check if the native SDK is loadable without
  actually loading it (platform + architecture + file existence check).
- **Expanded netsdk loader** — searches explicit path/env var → SDK root →
  system. Added `_arch_dir()` for platform-specific directory selection,
  pre-loading of companion `.so` dependencies.
- **aarch64 support** — loader recognizes `linux-arm64` bin directory for ARM64 Linux.

### Changed

- `netsdk/__init__.py` now exports `is_netsdk_available` and `NetSdkUnavailable`.
- Public `__init__.py` exports `DeviceManager`, `Backend`, `NoBackendAvailable`,
  `available_backends`.

## [0.4.0] — 2026-04-08

### Added

- **`pytvt.netsdk` package** — ctypes bindings for the TVT NetSDK C++ library
  (`libdvrnetsdk.so`). `NetSdkClient` + `DeviceSession` with 25+ methods covering
  device info, PTZ, snapshots, RTSP URLs, alarms, recording, firmware, disks,
  users, time sync, and more.
- **`SdkHttpClient`** — typed Python client for an SDK bridge service with
  10 methods: `health()`, `scan()`, `device_info()`, `device_time()`, `snapshot()`,
  `rtsp_url()`, `ptz()`, `ptz_preset()`, `reboot()`.
- **Result dataclasses** — `DeviceInfoResult`, `DeviceTimeResult`, `RtspUrlResult`,
  `CommandResult` for SDK HTTP responses.
- **135 netsdk tests** + **26 SdkHttpClient tests** (161 new tests).

## [0.3.0] — 2026-04-07

### Added

- **`WebApiClient`** — TVT HTTP API (LAPI protocol) client with HTTP Basic auth.
  Per-device management: capability detection, device info, channels, disks,
  date/time, password management, image/stream/audio/OSD configuration, snapshots
  with RTSP fallback, recording status and search.
- **66 new tests** for the Web API client.

## [0.2.0] — 2026-04-06

First packaged release. Restructured from flat scripts into an installable Python
package with formalized architecture, new operator-facing features, and
comprehensive test coverage.

### Added

- **Scan diffing / change detection** — compare two JSON scan result files to detect
  device and camera changes between runs. New `pytvt-diff` CLI command with
  human-readable, JSON, and summary output modes. New library API:
  `diff_scans()`, `load_scan_file()`, `ScanDiff`, `DeviceDiff`.
- **Backend family / integration mode architecture** — formalized `BackendFamily`
  (protocol, sdk) and `IntegrationMode` (compat_bridge, direct_sdk) enums with
  `CompositeStrategy` for multi-backend fallback.
- **Execution plan layer** — `ExecutionPlan` dataclass and `resolve_execution_plan()`
  resolver that makes "what will be executed" explicit and inspectable before a
  scan runs.
- **Backend registry** — `register()` / `get()` / `dispatch()` dispatch table
  mapping (family, mode) tuples to scan callables, with default wiring at import time.
- **Research / reference isolation** — protocol reverse-engineering scripts moved to
  `research/` with clear warning headers. Import boundary enforced by test.
- **Typed exception hierarchy** — `PytvtError`, `BackendError`, `RegistryError`.
- **Comprehensive test suite** — 237 tests across 13 files covering protocol
  encryption, config precedence, models, scanner dispatch, CLI parsing, output
  formatting, discovery, SDK backends, architecture invariants, and scan diffing.
- **CI pipeline** — GitHub Actions running pytest + ruff on Python 3.10–3.13.
- **Contributor docs** — CONTRIBUTING.md with code tier guidance, PR template,
  issue templates.
- **`pytvt-diff` console script** — registered in pyproject.toml alongside the
  existing four CLI entry points.

### Changed

- **Package layout** — migrated from flat scripts to `src/pytvt/` installable
  package with hatchling build backend.
- **CLI decomposition** — split monolithic main.py into focused modules: cli.py,
  config.py, models.py, scanner.py, protocol.py, discovery.py, output.py,
  sdk_http.py, sdk_local.py, nvr_api.py, snapshot.py.
- **Data models** — replaced raw dicts with typed dataclasses (`ScannerConfig`,
  `DeviceEntry`, `CameraInfo`, `ScanResult`) with factory methods and serialization.
- **Backend normalization** — CLI backend strings (`protocol`, `sdk`, `sdk-local`,
  `both`) are normalized via `resolve_backend()` with backward-compatible aliases
  (`sdk_local` → `sdk-local`, `compat_bridge` → `sdk`, `direct_sdk` → `sdk-local`).
- **Node.js bridge** — moved `scan_nvr.mjs` to `bridges/sdk_local/` with its own
  `package.json`, isolated from the Python package.
- **README** — complete rewrite with architecture diagram, backend comparison table,
  integration mode explanations, full CLI reference, and project structure tree.

### Licensing

- Licensed under MIT starting from 1.0.0. Previous versions were AGPLv3.

[0.6.0]: https://github.com/dannielperez/pytvt/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/dannielperez/pytvt/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/dannielperez/pytvt/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/dannielperez/pytvt/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/dannielperez/pytvt/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dannielperez/pytvt/releases/tag/v0.2.0
