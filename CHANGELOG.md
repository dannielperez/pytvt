# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
