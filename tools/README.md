# tools/ — Operational Utilities

Scripts in this directory are **operational utilities** that build on top of the
`pytvt` package. They are not part of the installed package and are not required
for normal scanning or discovery.

Unlike the scripts in [`research/`](../research/), these tools are intended for
real operational use — they import from `pytvt` and perform useful batch
operations against live infrastructure.

## Scripts

| Script | Purpose |
|---|---|
| `enable_nvr_services.py` | Batch-enable RTSP + API Server on TVT NVRs from a Ruijie inventory |

## Usage

```bash
# Requires pytvt to be installed
pip install -e .

# Run from repo root
TVT_PASSWORD=... python tools/enable_nvr_services.py
```
