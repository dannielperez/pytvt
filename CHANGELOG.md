# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] — 2026-04-09

### Added

- **`DeviceManager` unified facade** — single API for TVT device operations with
  automatic backend selection. Probes native SDK availability first, then falls
  back to the tvt-api Docker container. Supports forced backend selection.
  - `device_info()`, `device_time()`, `snapshot()`, `rtsp_url()`, `ptz()`,
    `ptz_preset()`, `reboot()` — all return the same result types regardless
    of backend.
  - `available_backends()` — probe what's available on the current platform.
  - `Backend` enum, `NoBackendAvailable` exception.
- **`is_netsdk_available()` probe** — check if the native SDK is loadable without
  actually loading it (platform + architecture + file existence check).
- **Expanded netsdk loader** — searches env var → vendored → tvt-api submodule →
  tvt repo → system. Added `_arch_dir()` for platform-specific directory selection,
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
- **`SdkHttpClient`** — typed Python client for the tvt-api Docker container with
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

[0.5.0]: https://github.com/dannielperez/pytvt/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/dannielperez/pytvt/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/dannielperez/pytvt/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dannielperez/pytvt/releases/tag/v0.2.0
