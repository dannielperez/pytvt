# pytvt Package Overview

## Intent

`pytvt` is a Python library for TVT ecosystems with two parallel tracks:

1. Stable device/NVR operations already used in production workflows.
2. Additive management-server support that remains explicitly provisional.

The management track must not silently replace or regress stable device/NVR
behavior.

## Module Map

## Stable library surface

- `src/pytvt/scanner.py`: bulk scanning entrypoints
- `src/pytvt/discovery.py`: LAN/subnet discovery
- `src/pytvt/nvr_api.py`: NVR CGI client
- `src/pytvt/webapi/`: HTTP Web API client
- `src/pytvt/device_manager.py`: unified backend facade
- `src/pytvt/sdk_http_client.py`: SDK bridge client

Compatibility-only surface:

- `src/pytvt/netsdk/`: legacy Linux native SDK bindings retained for
  backward compatibility

## Provisional management-server surface

- `src/pytvt/management/`
  - `client.py`: facade for management workflows
  - `sdk.py`: SDK-backed management diagnostics and capability evidence
  - `native.py`: native protocol stub (non-implemented by design)
  - `sidecar.py`: sidecar bridge backend — a first-class runtime mode for SDK-agnostic environments
  - `context.py`: normalized `SDKContext` model

## Operational tooling

Retained tooling lives in `tools/` and is intentionally minimal:

- `management_live_validate.py`
- `management_validation_lib.py`
- `manifest_resolver.py`
- `sdk_comparator.py`
- `management_report_summary.py`

## Management runtime modes

The management package supports three explicit backend modes:

- `native_linux_sdk` — SDK-backed management on Linux (requires `libdvrnetsdk.so`)
- `sidecar` — delegates to an external bridge process; SDK-agnostic by design; some operations remain provisional
- `native_protocol` — native protocol stub; not implemented by design
- `auto` — SDK-first with fallback to `native_protocol`

Backend selection is explicit and frozen for the session lifetime. SDK and native mixing within a session is not permitted.

## Cleanup Rules

- Keep production library modules in `src/pytvt/` behavior-stable.
- Keep management support additive and explicitly marked provisional.
- Keep generated artifacts (`tools/out/`) out of commits.
