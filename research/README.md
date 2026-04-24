# research/ — Protocol Reverse Engineering Reference

> **⚠ RESEARCH / REFERENCE ONLY** — These scripts are not part of the pytvt runtime
> and are not executed during normal scanning, discovery, or any CLI operation.

## What is this?

This directory contains scripts used during the reverse engineering of the TVT
binary protocol (port 6036). They document the process of discovering how NVR
authentication works — nonce exchange, password encryption schemes (XOR and
SHA1 head-variant), login packet structure, and username encoding.

The findings from this research are implemented as production code in
[`src/pytvt/protocol.py`](../src/pytvt/protocol.py).

## Why keep it?

- **Reproducibility** — anyone can re-derive the protocol findings from scratch
- **Documentation** — the scripts serve as runnable documentation of *how* the
  protocol was reverse engineered, complementing the *what* in protocol.py
- **Future research** — if TVT introduces new firmware versions or encryption
  variants, these tools provide a starting point for analysis

## Scripts

| Script | Language | Purpose |
|---|---|---|
| `capture_sdk.sh` | Bash | Capture SDK ↔ NVR traffic via tcpdump inside Docker |
| `sdk_login.py` | Python | Trigger a native SDK login to generate capturable traffic |
| `test_sha1.py` | Python | Call `PUB_SHA1Encrypt` from the SDK to compare with Python output |
| `parse_pcap.py` | Python | Extract init/login packets from a pcap for manual inspection |
| `verify_pw.py` | Python | Connect to live NVR, capture nonce, verify encryption scheme |
| `verify_capture.py` | Python | Extract password from captured pcap and brute-force the hash scheme |

## Typical workflow (for reference)

```
1. Start tvt-api Docker container with SDK
2. Copy capture_sdk.sh + sdk_login.py into the container
3. Run capture_sdk.sh → produces /tmp/capture.pcap
4. Copy pcap out, run parse_pcap.py or verify_capture.py to analyze
5. Alternatively, run verify_pw.py directly against a live NVR
```

## Relationship to pytvt architecture

These scripts sit entirely outside the package architecture:

```
src/pytvt/          ← supported runtime (installed package)
bridges/            ← historical local-only runtime area (now removed)
tvt-api/            ← compatibility runtime (Docker compat_bridge)
tools/              ← operational utilities (import from pytvt)
research/           ← this directory (standalone, no imports into pytvt)
```

**No module in `src/pytvt/` imports from `research/`.** This boundary is
enforced by a test in `tests/test_architecture.py`.

## Running these scripts

These scripts are standalone and do **not** require `pytvt` to be installed.
They have their own dependencies (raw socket access, pcap files, Docker + SDK,
and Python 3) and are intended for manual, interactive use only.
