# pytvt

Bulk-scan TVT NVRs and enumerate every connected IP camera — pure Python, no vendor tools required.

## Installation

```bash
pip install pytvt
```

Requires Python 3.10+. The published wheel contains only pure Python code.

## SDK Installation Required

Native SDK features are optional. `pytvt` does not bundle, download, or reference proprietary SDK binaries, header files, or private repositories.

To enable SDK-backed features, obtain the TVT SDK directly from the vendor and configure one of these inputs:

| Feature | Requirement | Configuration |
|---|---|---|
| `netsdk` | `libdvrnetsdk.so` on Linux x86-64/aarch64 | Set `TVT_SDK_PATH=/path/to/libdvrnetsdk.so` or `TVT_SDK_PATH=/path/to/sdk-root` |
| `sdk-local` | Node.js 18+ and a local `scan_nvr.mjs` bridge | Set `TVT_SCAN_SCRIPT=/path/to/scan_nvr.mjs`; set `TVT_SDK_PATH` if the bridge also needs the SDK location |
| `sdk` / `sdk_http` | A compatible SDK bridge service | Set `TVT_API_URL=http://host:3000` |

Legacy environment variables `PYTVT_NETSDK_LIB` and `PYTVT_SCAN_SCRIPT` are still honored for existing developer workflows.

All SDK features fail gracefully with clear errors when dependencies are absent. The default `protocol` and `webapi` backends work with a plain `pip install`.

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

with DeviceManager("10.0.0.1", "admin", "password", sdk_path="/opt/tvt-sdk") as mgr:
  print(mgr.device_info())
```

## What is pytvt?

`pytvt` is a Python toolkit for managing [TVT](https://en.tvt.net.cn/) (Shenzhen TVT Digital Technology) NVR and IPC devices at scale. It connects to NVRs, authenticates via the proprietary binary protocol, and retrieves full camera channel inventories — names, IPs, ports, models, online status — across dozens of sites in parallel.

It also supports LAN auto-discovery via SSDP multicast and remote subnet sweeps, so you can find devices without an inventory file.

Since v0.3.0, pytvt includes a **Web API client** for the TVT HTTP API (LAPI protocol), providing direct per-device management via HTTP Basic auth — device info, snapshots, password changes, image/stream configuration, and recording queries — all in pure Python with no binary dependencies.

Since v0.5.0, the **`DeviceManager`** provides a unified facade for device operations that auto-selects the best available backend — native SDK (`netsdk` ctypes) on Linux, or a compatible SDK bridge over HTTP — so the same code works on any platform.

## Architecture

```
                       ┌───────────────────────────────────────────┐
                       │              pytvt (Python)               │
                       │                                           │
  Inventory JSON ─────▶│  CLI / Library API                        │
  or --discover        │    │                                      │
                       │    ├─ protocol (default)                  │
                       │    │    Pure Python TCP client ──────────▶│── NVR :6036
                       │    │                                      │
                       │    ├─ sdk (compat mode)                   │
                       │    │    HTTP POST ──▶ SDK bridge service ─▶│── NVR :6036
                       │    │                                      │
                       │    └─ sdk-local (direct mode)             │
                       │         Node subprocess ─▶ libdvrnetsdk ─▶│── NVR :6036
                       │                                           │
                       │  Output: console / CSV / JSON / XLSX      │
                       └───────────────────────────────────────────┘
