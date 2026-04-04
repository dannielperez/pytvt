# pytvt

A Python toolset for bulk-scanning [TVT](https://en.tvt.net.cn/) (Shenzhen TVT Digital Technology Co., Ltd) NVRs and enumerating every programmed IP camera. Supports two independent backends — a pure-Python binary protocol client and a native SDK bridge via HTTP API — that can be used alone or together.

Built on the reverse-engineering work from [2BAD/tvt](https://github.com/2BAD/tvt) (TypeScript) and extended with a native Python protocol client.

## Features

- **Pure-Python protocol client** — speaks the TVT binary protocol directly over TCP (port 6036), supporting both standard (XOR) and head-variant (SHA1) login encryption, plus HTTP-tunnelled API calls
- **Native SDK via HTTP API** — optional [tvt-api](https://github.com/dannielperez/tvt-api) backend that wraps the vendor's `libdvrnetsdk.so` in a Fastify HTTP service (Docker, linux/amd64)
- **Native SDK local** — alternative `sdk-local` backend that runs the SDK bridge (`scan_nvr.mjs`) as a subprocess (requires Node.js + the SDK natively on Linux x86-64)
- **LAN auto-discovery** — finds TVT devices on the local network via SSDP multicast (no inventory file needed)
- **Remote subnet sweep** — discovers TVT devices on routable subnets (e.g. CCTV VLANs) via unicast UDP probes + TCP port fingerprinting
- **Bulk scanner** — scan dozens of NVRs in parallel and extract every programmed camera channel (name, IP, port, status, model)
- **Multiple output formats** — console, CSV, JSON, or per-site XLSX workbooks
- **Flexible backend selection** — `protocol`, `sdk`, `sdk-local`, or `both` (protocol with SDK fallback)

## Related Repositories

| Repo | Description |
|---|---|
| [dannielperez/tvt](https://github.com/dannielperez/tvt) | Fork of 2BAD/tvt TypeScript library — TVT SDK FFI bindings (git submodule) |
| [dannielperez/tvt-api](https://github.com/dannielperez/tvt-api) | Fastify HTTP API wrapping the TVT SDK — Docker-based, linux/amd64 |

## How It Works

### Backends

#### 1. Python Protocol Client (`--backend protocol`, default)

Connects directly to TVT devices over TCP using the proprietary binary protocol:

1. **Handshake** — receives encryption parameters (nonce, protocol version, encryption type)
2. **Login** — authenticates with encrypted credentials (standard XOR or head-variant SHA1, auto-detected)
3. **Query** — retrieves device info and camera list via HTTP-tunnelled requests
4. **Logout** — cleanly disconnects

No external dependencies beyond Python. Works on any platform with TCP access to the NVR.

#### 2. SDK via HTTP API (`--backend sdk`)

Sends scan requests to the [tvt-api](https://github.com/dannielperez/tvt-api) Fastify server, which wraps the native TVT SDK (`libdvrnetsdk.so`). The API runs in Docker (linux/amd64):

```
main.py ──POST /scan──▶ tvt-api (Docker) ──FFI──▶ libdvrnetsdk.so ──▶ NVR
```

Start the API first:

```bash
# From the tvt-api repo:
docker build -t tvt-api .
docker run --rm -p 3000:3000 tvt-api
```

Then scan:

```bash
python3 main.py devices.json --backend sdk --api-url http://localhost:3000
```

#### 3. SDK Local Subprocess (`--backend sdk-local`)

Runs `scan_nvr.mjs` via Node.js subprocess. Requires the native SDK library on the host (Linux x86-64 only):

```bash
python3 main.py devices.json --backend sdk-local
```

#### 4. Combined (`--backend both`)

Tries the Python protocol first; on failure, falls back to the SDK HTTP API:

```bash
python3 main.py devices.json --backend both --api-url http://localhost:3000
```

### LAN Auto-Discovery (`tvt_discovery.py`)

Discovers TVT NVRs, IPCs, DVRs and other devices on the local network without needing an inventory file. Uses the same SSDP/UPnP multicast protocol as TVT's official IPTool application:

1. Sends an `M-SEARCH` probe to multicast group `239.255.255.250:1900`
2. TVT devices respond with an XML body (`<multicastSearchResult>`) containing IP, MAC, model, firmware, ports, etc.
3. Responses are parsed and de-duplicated by MAC address

The discovery protocol was reverse-engineered from the IPTool.app macOS binary and confirmed against the official `DVR_NET_SDK.h` header (`SEARCHED_DEVICE_INFO` struct, `IPTool_SearchDataCallBack` callback).

```
tvt_discovery.py ──M-SEARCH──▶ 239.255.255.250:1900
                                    │
                              TVT devices respond
                              with XML device info
                                    │
                                    ▼
                        parsed device list (table / JSON)
```

#### Remote Subnet Sweep

SSDP multicast is limited to the local broadcast domain — routers don't forward it. For remote routable subnets (e.g. a 10.200.50.0/24 CCTV VLAN), the discovery tool switches to a two-phase unicast approach:

1. **UDP unicast** — sends the same M-SEARCH probe directly to each host's port 1900 using a ThreadPoolExecutor (default 50 parallel probes)
2. **TCP fallback** — hosts that don't reply to UDP get a TCP connection attempt on port 9008 (the TVT data port); if the device sends an init handshake packet (`head` or `1111` magic bytes), it's flagged as a TVT device

```
tvt_discovery.py --subnet 10.200.50.0/24
    │
    ├──UDP M-SEARCH──▶ 10.200.50.1:1900
    ├──UDP M-SEARCH──▶ 10.200.50.2:1900
    ├──  ...254 hosts in parallel...     ──▶ XML response? → full device info
    │
    └──TCP fallback on non-responders:
        ├──TCP :9008──▶ 10.200.50.3  ──▶ TVT handshake? → minimal device entry
        └──TCP :9008──▶ 10.200.50.5  ──▶ connection refused → skip
```

### Orchestrator (`main.py`)

```
nvr_devices.json ──▶ main.py ──▶ backend (protocol / sdk / sdk-local / both)
                                        │
                                        ▼
                                  console / CSV / JSON / XLSX
```

1. Reads a JSON inventory of devices
2. Filters to TVT devices (by manufacturer field or MAC OUI prefix `58:5B:69`)
3. De-duplicates by IP
4. Scans each NVR in parallel using the chosen backend
5. Outputs results to console, CSV, JSON, or per-site XLSX

## Requirements

### Protocol-only (default)

- **Python 3.10+**
- `pip install -r requirements.txt`
- Network access to NVR management port (default: `6036`)

### SDK via HTTP API (`--backend sdk`)

Everything above, plus:

- **Docker** (for running [tvt-api](https://github.com/dannielperez/tvt-api))
- Network access from the tvt-api container to NVR port `6036`

### SDK Local (`--backend sdk-local`)

Everything above, plus:

- **Node.js 18+**
- **Linux x86-64** (the native SDK library is x86-64 only)
- `npm install` (installs koffi FFI bindings)

## Installation

### Protocol-only (simplest)

```bash
git clone https://github.com/dannielperez/pytvt.git
cd pytvt
pip install -r requirements.txt
```

### With SDK HTTP API support

```bash
git clone https://github.com/dannielperez/pytvt.git
cd pytvt
pip install -r requirements.txt

# In a separate directory, set up the API:
git clone --recurse-submodules https://github.com/dannielperez/tvt-api.git
cd tvt-api
docker build -t tvt-api .
docker run --rm -p 3000:3000 tvt-api
```

### With SDK Local support (Linux x86-64 only)

```bash
git clone --recurse-submodules https://github.com/dannielperez/pytvt.git
cd pytvt
pip install -r requirements.txt
npm install
```

If you cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
npm install
```

## Configuration

### Credentials

Copy the example environment file and fill in your NVR credentials:

```bash
cp .env.example .env
```

`.env`:

```
TVT_USERNAME=admin
TVT_PASSWORD=your_password_here
```

Credentials are resolved in this order (first wins):

1. CLI flags (`-u`, `-p`)
2. Environment variables (`TVT_USERNAME`, `TVT_PASSWORD`)
3. `config.json` fields (`username`, `password`)

### Settings

Edit `config.json` for non-sensitive settings:

```json
{
    "port": 6036,
    "timeout": 10,
    "max_channels": 64,
    "concurrency": 4
}
```

| Field | Description | Default |
|---|---|---|
| `port` | TVT protocol port | `6036` |
| `timeout` | Connection timeout (seconds) | `10` |
| `max_channels` | Max camera channels to query per NVR | `64` |
| `concurrency` | Number of NVRs to scan in parallel | `4` |

## Usage

### Bulk Scanner

```bash
# Scan all TVT NVRs from an inventory file (protocol backend)
python3 main.py nvr_devices.json

# Use the SDK HTTP API backend
python3 main.py nvr_devices.json --backend sdk --api-url http://localhost:3000

# Use both backends (protocol first, SDK fallback)
python3 main.py nvr_devices.json --backend both --api-url http://localhost:3000

# Export to CSV
python3 main.py nvr_devices.json -o cameras.csv

# Export to JSON
python3 main.py nvr_devices.json -o cameras.json

# Export one XLSX per site into files/
python3 main.py nvr_devices.json --xlsx files/

# Filter by site name
python3 main.py nvr_devices.json --site "Site A"

# Override credentials
python3 main.py nvr_devices.json -u admin -p mypassword

# Limit concurrency
python3 main.py nvr_devices.json -c 2
```

### CLI Options

| Flag | Description |
|---|---|
| `input` | Path to NVR devices JSON file (optional with `--discover`) |
| `--config` | Path to config.json (default: `./config.json`) |
| `-o`, `--output` | Output file (`.csv` or `.json`) |
| `-s`, `--site` | Filter by site name (partial match) |
| `-u`, `--username` | Override NVR username |
| `-p`, `--password` | Override NVR password |
| `-c`, `--concurrency` | Max parallel scans |
| `--backend` | `protocol` (default), `sdk`, `sdk-local`, or `both` |
| `--api-url` | tvt-api URL for `sdk`/`both` backends (default: `http://localhost:3000`) |
| `--xlsx DIR` | Export one `.xlsx` file per site into `DIR` |
| `--failed FILE` | Save devices that failed to scan to a JSON file for retry |
| `--discover` | Run LAN discovery first, merge found devices with input file, then scan |
| `--discover-only` | Only discover devices on the LAN (no scanning) |
| `--discover-timeout` | Seconds to wait for discovery responses per probe (default: `5`) |
| `--subnet CIDR` | Sweep a remote subnet for TVT devices (repeatable) |
| `--discover-concurrency` | Max parallel probes for subnet sweep (default: `50`) |
| `--no-tcp-fallback` | Skip TCP port-probe fallback during subnet sweep |

### LAN Discovery

```bash
# Discover all TVT devices on the local network
python3 tvt_discovery.py

# Custom timeout and retries
python3 tvt_discovery.py --timeout 8 --retries 3

# Output as JSON
python3 tvt_discovery.py --json

# Save discovered NVRs in scanner-compatible JSON format
python3 tvt_discovery.py --scanner-json discovered_nvrs.json --site "Office"

# Sweep a remote CCTV subnet for TVT devices
python3 tvt_discovery.py --subnet 10.200.50.0/24

# Sweep multiple subnets
python3 tvt_discovery.py --subnet 10.200.50.0/24 --subnet 10.200.51.0/24

# Sweep with faster timeout (1s per host) and more parallelism
python3 tvt_discovery.py --subnet 10.200.50.0/24 --timeout 1 --concurrency 100

# Sweep without TCP fallback (UDP only)
python3 tvt_discovery.py --subnet 10.200.50.0/24 --no-tcp-fallback

# Discover and scan in one command (via main.py)
python3 main.py --discover-only
python3 main.py --discover --backend protocol --xlsx files/
python3 main.py devices.json --discover --xlsx files/  # merge with existing inventory

# Sweep a subnet and scan all found NVRs
python3 main.py --subnet 10.200.50.0/24 --xlsx files/
python3 main.py --subnet 10.200.50.0/24 --subnet 10.200.51.0/24 -o results.json
python3 main.py devices.json --subnet 10.200.50.0/24 --discover --xlsx files/  # merge all
```

### Python Protocol Client (standalone)

```bash
# Scan a single NVR directly
python3 tvt_protocol.py 192.168.1.100
```

### Input Format

A JSON array of device objects. At minimum each entry needs `ip`; the scanner filters by manufacturer or MAC:

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

### Sample Output

**Console:**

```
================================================================================
  Site: Demo Site
  NVR:  NVR (10.10.10.100)
================================================================================
  Device: NVR32 | Model: TD-3332B4
  Firmware: 5.2.3.19033B241010
  S/N: ABC123456789
  Total Channels: 29
  ----------------------------------------------------------------------------
  Ch   Camera Name                  Address            Port   Status   Model
  --   ---------------------------  -----------------  ----   ------   -----
  1    Camera1                      192.168.1.100      9008   Online   IP-5IRD4S4C4-28
  2    Camera2                      192.168.1.102      9008   Online   IP-5IRD4S4C4-28
  3    Camera3                      192.168.1.103      9008   Online   IP-5IRD4S4C4-28
  ...

================================================================================
  SUMMARY: 2/2 NVRs scanned successfully, 58 total cameras found
================================================================================
```

### XLSX Per-Site Export

When using `--xlsx`, the scanner creates one `.xlsx` file per site. Each file contains two tabs:

**NVR Config** — all cameras from every NVR at that site:

| Camera Name | Address | Port | Status | Protocol | Model |
|---|---|---|---|---|---|
| Lobby | 192.168.1.100 | 9008 | Online | IP CAM | IP-5IRD4S4C4-28 |
| Parking | 192.168.1.101 | 80 | Online | HIKVISION | ECI-D12F2 |

**NVR Info** — summary of each NVR at that site:

| NVR Name | IP | Model | Serial Number | Firmware | Cameras |
|---|---|---|---|---|---|
| NVR32 | 10.10.10.100 | TD-3332B4 | ABC123 | 5.2.3.190 | 29 |

If the `.xlsx` file already exists, only the NVR Config and NVR Info tabs are replaced — other tabs are preserved.

```bash
python3 main.py nvr_devices.json --xlsx files/
```

### Retry Failed Devices

Any device that fails to scan is automatically saved to `files/failed_devices.json`. Re-run against just those devices:

```bash
# First scan
python3 main.py nvr_devices.json --xlsx files/

# Retry only the failures
python3 main.py files/failed_devices.json --xlsx files/

# Custom path for failed list
python3 main.py nvr_devices.json --xlsx files/ --failed retry_later.json
```

## Project Structure

```
pytvt/
├── main.py               # Python orchestrator — CLI, concurrency, output
├── tvt_protocol.py       # Pure Python TVT binary protocol client
├── tvt_discovery.py      # LAN auto-discovery + remote subnet sweep
├── scan_nvr.mjs          # Node.js SDK bridge — subprocess for sdk-local backend
├── config.json           # Non-sensitive settings (port, timeout, etc.)
├── .env.example          # Template for credentials
├── requirements.txt      # Python dependencies
├── package.json          # Node.js dependencies (koffi) — sdk-local only
├── tools/                # Reverse-engineering and debug utilities
│   ├── verify_pw.py      # Live NVR nonce capture + password encryption verifier
│   ├── verify_capture.py # Pcap-based login packet encryption verifier
│   ├── parse_pcap.py     # Full TVT protocol pcap parser (SLL, SLL2, Ethernet)
│   ├── capture_sdk.sh    # Docker tcpdump capture script for SDK traffic
│   ├── sdk_login.cjs     # SDK login helper for traffic capture
│   └── test_sha1.mjs     # Direct PUB_SHA1Encrypt SDK function tester
└── tvt/                  # Git submodule — github.com/dannielperez/tvt
    ├── bin/linux/        # Native shared library (libdvrnetsdk.so)
    ├── source/           # TypeScript SDK source (reference implementation)
    ├── proto/            # Wireshark protocol dissectors
    └── docs/             # SDK documentation
```

## Protocol Details

The TVT binary protocol runs over TCP and uses the following packet structure:

| Offset | Size | Field | Description |
|---|---|---|---|
| 0 | 4 | Flag | Always `"1111"` |
| 4 | 4 | Length | Total packet length (little-endian) |
| 8 | 4 | Command | Command ID |
| 12 | 4 | CmdId | Sequence ID |
| 16 | 4 | CmdVer | Protocol version |
| 20 | 4 | DataLen | Payload length |
| 24 | … | Data | Command-specific payload |

Login passwords are XOR-encrypted using a 3-byte nonce provided in the initial handshake. Camera information is retrieved via HTTP requests tunnelled through the binary protocol.

### Login Encryption Variants

The protocol version in the init handshake (`protocolVer` at offset 12) determines the login encryption scheme:

#### Standard Variant (`protocolVer < 11`)

Password bytes are XOR-encrypted with a 3-byte nonce from the init packet (offset 45–47). Username is sent in plaintext.

#### Head Variant (`protocolVer >= 11`, init flag `"head"`)

Uses a stronger two-step password encryption scheme, discovered via SDK binary disassembly:

1. `MD5(password)` → hex digest → uppercase
2. Concatenate with nonce: `SHA1( MD5_UPPER + sprintf("%08d", nonce) )`

The nonce is a 3-byte little-endian integer from the init packet, formatted as a zero-padded 8-digit decimal string. The username is XOR-encrypted using the nonce formatted via `sprintf("%u", nonce)` as a repeating key.

The login packet uses `connectType=3` with a 236-byte payload, and the NVR returns device info and channel data directly in the login response (no HTTP tunnel needed for camera enumeration).

See `tvt/proto/` for Wireshark dissector scripts that decode the full protocol.

## Credits

- TVT protocol reverse engineering and TypeScript SDK by [2BAD/tvt](https://github.com/2BAD/tvt)
- Native SDK integration via [dannielperez/tvt](https://github.com/dannielperez/tvt) (fork)
- HTTP API wrapper: [dannielperez/tvt-api](https://github.com/dannielperez/tvt-api)
- FFI bindings powered by [koffi](https://koffi.dev/)

## License

[AGPL-3.0](LICENSE)
