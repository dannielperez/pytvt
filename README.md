# pytvt

Python toolkit for TVT device, NVR, and management-server workflows.

Today `pytvt` covers two parallel tracks:

- Stable device and NVR operations: discovery, bulk scanning, diffing, NVR CGI, TVT Web API, direct SDK access, AutoNAT/P2P login, and unified device management.
- Additive management-server integration: `ManagementClient`, PlatformSDK-backed inventory/diagnostics, and sidecar-compatible runtime modes that are still explicitly provisional.

The published package is pure Python. Optional vendor SDK integrations are loaded from user-supplied installations and are never bundled in the wheel or sdist.

## Installation

```bash
pip install pytvt
```

Requires Python 3.10+. The published wheel contains only pure Python code.

## What pytvt includes

### Stable device and NVR surface

- Bulk NVR scanning with `protocol`, `sdk`, `sdk-local`, and `both` execution modes
- LAN discovery and remote subnet discovery
- `NvrClient` for NVR CGI operations
- `WebApiClient` for TVT HTTP Web API / LAPI operations
- `DeviceManager` for unified device actions across `netsdk` and `sdk_http`
- Direct SDK login helpers, including AutoNAT / P2P flows
- Connection pooling and `connect-many` orchestration for large fleets
- JSON / CSV / XLSX export and scan diffing

### Provisional management-server surface

- `ManagementClient` facade for management-server sessions
- `platform_sdk` runtime backed by `libPlatClientSDK.so` / `PlatClientSDK.dll`
- `native_linux_sdk`, `sidecar`, and `native_protocol` management runtime modes
- Read-only inventory, topology, health, alarm, and capability analysis modules

Management support is additive. It does not replace or downgrade the stable device/NVR flows.

## Python-only architecture

`pytvt` is permanently Python-only at the repository and packaging levels.

- No Node.js runtime is required for any supported CLI or library path.
- No JavaScript bridge, npm manifest, or Node build step is part of the supported runtime.
- Proprietary SDK binaries are never redistributed in this repository or in PyPI artifacts.
- Native SDK support remains optional and is loaded only from a user-supplied vendor installation.

Repository guardrails enforce this policy through tests, packaging checks, `.gitignore`, and a pre-commit hook.

## Runtime and SDK matrix

| Runtime family | What it powers | Requirement | Packaging policy |
|---|---|---|---|
| Pure Python | `protocol`, discovery, diffing, NVR CGI, Web API, most CLI flows | `pip install pytvt` | Included in wheel/sdist |
| Device SDK (`libdvrnetsdk.so`) | `sdk-local`, `DeviceManager` netsdk backend, `scan-nvr`, `connect`, `connect-many`, AutoNAT/P2P | User-supplied TVT device SDK on Linux x86-64/aarch64 | Never bundled |
| Platform SDK (`libPlatClientSDK.so` / `PlatClientSDK.dll`) | `ManagementClient(backend_mode="platform_sdk")` and related platform analysis | User-supplied NVMS PlatformSDK | Never bundled |
| External bridge/service | `sdk` / `sdk_http` compatibility path, management `sidecar` mode | External service/process you operate | Outside the package |

## SDK Installation Required

Native SDK features are optional. `pytvt` does not bundle, download, or vendor
proprietary SDK binaries or headers in this public repository.

SDK artifacts can be stored in a separate private/internal repository and loaded
at runtime via explicit paths or manifest-driven resolution.

To enable SDK-backed features, obtain the TVT SDK directly from the vendor and configure one of these inputs:

| Feature | Requirement | Configuration |
|---|---|---|
| `netsdk` | `libdvrnetsdk.so` on Linux x86-64/aarch64 | Set `TVT_SDK_PATH=/path/to/libdvrnetsdk.so` or `TVT_SDK_PATH=/path/to/sdk-root` |
| `sdk-local` | `libdvrnetsdk.so` on Linux x86-64/aarch64 | Set `TVT_SDK_PATH=/path/to/libdvrnetsdk.so` or `TVT_SDK_PATH=/path/to/sdk-root` |
| `platform_sdk` | `libPlatClientSDK.so` or `PlatClientSDK.dll` | Pass `platform_sdk_path="/path/to/libPlatClientSDK.so"` to `ManagementClient` |
| `sdk` / `sdk_http` | A compatible SDK bridge service | Set `TVT_API_URL=http://host:3000` |
| `sidecar` | External management sidecar process | Pass `sidecar_command=...` or set `PYTVT_MGMT_SIDECAR_CMD` |

Legacy environment variable `PYTVT_NETSDK_LIB` is still honored for existing developer workflows.

All SDK features fail gracefully with clear errors when dependencies are absent. The default `protocol` and `webapi` backends work with a plain `pip install`.

