# pytvt

A Python toolset for bulk-scanning [TVT](https://en.tvt.net.cn/) (Shenzhen TVT Digital Technology Co., Ltd) NVRs and enumerating every programmed IP camera. Supports two independent backends — a pure-Python binary protocol client and a native SDK bridge via HTTP API — that can be used alone or together.

Built on the reverse-engineering work from [2BAD/tvt](https://github.com/2BAD/tvt) (TypeScript) and extended with a native Python protocol client.

## Features

- **Pure-Python protocol client** — speaks the TVT binary protocol directly over TCP (port 6036), including XOR-encrypted login and HTTP-tunnelled API calls
- **Native SDK via HTTP API** — optional [tvt-api](https://github.com/dannielperez/tvt-api) backend that wraps the vendor's `libdvrnetsdk.so` in a Fastify HTTP service (Docker, linux/amd64)
- **Native SDK local** — alternative `sdk-local` backend that runs the SDK bridge (`scan_nvr.mjs`) as a subprocess (requires Node.js + the SDK natively on Linux x86-64)
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

1. **Handshake** — receives encryption parameters (nonce, protocol version)
2. **Login** — authenticates with XOR-encrypted credentials
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
| `input` | Path to NVR devices JSON file (required) |
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
    "ip": "10.0.1.250",
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
| NVR32 | 10.0.1.250 | TD-3332B4 | ABC123 | 5.2.3.190 | 29 |

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
├── scan_nvr.mjs          # Node.js SDK bridge — subprocess for sdk-local backend
├── config.json           # Non-sensitive settings (port, timeout, etc.)
├── .env.example          # Template for credentials
├── requirements.txt      # Python dependencies
├── package.json          # Node.js dependencies (koffi) — sdk-local only
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

See `tvt/proto/` for Wireshark dissector scripts that decode the full protocol.

## Credits

- TVT protocol reverse engineering and TypeScript SDK by [2BAD/tvt](https://github.com/2BAD/tvt)
- Native SDK integration via [dannielperez/tvt](https://github.com/dannielperez/tvt) (fork)
- HTTP API wrapper: [dannielperez/tvt-api](https://github.com/dannielperez/tvt-api)
- FFI bindings powered by [koffi](https://koffi.dev/)

## License

[AGPL-3.0](LICENSE)