```

The scanner supports three backends (integration modes), each suited to different deployment scenarios. You can also combine `protocol` with `sdk` fallback using `--backend both`.

## Why use it?

- **No vendor software required** — the default `protocol` backend is pure Python with zero native dependencies
- **Bulk operations** — scan dozens of NVRs in parallel, export per-site XLSX workbooks
- **Multiple backends** — choose the integration mode that fits your environment
- **Auto-discovery** — find TVT devices via SSDP multicast or remote subnet sweep
- **Typed data models** — clean `dataclass`-based API for programmatic use
- **CLI + library** — use from the command line or import as a Python package

## Features

- Pure-Python TVT binary protocol client (standard XOR and head-variant SHA1 encryption)
- Native SDK integration via an SDK bridge service or local subprocess
- LAN device discovery via SSDP multicast
- Remote subnet sweep via unicast UDP + TCP port fingerprinting
- Concurrent bulk scanning with configurable parallelism
- Scan diffing / change detection between scan runs
- Output to console, CSV, JSON, or per-site XLSX
- Failed device tracking with automatic retry support
- Camera snapshot capture (SDK-based)
- NVR web CGI client for configuration management
- **TVT HTTP Web API client (LAPI)** — per-device management via HTTP Basic auth:
  - Capability detection (GetSupportedAPIs)
  - Device info, channels, disks, date/time
  - Password management
  - Image, video stream, audio stream, and OSD configuration
  - Snapshots with automatic fallback (Web API → RTSP)
  - Recording status and search

## Backend Comparison

| Backend | Flag | Requires | Platform | Best for |
|---|---|---|---|---|
| **protocol** | `--backend protocol` | Python only | Any | Default — works everywhere, no external deps |
| **sdk** | `--backend sdk` | SDK bridge service | Any | Compatibility mode |
| **sdk-local** | `--backend sdk-local` | Node.js 18+ + native SDK | Linux x86-64 only | Direct mode — no Docker required |
| **both** | `--backend both` | SDK bridge service | Any | Protocol first, SDK fallback on failure |

### Integration Modes

**Compatibility mode** (`sdk` backend) — uses a compatible SDK bridge service that wraps the native TVT SDK behind HTTP.

**Direct mode** (`sdk-local` backend) — runs a local `scan_nvr.mjs` bridge as a Node.js subprocess, calling the native `libdvrnetsdk.so` directly. Requires Linux x86-64, Node.js 18+, and a vendor-supplied SDK installation.

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
```

## Web API Client (v0.3.0+)

The `WebApiClient` provides direct HTTP access to TVT devices (NVRs and IPCs) using the TVT LAPI protocol. Unlike the NVR CGI client (`NvrClient`), it uses HTTP Basic auth per request and targets individual devices.

```python
from pytvt.webapi import WebApiClient

client = WebApiClient("192.168.1.100", "admin", "password")

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
client = WebApiClient("192.168.1.100", "admin", "password")
client.ensure_webapi_available()  # enables via NvrClient if disabled
```

## DeviceManager (v0.5.0+)

The `DeviceManager` provides a unified facade for TVT device operations that automatically selects the best available backend:

1. **Native SDK** (`netsdk`) — direct ctypes calls to `libdvrnetsdk.so` on Linux x86_64/aarch64
2. **SDK HTTP** (`sdk_http`) — HTTP calls to a compatible SDK bridge service (any platform)

```python
from pytvt import DeviceManager, available_backends

# Check what's available
print(available_backends(sdk_path="/opt/tvt-sdk"))

# Auto-detect best backend
with DeviceManager("10.200.50.251", "admin", "password", sdk_path="/opt/tvt-sdk") as mgr:
    print(f"Using: {mgr.backend}")

    info = mgr.device_info()
    print(f"{info.device_model} — SN {info.serial_number}")

    time = mgr.device_time()
    jpeg = mgr.snapshot(channel=0)
    url  = mgr.rtsp_url(channel=0)
    mgr.reboot()

# Force a specific backend
mgr = DeviceManager("10.200.50.251", "admin", "password", backend="sdk_http", api_url="http://localhost:3000")
```

All methods return the same result types regardless of which backend is active. If no backend is available, `NoBackendAvailable` is raised.

## Installation

### Public PyPI install

```bash
pip install pytvt
```

Requires Python 3.10+ and network access to NVR port 6036. No native dependencies are needed for `protocol` or `webapi`.

### Enable native SDK features

