# pytvt v0.5.1

Release hardening for public PyPI distribution. This release keeps local SDK-enabled workflows intact while ensuring the published package never redistributes proprietary TVT SDK components.

## Highlights

**Runtime-only SDK loading** — Native TVT SDK components are now loaded only at runtime from explicit user-provided paths. `TVT_SDK_PATH` and `TVT_SCAN_SCRIPT` are supported across the Python package and local Node.js bridge, with legacy environment variables still honored for existing local setups.

**Public-package safety** — Wheel and sdist builds exclude native SDK binaries, headers, bridge workspaces, research folders, and other local-only material. The published wheel remains pure Python.

**Graceful fallback behavior** — SDK-free features continue to work without the SDK installed. SDK-dependent features now fail with clear `NetSdkUnavailable` and `NoBackendAvailable` errors instead of assuming bundled files or repo-relative paths.

## What's new

- `TVT_SDK_PATH` support for `load_sdk()`, `NetSdkClient`, and `DeviceManager`
- `TVT_SCAN_SCRIPT` support for the `sdk-local` backend
- Legacy `PYTVT_NETSDK_LIB` and `PYTVT_SCAN_SCRIPT` compatibility retained
- Expanded `.gitignore`, `MANIFEST.in`, and hatch build exclusions for native SDK artefacts
- Updated README and contributor docs for compliant public distribution
- Full test-suite validation and clean-install smoke verification for 0.5.1

## Upgrade notes

- SDK users should prefer `TVT_SDK_PATH` over the legacy env var name
- `sdk-local` users can point directly at a local `scan_nvr.mjs` bridge with `TVT_SCAN_SCRIPT`
- No SDK binaries, headers, or bridge folders are shipped in the PyPI artifacts

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