The SDK must always be supplied by the user through `TVT_SDK_PATH` or an explicit `sdk_path=` argument. `pytvt` does not download, embed, or vendor `libdvrnetsdk.so`, `libNatClientSDK.so`, or any other TVT binary.

### AutoNAT / P2P SDK Login

On Linux, the vendor SDK also exposes AutoNAT / P2P login through `NET_SDK_LoginEx(...)` with `NET_SDK_CONNECT_NAT` or `NET_SDK_CONNECT_NAT20`. `pytvt` now wraps that SDK-managed flow directly; it does not implement the NAT traversal protocol itself.

Use NAT mode when the recorder is not directly reachable by IP but is provisioned for the vendor's cloud / P2P path. AutoNAT still requires a local SDK installation with both `libdvrnetsdk.so` and `libNatClientSDK.so` present.

### Example SDK loader

```python
from pytvt.netsdk.loader import NetSdkUnavailable, load_sdk

try:
  sdk = load_sdk(sdk_path="/opt/tvt-sdk")
  print("SDK loaded:", sdk)
except NetSdkUnavailable as exc:
  print(f"SDK unavailable: {exc}")
```

Higher-level APIs accept the same explicit path:

```python
from pytvt import DeviceManager

with DeviceManager("192.0.2.10", "admin", "example-password", sdk_path="/opt/tvt-sdk") as mgr:
  print(mgr.device_info())
```

## What is pytvt?