```bash
export TVT_SDK_PATH=/opt/tvt-sdk
```

`TVT_SDK_PATH` can point either to `libdvrnetsdk.so` itself or to the root of the unpacked vendor SDK.

### Enable sdk-local

```bash
export TVT_SCAN_SCRIPT=/opt/pytvt-bridges/scan_nvr.mjs
pytvt devices.json --backend sdk-local
```

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
pytvt-discover --subnet 10.200.50.0/24

# Multiple subnets
pytvt-discover --subnet 10.200.50.0/24 --subnet 10.200.51.0/24

# Discover + scan in one command
pytvt --discover --xlsx files/
pytvt --subnet 10.200.50.0/24 -o results.json
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
  ~ 10.10.10.100 / NVR-01 @ Site A
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
  NVR:  NVR-01 (10.10.10.100)
================================================================================
  Device: NVR32 | Model: TD-3332B4
  Firmware: 5.2.3.19033B241010
  S/N: ABC123456789
  Total Channels: 29
  ----------------------------------------------------------------------------
  Ch   Camera Name                  Address            Port   Status   Model
  --   ---------------------------  -----------------  ----   ------   -----
  1    Lobby                        192.168.1.100      9008   Online   IP-5IRD4S4C4-28
  2    Parking                      192.168.1.101      9008   Online   IP-5IRD4S4C4-28

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
    "ip": "10.10.10.100",
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

Environment variables override config file values: `TVT_PORT`, `TVT_TIMEOUT`, `TVT_MAX_CHANNELS`, `TVT_CONCURRENCY`, `TVT_SDK_PATH`, `TVT_SCAN_SCRIPT`.

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
│   ├── sdk_local.py           # SDK local subprocess backend (direct mode)
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
├── bridges/
│   └── sdk_local/             # Local-only Node.js SDK bridge (excluded from PyPI)
│       ├── scan_nvr.mjs       # SDK FFI subprocess script
│       └── package.json       # koffi dependency
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
│   └── test_sdk_local.py      # JSON extraction tests
├── tools/                     # Operational utilities (import from pytvt)
│   └── enable_nvr_services.py # Batch NVR RTSP + API enablement
├── research/                  # Reverse-engineering reference (NOT runtime code)
│   ├── README.md              # Context and usage notes
│   ├── parse_pcap.py          # Extract TVT init/login packets from pcap
│   ├── verify_pw.py           # Verify password encryption against live NVR
│   ├── verify_capture.py      # Verify encryption from captured SDK traffic
│   ├── capture_sdk.sh         # Capture SDK ↔ NVR traffic via tcpdump
│   ├── sdk_login.cjs          # Trigger SDK login for traffic capture
│   └── test_sha1.mjs          # Test SDK PUB_SHA1Encrypt directly
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

### Local helper runtimes (`bridges/`, `tvt-api/`)

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
- **SDK backends:** Native SDK (`libdvrnetsdk.so`) is Linux x86-64 only
- **sdk-local backend:** Requires Node.js and the SDK shared library on the host
- **Discovery:** SSDP multicast is limited to the local broadcast domain; remote subnets need `--subnet`
- **No TLS:** The TVT protocol does not support encryption in transit
- **Credential handling:** Passwords are XOR-encrypted on the wire (standard variant) — treat the NVR management network as trusted

## Related Projects

| Project | Description |
|---|---|
| [dannielperez/tvt](https://github.com/dannielperez/tvt) | Fork of 2BAD/tvt — TypeScript SDK FFI bindings |
| [2BAD/tvt](https://github.com/2BAD/tvt) | Original TVT protocol reverse engineering (TypeScript) |
| [koffi](https://koffi.dev/) | FFI bindings used by the SDK bridge |

## License

[MIT License](LICENSE). See [CHANGELOG.md](CHANGELOG.md) for release history.

Starting from version 1.0.0, this project is licensed under the MIT License. Previous versions were licensed under AGPLv3.
