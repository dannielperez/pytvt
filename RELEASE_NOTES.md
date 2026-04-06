# pytvt v0.2.0

First packaged release — bulk TVT NVR scanner restructured into an installable Python package with formalized architecture and operator tooling.

## Highlights

**Scan diffing** — New `pytvt-diff` command compares two JSON scan result files and reports device/camera changes between runs. Detects added/removed devices, firmware upgrades, camera status changes, and failure drift. Available as CLI (console, JSON, or summary output) and as a library API.

```bash
pytvt-diff monday.json friday.json
pytvt-diff --json monday.json friday.json
```

**Formalized backend architecture** — Backends are now modeled as `BackendFamily` × `IntegrationMode` enums with an `ExecutionPlan` that makes scan resolution explicit before execution. The registry, dispatch, and composite strategy (protocol-then-SDK fallback) are cleanly separated.

**237 tests** — Comprehensive pytest suite covering protocol encryption, config loading, data models, scanner dispatch, CLI parsing, output formatting, discovery, SDK backends, architecture invariants, and the new diffing feature. CI runs on Python 3.10–3.13.

**Research isolation** — Protocol reverse-engineering scripts are preserved in `research/` with clear "not runtime" warnings. An import-boundary test enforces that no runtime module depends on research code.

## What's new

- `pytvt-diff` CLI command (console / `--json` / `--summary`)
- `diff_scans()`, `load_scan_file()`, `ScanDiff`, `DeviceDiff` library API
- `BackendFamily`, `IntegrationMode`, `CompositeStrategy` enums
- `ExecutionPlan` dataclass + `resolve_execution_plan()` resolver
- Backend registry with `register()` / `get()` / `dispatch()`
- `PytvtError` / `BackendError` / `RegistryError` exception hierarchy
- Typed dataclass models: `ScannerConfig`, `DeviceEntry`, `CameraInfo`, `ScanResult`
- `src/pytvt/` package layout with hatchling build
- GitHub Actions CI (pytest + ruff, Python 3.10–3.13)
- CONTRIBUTING.md with code tier guidance
- Full README with architecture diagram and CLI reference

## Backend normalization

All CLI backend names from previous usage continue to work:

| You type | Resolves to |
|---|---|
| `protocol` | BackendFamily.PROTOCOL |
| `sdk` | BackendFamily.SDK / compat_bridge |
| `sdk-local` | BackendFamily.SDK / direct_sdk |
| `sdk_local` | *(alias)* → `sdk-local` |
| `both` | CompositeStrategy.BOTH |
| `compat_bridge` | *(alias)* → `sdk` |
| `direct_sdk` | *(alias)* → `sdk-local` |

## Upgrading

This is the first tagged release. If you were using pytvt from a checkout:
- `pip install -e .` continues to work
- All existing CLI flags are preserved
- JSON scan output format is unchanged (diffing reads the same files `pytvt -o` writes)

## Install

```bash
git clone https://github.com/dannielperez/pytvt.git
cd pytvt
pip install -e .
```

Python 3.10+ required. No native dependencies for the default protocol backend.
