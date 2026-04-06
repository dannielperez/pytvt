# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: https://github.com/dannielperez/pytvt/releases/tag/v0.2.0
