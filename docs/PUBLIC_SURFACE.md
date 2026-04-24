# Public Surface Classification

This document classifies `pytvt` surfaces as stable, internal, or provisional.

## Stable

These are expected to remain backward compatible for downstream callers:

- Top-level package exports in `src/pytvt/__init__.py` for scanning/device flows
- `DeviceManager` and `available_backends`
- `NvrClient` and primary NVR models
- `WebApiClient`
- `sdk_http` / `sdk_http_client` typed clients

Compatibility-only:

- `netsdk` bindings and loader interfaces are retained for existing consumers
	but are not the preferred entrypoint for new integrations.

## Internal

These are implementation details and may change without notice:

- Private helpers and internal classes in `src/pytvt/*` not exported from package root
- Internal symbol-parsing and backend selection helpers
- Disposable tooling helper internals under `tools/`

## Provisional / Experimental

These are intentionally additive and may evolve:

- `pytvt.management` package as a whole
- `ManagementClient`, especially `platform_sdk`, `sidecar`, and other management runtime selection details
- Management protocol-native backend in `src/pytvt/management/native.py`
- Sidecar backend (`sidecar` runtime mode) — SDK-agnostic; some management operations not yet implemented
- Management evidence model details while validation is ongoing
- Manifest-based comparison tooling contracts in `tools/` (operational, not package API)

## Export Policy

Current exports are intentionally preserved to avoid downstream breakage.
Future tightening should be versioned and announced via changelog entries,
not introduced as silent cleanup.