`pytvt` is a Python toolkit for working with [TVT](https://en.tvt.net.cn/) (Shenzhen TVT Digital Technology) devices and TVT-operated management environments.

At the device and NVR level, it can discover recorders, bulk-scan channel inventories, query or modify configuration through NVR CGI and Web API paths, capture snapshots, and connect through the vendor SDK when that path is available.

At the management-server level, it exposes an additive `ManagementClient` plus platform analysis modules for inventory, topology, alarms, and backend diagnostics. That management layer is usable today, but it remains intentionally marked provisional while backend coverage continues to expand.

For downstream code, the package now centers around a few clear entrypoints:

- `scan_single_nvr()` and the `pytvt` scanner CLI for inventory-oriented fleet workflows
- `NvrClient` and `WebApiClient` for direct device configuration and data access
- `DeviceManager` for unified per-device operations across SDK-backed runtimes
- `ManagementClient` for management-server sessions and platform inventory analysis

## Stability and Scope

`pytvt` ships stable production device/NVR flows alongside an explicitly
provisional management-server package.

**Stable:**
- Scanner, discovery, `NvrClient`, `WebApiClient`, `DeviceManager`,
  connection-pool helpers, and netsdk-backed device operations

**Provisional (`pytvt.management`):**
- Management-server backend family and related validation tooling in `tools/`
- Runtime modes include `native_linux_sdk`, `platform_sdk`, `sidecar`,
  `native_protocol`, and `auto`
- `sidecar` is a first-class runtime mode for environments where the native
  Linux SDK is not available; some management operations are not yet implemented
- Management diagnostics and capability evidence are the primary outputs at
  this stage

Management-server support is strictly additive. It does not replace or regress
legacy device/NVR behavior.

### Platform SDK backend (v0.7.0+)

`ManagementClient(backend_mode="platform_sdk")` wraps TVT's
`libPlatClientSDK.so` / `PlatClientSDK.dll` through `ctypes` and exposes a
read-only view of a TVT management server: resources, areas, device and
channel inventories, server roster, alarm zones, and connection events.
Normalized outputs are typed dataclasses with sensitive fields redacted;
write operations default to `dry_run=True` and refuse real mutations.

A complementary set of pure-Python, read-only analysis modules —
`platform_topology`, `platform_health`, `platform_alarms`,
`platform_classification`, `platform_capabilities`, and
`platform_inventory` — compose on top of it to produce JSON-safe
operational snapshots. Credentials for the `tools/validate_platform_inventory.py`
live-test CLI are read from environment variables / `.env`, never from
command-line flags. See [`docs/platform_sdk.md`](docs/platform_sdk.md).

`pytvt` does not vendor SDK binaries. SDKs are loaded via explicit path
(`sdk_path=`) or manifest-aware tooling in `tools/`.

For maintainer guidance, see `docs/PACKAGE_OVERVIEW.md` and `docs/PUBLIC_SURFACE.md`.

## Architecture

`pytvt` is best understood as a Python package with two additive layers:

| Layer | Primary entrypoints | Typical targets |
|---|---|---|
| Device / NVR operations | `pytvt`, `pytvt-discover`, `pytvt-diff`, `pytvt-api`, `pytvt-snapshot`, `DeviceManager`, `NvrClient`, `WebApiClient` | NVRs, IPCs, single devices, fleet inventories |
| Management-server operations | `ManagementClient`, `platform_inventory`, `platform_health`, `platform_alarms`, `platform_capabilities`, `platform_topology` | TVT NVMS / platform deployments |

For device scanning specifically, the runtime choices are:

- `protocol`: pure Python TCP implementation, no external SDK needed
- `sdk`: compatibility path via an external SDK bridge service
- `sdk-local`: direct Python `ctypes` calls into the vendor device SDK
- `both`: protocol-first with SDK bridge fallback

## Why use it?

- **Pure Python distribution** — install from PyPI without bundling vendor binaries or Node tooling
- **Broad TVT surface area** — scanner, discovery, NVR CGI, Web API, SDK-backed device actions, and management-server analysis
- **Flexible runtime options** — pure Python where possible, explicit external SDK/service hooks where needed
- **Bulk operations** — scan dozens of NVRs in parallel, diff results, export per-site XLSX workbooks
- **Unified per-device API** — `DeviceManager` selects a workable backend without changing your higher-level code
- **Operational diagnostics** — inspect SDK readiness with `pytvt doctor` and backend diagnostics helpers

## Features

- Pure-Python TVT binary protocol client (standard XOR and head-variant SHA1 encryption)
- Native device SDK integration via an SDK bridge service or local Python `ctypes` calls
- TVT management-server integration via `ManagementClient` and PlatformSDK / sidecar runtime modes
- LAN device discovery via SSDP multicast
- Remote subnet sweep via unicast UDP + TCP port fingerprinting
- Concurrent bulk scanning with configurable parallelism
- Scan diffing / change detection between scan runs
- Output to console, CSV, JSON, or per-site XLSX
- Failed device tracking with automatic retry support
- Camera snapshot capture (SDK-based)
- Single-device `connect` and fleet-oriented `connect-many` SDK login workflows
- Runtime diagnostics through `diagnostics()` and `pytvt doctor`
- NVR web CGI client for configuration management:
  - Channel listing (`query_channels()`), port config, password security
  - LAN device discovery/management, channel add/delete, IPC password change
  - Platform Access (Auto Report) query and configuration
- **TVT HTTP Web API client (LAPI)** — per-device management via HTTP Basic auth:
  - Capability detection (GetSupportedAPIs)
  - Device info, channels, disks, date/time
  - NAT/P2P cloud relay configuration (`get_nat_config()`)
  - Password management
  - Image, video stream, audio stream, and OSD configuration
  - Snapshots with automatic fallback (Web API → RTSP)
  - Recording status and search

## Backend Comparison

| Backend | Flag | Requires | Platform | Best for |
|---|---|---|---|---|
| **protocol** | `--backend protocol` | Python only | Any | Default — works everywhere, no external deps |
| **sdk** | `--backend sdk` | SDK bridge service | Any | Compatibility mode |
| **sdk-local** | `--backend sdk-local` | Native SDK | Linux x86-64/aarch64 | Direct mode — pure Python ctypes |
| **both** | `--backend both` | SDK bridge service | Any | Protocol first, SDK fallback on failure |

## Management Runtime Modes

| Runtime mode | Requirement | Status | Notes |
|---|---|---|---|
| `native_linux_sdk` | Device SDK (`libdvrnetsdk.so`) | Provisional | Linux management path via NET_SDK family |
| `platform_sdk` | PlatformSDK (`libPlatClientSDK.so` / `PlatClientSDK.dll`) | Provisional but live-validated for read-only inventory flows | Best current path for NVMS inventory and diagnostics |
| `sidecar` | External sidecar bridge process | Provisional | Supported runtime mode for SDK-agnostic environments |
| `native_protocol` | None | Stub | Intentionally incomplete |
| `auto` | SDK path optional | Provisional | SDK-first, native-protocol fallback |

### Integration Modes

**Compatibility mode** (`sdk` backend) — uses a compatible SDK bridge service that wraps the native TVT SDK behind HTTP.

**Direct mode** (`sdk-local` backend) — calls the native `libdvrnetsdk.so` directly through Python `ctypes`. Requires Linux x86-64/aarch64 and a vendor-supplied SDK installation.

**Protocol mode** (default) — pure Python, no external dependencies. Works on any platform with TCP access to the NVR management port. Does not require the SDK at all.

Both integration paths are intentionally supported for compatibility.

## Quick Start

```bash
# Install
pip install -e .

# Scan NVRs from an inventory file
pytvt devices.json -u admin -p password

# Discover devices on the local LAN
pytvt-discover

# Discover and scan in one command
pytvt --discover -u admin -p password --xlsx files/

# Inspect SDK readiness
pytvt doctor --json

# Probe one NVR through the local SDK
pytvt scan-nvr 192.0.2.10 6036 admin example-password --sdk-path /opt/tvt-sdk --json

# Connect to a single device via direct SDK or AutoNAT
pytvt connect --host 192.0.2.10 -u admin -p example-password --sdk-path /opt/tvt-sdk
pytvt connect --nat --id DEMO-DEVICE-001 -u admin -p example-password --sdk-path /opt/tvt-sdk

# Connect to many devices concurrently
pytvt connect-many --file devices.json -u admin -p example-password --sdk-path /opt/tvt-sdk --json
```

## CLI Surface

Console scripts:

- `pytvt` — bulk scanner plus `scan-nvr`, `doctor`, `connect`, and `connect-many` subcommands
- `pytvt-discover` — device discovery
- `pytvt-diff` — scan diffing
- `pytvt-api` — NVR CGI operations
- `pytvt-snapshot` — snapshot capture
- `pytvt-scan` — direct alias for `pytvt scan-nvr`

## scan-nvr CLI

`scan-nvr` is the direct single-device SDK probe for the Python-only `sdk-local` path.

```bash
python -m pytvt scan-nvr 192.0.2.10 6036 admin example-password --sdk-path /opt/tvt-sdk
pytvt-scan 192.0.2.10 6036 admin example-password --sdk-path /opt/tvt-sdk --json --indent 2
```

Optional flags:

- `--timeout 15` — override the SDK connect/receive timeout in seconds
- `--json` — emit raw JSON only
- `--no-sentinels` — legacy alias for raw JSON output
- `--max-channels 128` — cap IPC enumeration

Expected output shape:

```json
{
  "nvr_ip": "192.0.2.10",
  "nvr_port": 6036,
  "success": true,
  "device_name": "NVR-01",
  "device_model": "TD-3332B4",
  "serial_number": "DEMO-DEVICE-001",
  "firmware": "5.2.3.190",
  "total_channels": 4,
  "cameras": [
    {
      "channel": 1,
      "name": "Lobby",
      "address": "198.51.100.10",
      "port": 9008,
      "status": "Online",
      "protocol": "TVT",
      "model": "TD-9544S4"
    }
  ],
  "error": null
}
```

For runtime diagnostics, use:

```bash
pytvt doctor
pytvt doctor --sdk-path /opt/tvt-sdk --json
```

## Web API Client (v0.3.0+)

The `WebApiClient` provides direct HTTP access to TVT devices (NVRs and IPCs) using the TVT LAPI protocol. Unlike the NVR CGI client (`NvrClient`), it uses HTTP Basic auth per request and targets individual devices.

```python
from pytvt.webapi import WebApiClient

client = WebApiClient("198.51.100.25", "admin", "example-password")

# Check what the device supports
apis = client.get_supported_apis()

# Device info
info = client.get_device_info()
print(f"{info.device_model} — FW {info.firmware_version}")

# Channels
for ch in client.get_channel_info():
    status = "online" if ch.online else "offline"
    print(f"  CH{ch.channel_id}: {ch.channel_name} ({status})")

# Snapshot (Web API with RTSP fallback)
result = client.get_snapshot(channel_id=1)
if result.success:
    with open("snapshot.jpg", "wb") as f:
        f.write(result.image_data)

# Change password
client.modify_password("old_pass", "new_pass")
```

The Web API service must be enabled on the device. Use `ensure_webapi_available()` to auto-enable it via the NVR CGI if needed:

```python
client = WebApiClient("198.51.100.25", "admin", "example-password")
client.ensure_webapi_available()  # enables via NvrClient if disabled
```

## DeviceManager (v0.5.0+)

The `DeviceManager` provides a unified facade for TVT device operations that automatically selects the best available backend:

1. **Native SDK** (`netsdk`) — direct ctypes calls to `libdvrnetsdk.so` on Linux x86_64/aarch64, including AutoNAT via `NET_SDK_LoginEx`
2. **SDK HTTP** (`sdk_http`) — HTTP calls to a compatible SDK bridge service (any platform)

```python
from pytvt import DeviceManager, available_backends

# Check what's available
print(available_backends(sdk_path="/opt/tvt-sdk"))

# Auto-detect best backend
with DeviceManager("192.0.2.25", "admin", "example-password", sdk_path="/opt/tvt-sdk") as mgr:
    print(f"Using: {mgr.backend}")

    info = mgr.device_info()
    print(f"{info.device_model} — SN {info.serial_number}")

    time = mgr.device_time()
    jpeg = mgr.snapshot(channel=0)
    url  = mgr.rtsp_url(channel=0)
    mgr.reboot()

# Force a specific backend
mgr = DeviceManager("192.0.2.25", "admin", "example-password", backend="sdk_http", api_url="http://localhost:3000")
```

AutoNAT uses the same facade. Pass a device serial / UID instead of an IP address:

```python
from pytvt import DeviceManager

with DeviceManager(
  None,
  "admin",
  "example-password",
  identifier="DEMO-DEVICE-001",
  sdk_path="/opt/tvt-sdk",
) as mgr:
  print(mgr.connection_method)
  print(mgr.device_info())
```

All methods return the same result types regardless of which backend is active. If no backend is available, `NoBackendAvailable` is raised.

## Installation

### Public PyPI install

```bash
pip install pytvt
```

Requires Python 3.10+ and network access to NVR port 6036. No native dependencies are needed for `protocol` or `webapi`.

### Enable device SDK features

```bash
export TVT_SDK_PATH=/opt/tvt-sdk
```

`TVT_SDK_PATH` can point either to `libdvrnetsdk.so` itself or to the root of the unpacked vendor SDK.

### Enable sdk-local scanning

```bash
export TVT_SDK_PATH=/opt/tvt-sdk
pytvt devices.json --backend sdk-local
```

### Single-NVR SDK scan

```bash
python -m pytvt scan-nvr 192.0.2.10 6036 admin example-password
pytvt-scan 192.0.2.10 6036 admin example-password --sdk-path /opt/tvt-sdk --json
```

### Enable PlatformSDK-backed management

```python
from pytvt import ManagementClient

client = ManagementClient(
  "198.51.100.25",
  backend_mode="platform_sdk",
  platform_sdk_path="/opt/tvt-platform-sdk/libPlatClientSDK.so",
)
client.login("admin", "example-password")
print(client.list_devices_normalized()[:3])
client.close()
```

For live validation guidance and the currently verified PlatformSDK capability matrix, see [`docs/platform_sdk.md`](docs/platform_sdk.md).

### Single-device Connect

```bash
# Direct SDK login
pytvt connect --host 192.0.2.25 -u admin -p example-password --sdk-path /opt/tvt-sdk

# AutoNAT / P2P login by device serial / UID
pytvt connect --nat --id DEMO-DEVICE-001 -u admin -p example-password --sdk-path /opt/tvt-sdk
```

Use `--nat-server` and `--nat-port` when your SDK build requires an explicit NAT2 endpoint override.

### Bulk Connect (connect-many)

Connect to many devices concurrently via direct or AutoNAT:

```bash
# devices.json: [{"ip": "192.0.2.10"}, {"identifier": "DEMO-DEVICE-001"}, ...]
pytvt connect-many --file devices.json -u admin -p example-password --sdk-path /opt/tvt-sdk

# Prefer NAT for devices with identifiers
pytvt connect-many --file devices.json -u admin -p example-password --nat --concurrency 20

# JSON output
pytvt connect-many --file devices.json -u admin -p example-password --json
```

Output table:

```
TARGET                         STATUS     LATENCY    METHOD     DEVICE               ERROR
----------------------------------------------------------------------------------------------
192.0.2.10                     OK         42ms       direct     NVR-01
DEMO-DEVICE-001                OK         320ms      nat        NVR-02
192.0.2.30                     FAIL       5001ms     direct                          Connection timed out
```

### Scaling AutoNAT Connections

The `ConnectionPool` class provides thread-safe session management for many devices:

```python
from pytvt.connection_pool import ConnectionPool

pool = ConnectionPool(
    sdk_path="/opt/tvt-sdk",
    max_active_sessions=50,    # hard cap on concurrent sessions
    idle_timeout=300,          # evict idle sessions after 5 min
    keepalive_interval=60,     # probe idle sessions every 60s
    reconnect_backoff=5.0,     # base seconds between reconnect retries
    prefer_nat=True,           # prefer NAT when identifier is available
)

# Acquire and reuse sessions
session = pool.acquire(ip="192.0.2.10", username="admin", password="example-password")
info = session.device_info()
pool.release(session)  # return to pool for reuse

# Session is reused on next acquire for same target
session2 = pool.acquire(ip="192.0.2.10", username="admin", password="example-password")
assert session2 is session  # same handle

pool.close()
```

For one-shot bulk operations, use `connect_many()`:

```python
from pytvt.connection_pool import connect_many
from pytvt.models import DeviceEntry

devices = [
    DeviceEntry(ip="192.0.2.10"),
    DeviceEntry(identifier="DEMO-DEVICE-001"),
    DeviceEntry(ip="192.0.2.30", identifier="DEMO-DEVICE-002", connection_preference="nat"),
]

results = connect_many(devices, "admin", "example-password", sdk_path="/opt/tvt-sdk", max_workers=20)
for r in results:
    print(f"{r.target}: {'OK' if r.success else 'FAIL'} via {r.connection_method} ({r.latency_ms}ms)")
```

**Limits of the vendor SDK:**
- The SDK runs one NAT channel per thread; set `max_workers` / `max_active_sessions` to match your host capacity (typically 50-100).
- NAT handshakes take 200-3000 ms depending on P2P path; budget `--timeout` accordingly (default: 10s).
- The SDK keepalive is opaque; `ConnectionPool` uses lightweight `device_info()` probes as a health check.
- SDK global state (`NET_SDK_Init`) is per-process; avoid running multiple pools in the same process.

### Enable an SDK bridge service

```bash
export TVT_API_URL=http://localhost:3000
pytvt devices.json --backend sdk
```

## Usage

### SDK Bridge Mode

```bash
# Scan via an SDK HTTP bridge service
pytvt devices.json --backend sdk --api-url http://localhost:3000

# Protocol first, SDK fallback
pytvt devices.json --backend both --api-url http://localhost:3000
```

### Direct Mode (sdk-local)

```bash
pytvt devices.json --backend sdk-local
```

### Protocol Mode (default)

```bash
# Scan with default protocol backend
pytvt devices.json

# Override credentials
pytvt devices.json -u admin -p mypassword

# Export to CSV
pytvt devices.json -o cameras.csv

# Export to JSON
pytvt devices.json -o cameras.json

# One XLSX per site
pytvt devices.json --xlsx files/

# Filter by site name
pytvt devices.json --site "Downtown"

# Limit concurrency
pytvt devices.json -c 2

# Also works as a module
python -m pytvt devices.json
```

### Discovery

```bash
# LAN multicast discovery
pytvt-discover

# Custom timeout
pytvt-discover --timeout 8 --retries 3

# JSON output
pytvt-discover --json

# Save as scanner-compatible inventory
pytvt-discover --scanner-json discovered.json --site "Office"

# Remote subnet sweep
pytvt-discover --subnet 198.51.100.0/24

# Multiple subnets
pytvt-discover --subnet 198.51.100.0/24 --subnet 198.51.101.0/24

# Discover + scan in one command
pytvt --discover --xlsx files/
pytvt --subnet 198.51.100.0/24 -o results.json
```

### Scan Diffing

Compare two scan result files to detect changes between runs:

```bash
# Human-readable diff report
pytvt-diff old_scan.json new_scan.json

# Machine-readable JSON output
pytvt-diff --json old_scan.json new_scan.json

# One-line summary
pytvt-diff --summary old_scan.json new_scan.json
```

The diff detects:
- **Devices added/removed** — NVRs appearing or disappearing between scans
- **Device metadata changes** — firmware upgrades, model changes, scan failures
- **Camera channel changes** — cameras added/removed/renamed, status changes (Online ↔ Offline), IP address changes
- **Failure drift** — devices that started or stopped failing

Devices are matched by `nvr_ip`. Cameras within a device are matched by `channel` index. Only cameras with IP addresses are compared.

**Console output example:**

```
================================================================================
  SCAN DIFF REPORT
================================================================================
  Old: monday.json
  New: friday.json
  Devices: 3 → 3
  Changes: 1 changed, 2 unchanged
================================================================================

  ~~ CHANGED DEVICES (1)
  ----------------------------------------------------------------------------
  ~ 192.0.2.100 / NVR-01 @ Site A
      firmware: '5.1.0' → '5.2.3'
      cameras: 29 → 30
      + ch 29: New Entrance Cam
      ~ ch 5 (Lobby): status: 'Offline' → 'Online'
```

**Library usage:**

```python
from pytvt import diff_scans, load_scan_file

old = load_scan_file("monday.json")
new = load_scan_file("friday.json")
diff = diff_scans(old, new)

for device in diff.devices_changed:
    print(f"{device.nvr_ip}: {len(device.field_changes)} field changes")
```

### CLI Reference

| Flag | Description |
|---|---|
| `input` | Path to NVR devices JSON file (optional with `--discover`) |
| `--config` | Path to config.json (default: `./config.json`) |
| `-o`, `--output` | Output file path (`.csv` or `.json`) |
| `-s`, `--site` | Filter by site name (partial match) |
| `-u`, `--username` | Override NVR username |
| `-p`, `--password` | Override NVR password |
| `-c`, `--concurrency` | Max parallel scans (default: from config) |
| `--backend` | `protocol` (default), `sdk`, `sdk-local`, or `both` |
| `--api-url` | SDK bridge URL for `sdk`/`both` (default: `http://localhost:3000`) |
| `--xlsx DIR` | Export one XLSX per site into DIR |
| `--failed FILE` | Save failed devices to JSON for retry |
| `--discover` | Run LAN discovery before scanning |
| `--discover-only` | Discover only, no scanning |
| `--discover-timeout` | Seconds per discovery probe (default: 5) |
| `--subnet CIDR` | Sweep a remote subnet (repeatable) |
| `--discover-concurrency` | Max parallel probes for subnet sweep (default: 50) |
| `--no-tcp-fallback` | Skip TCP port-probe fallback during sweep |

### Output Examples

**Console:**

```
================================================================================
  Site: Demo Site
  NVR:  NVR-01 (192.0.2.100)
================================================================================
  Device: NVR32 | Model: TD-3332B4
  Firmware: 5.2.3.19033B241010
  S/N: DEMO-DEVICE-100
  Total Channels: 29
  ----------------------------------------------------------------------------
  Ch   Camera Name                  Address            Port   Status   Model
  --   ---------------------------  -----------------  ----   ------   -----
  1    Lobby                        198.51.100.100    9008   Online   IP-5IRD4S4C4-28
  2    Parking                      198.51.100.101    9008   Online   IP-5IRD4S4C4-28

================================================================================
  SUMMARY: 2/2 NVRs scanned successfully, 58 total cameras found
================================================================================
```

**XLSX per-site:** One `.xlsx` file per site with NVR Config (cameras) and NVR Info (device summary) tabs. Existing tabs in the file are preserved.

### Input Format

JSON array of device objects. At minimum each needs `ip`:

```json
[
  {
    "site": "Site A Downtown",
    "ip": "192.0.2.100",
    "mac": "58:5B:69:AA:BB:01",
    "hostname": "NVR1",
    "manufacturer": "TVT"
  }
]
```

## Configuration

### Credentials

```bash
cp .env.example .env
# Edit .env with your NVR credentials
```

Precedence (first wins):

1. CLI flags (`-u`, `-p`)
2. Environment variables (`TVT_USERNAME`, `TVT_PASSWORD`)
3. `config.json` fields
4. Built-in defaults

### Settings (`config.json`)

```json
{
    "port": 6036,
    "timeout": 10,
    "max_channels": 64,
    "concurrency": 4
}
```

| Field | Default | Description |
|---|---|---|
| `port` | 6036 | TVT protocol port |
| `timeout` | 10 | Connection timeout (seconds) |
| `max_channels` | 64 | Max camera channels per NVR |
| `concurrency` | 4 | Parallel NVR scans |

Environment variables override config file values: `TVT_PORT`, `TVT_TIMEOUT`, `TVT_MAX_CHANNELS`, `TVT_CONCURRENCY`, `TVT_SDK_PATH`.

## Project Structure

```
pytvt/
├── src/pytvt/                 # Installable Python package (supported runtime)
│   ├── __init__.py            # Public API + version
│   ├── __main__.py            # python -m pytvt support
│   ├── cli.py                 # CLI entry points (pytvt, pytvt-discover, etc.)
│   ├── config.py              # Configuration loading + precedence
│   ├── constants.py           # Enums, ExecutionPlan, backend resolution
│   ├── exceptions.py          # Base exception hierarchy
│   ├── models.py              # Dataclasses: ScannerConfig, DeviceEntry, CameraInfo, ScanResult
│   ├── registry.py            # Backend/integration mode dispatch table
│   ├── scanner.py             # Backend dispatch + scan orchestration
│   ├── protocol.py            # Pure Python TVT binary protocol client
│   ├── discovery.py           # SSDP multicast + subnet sweep discovery
│   ├── diff.py                # Scan diffing / change detection
│   ├── sdk_http.py            # SDK HTTP backend (compat mode → external bridge)
│   ├── sdk_http_client.py     # Typed SDK HTTP client
│   ├── device_manager.py      # Unified facade with auto-backend selection
│   ├── sdk_local.py           # SDK local backend (direct ctypes mode)
│   ├── output.py              # CSV / JSON / XLSX formatters
│   ├── nvr_api.py             # NVR web CGI client
│   ├── snapshot.py            # Camera snapshot capture
│   └── netsdk/                # Native SDK ctypes bindings (Linux only)
│       ├── __init__.py        # Package exports
│       ├── loader.py          # Library search + loading
│       ├── bindings.py        # ctypes function prototypes
│       ├── types.py           # ctypes structure definitions
│       ├── constants.py       # Enums (PtzCommand, StreamType, etc.)
│       └── client.py          # NetSdkClient + DeviceSession
├── tvt-api/                   # Local-only SDK bridge workspace (excluded from PyPI)
├── tests/                     # pytest test suite
│   ├── conftest.py            # Shared fixtures + sample data
│   ├── test_protocol.py       # Protocol packet/encryption tests
│   ├── test_architecture.py   # Enum, registry, execution plan, import boundary tests
│   ├── test_config.py         # Config precedence tests
│   ├── test_models.py         # Model construction + serialization
│   ├── test_scanner.py        # Backend dispatch + device loading
│   ├── test_output.py         # Output format tests
│   ├── test_discovery.py      # XML parsing + discovery
│   ├── test_diff.py           # Scan diffing + CLI tests
│   ├── test_cli.py            # CLI parser + helpers
│   ├── test_sdk_http.py       # SDK HTTP backend (mocked)
│   ├── test_sdk_http_client.py # SdkHttpClient tests
│   ├── test_netsdk_client.py  # netsdk ctypes binding tests
│   ├── test_device_manager.py # DeviceManager facade tests
│   └── test_sdk_local.py      # Direct SDK local backend tests
├── tools/                     # Operational utilities (import from pytvt)
│   └── enable_nvr_services.py # Batch NVR RTSP + API enablement
├── research/                  # Reverse-engineering reference (NOT runtime code)
│   ├── README.md              # Context and usage notes
│   ├── parse_pcap.py          # Extract TVT init/login packets from pcap
│   ├── verify_pw.py           # Verify password encryption against live NVR
│   ├── verify_capture.py      # Verify encryption from captured SDK traffic
│   ├── capture_sdk.sh         # Capture SDK ↔ NVR traffic via tcpdump
│   ├── sdk_login.py           # Trigger SDK login for traffic capture
│   └── test_sha1.py           # Test SDK PUB_SHA1Encrypt directly
├── pyproject.toml             # Packaging (hatchling) + tool config
├── config.json                # Default non-sensitive settings
├── .env.example               # Credential template
├── .github/
│   ├── workflows/ci.yml       # CI: pytest + ruff (Python 3.10–3.13)
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── ISSUE_TEMPLATE/
└── CONTRIBUTING.md
```

## Supported vs Experimental Code

pytvt distinguishes between three tiers of code:

### Published package (`src/pytvt/`)

The PyPI wheel and sdist ship only the Python package in `src/pytvt/`. This is the supported public runtime.

### Local helper runtimes (`tvt-api/`)

These folders support developer workflows only. They are excluded from PyPI artifacts and may rely on separate, vendor-supplied SDK installations.

### Research / reference (`research/`)

Standalone scripts from the protocol reverse-engineering process. These scripts were used to discover and validate the TVT binary protocol — nonce exchange, password encryption, login packet structure. The findings are implemented in `src/pytvt/protocol.py`.

Research code is **not** imported by any runtime module. This boundary is enforced by a test (`test_architecture.py::TestImportBoundary`). The scripts are preserved as runnable documentation of *how* the protocol was reverse engineered.

## Development

### Setup

```bash
git clone https://github.com/dannielperez/pytvt.git
cd pytvt
pip install -e ".[dev]"
```

### Testing

```bash
pytest                    # run all tests
pytest -v                 # verbose
pytest tests/test_protocol.py  # single module
```

### Linting

```bash
ruff check src/ tests/    # lint
ruff format src/ tests/   # format
mypy src/pytvt/           # type check (optional)
```

### Using as a library

```python
from pytvt import load_config, load_devices, scan_single_nvr, filter_tvt_devices

config = load_config("config.json")
devices = filter_tvt_devices(load_devices("devices.json"))

for device in devices:
    result = scan_single_nvr(device, config, backend="protocol")
    if result.success:
        print(f"{result.device_name}: {result.camera_count} cameras")
```

## Limitations

- **Protocol backend:** Retrieves camera channel metadata only — no video streaming or recording
- **Head-variant NVRs** (protocolVer ≥ 11): Return basic channel data (name, status) but not IPC IP addresses
- **SDK backends:** Native SDK (`libdvrnetsdk.so`) is Linux x86-64/aarch64 only
- **sdk-local backend:** Requires the SDK shared library on the host and runs through Python `ctypes`
- **Discovery:** SSDP multicast is limited to the local broadcast domain; remote subnets need `--subnet`
- **No TLS:** The TVT protocol does not support encryption in transit
- **Credential handling:** Passwords are XOR-encrypted on the wire (standard variant) — treat the NVR management network as trusted

## Related Projects

| Project | Description |
|---|---|
| [2BAD/tvt](https://github.com/2BAD/tvt) | Original TVT protocol reverse engineering (TypeScript) |

## License

[MIT License](LICENSE). See [CHANGELOG.md](CHANGELOG.md) for release history.

Starting from version 1.0.0, this project is licensed under the MIT License. Previous versions were licensed under AGPLv3.
