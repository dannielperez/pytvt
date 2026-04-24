# pytvt v0.6.0

AutoNAT / P2P support and scalable connection pooling for managing many devices concurrently through the vendor SDK.

## Highlights

**AutoNAT login** — `pytvt` now wraps the vendor SDK's `NET_SDK_LoginEx` with `ConnectType.NAT` / `ConnectType.NAT20` for P2P device access. Devices that are not directly IP-reachable can be managed by serial / UID identifier through the vendor's cloud relay. Requires `libdvrnetsdk.so` + `libNatClientSDK.so` on Linux.

**Connection pooling** — New `ConnectionPool` provides thread-safe session management with reuse, keepalive probing, stale eviction, reconnection with backoff, and configurable `max_active_sessions`. Per-session `SessionMetrics` track handshake time, idle duration, and health status.

**Bulk connect** — `connect_many()` connects to a list of devices concurrently via ThreadPoolExecutor, returning structured `SessionResult` objects. New `pytvt connect-many` CLI command reads a JSON device file and outputs a status table or JSON.

**Unified connect facade** — `NetSdkClient.connect(method="direct"|"nat", ...)` provides a single entry point for both connection modes with automatic NAT→direct fallback.

## What's new

- `NetSdkClient.login_nat()` and `NetSdkClient.connect()` facade
- `TVTClient` convenience alias
- `NatUnavailableError`, `NatLoginFailed`, `NatTimeoutError` exceptions
- `ensure_nat_support()` / `load_sdk(require_nat=True)` NAT validation
- `ConnectionPool` with keepalive, eviction, reconnection
- `connect_many()` bulk concurrent connector
- `SessionMetrics`, `SessionResult`, `PoolStats` observability types
- `pytvt connect` — single-device direct or NAT login CLI
- `pytvt connect-many` — bulk device connection CLI with table/JSON output
- `DeviceManager.from_device()` classmethod, NAT-aware auto-detect
- `DeviceEntry` extended: `identifier`, `connection_method`, `nat_server`, `nat_port`, `connection_preference`, `last_connection_method`, `nat_capable`
- `NET_SDK_LoginEx` and `NET_SDK_SetNat2Addr` ctypes bindings
- SDK NAT capability documentation in `src/pytvt/sdk/nat_capabilities.md`
- 47 new tests (583 total), ruff clean

## Upgrade notes

- No breaking changes to existing APIs
- Direct connection workflows are unaffected
- NAT features require Linux with the vendor SDK + NAT companion library
- `DeviceEntry.from_dict()` now accepts `identifier` aliases: `id`, `uid`, `serial`, `sn`
- New `connection_preference` field on `DeviceEntry` allows per-device NAT/direct/auto preference

---

# pytvt v0.5.1

Release hardening for public PyPI distribution. This release keeps local SDK-enabled workflows intact while ensuring the published package never redistributes proprietary TVT SDK components.

## Highlights

**Runtime-only SDK loading** — Native TVT SDK components are now loaded only at runtime from explicit user-provided paths. `TVT_SDK_PATH` is supported across the Python package, with the legacy SDK env var still honored for existing local setups.

**Public-package safety** — Wheel and sdist builds exclude native SDK binaries, headers, bridge workspaces, research folders, and other local-only material. The published wheel remains pure Python.

**Graceful fallback behavior** — SDK-free features continue to work without the SDK installed. SDK-dependent features now fail with clear `NetSdkUnavailable` and `NoBackendAvailable` errors instead of assuming bundled files or repo-relative paths.

## What's new

- `TVT_SDK_PATH` support for `load_sdk()`, `NetSdkClient`, and `DeviceManager`
- Python-native `sdk-local` backend using ctypes instead of a Node.js bridge
- Legacy `PYTVT_NETSDK_LIB` compatibility retained
- Expanded `.gitignore`, `MANIFEST.in`, and hatch build exclusions for native SDK artefacts
- Updated README and contributor docs for compliant public distribution
- Full test-suite validation and clean-install smoke verification for 0.5.1

## Upgrade notes

- SDK users should prefer `TVT_SDK_PATH` over the legacy env var name
- `sdk-local` users only need `TVT_SDK_PATH`; no Node.js runtime is required
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
