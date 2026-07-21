# Public Surface Classification

This document classifies `pytvt` surfaces as stable, internal, or provisional.

## Stable

These are expected to remain backward compatible for downstream callers:

- Top-level package exports in `src/pytvt/__init__.py` for scanning/device flows
- `DeviceManager` and `available_backends`
- `NvrClient` and primary NVR models
- `NvrApiError`, and `NvrApiResponseShapeError` (since 1.1.0) — the latter signals a firmware
  response-shape mismatch from `query_channels` rather than a genuinely empty result
- `WebApiClient`
- `parse_alarm_frame` and `ParsedAlarmFrame` (since 1.2.0) — decode TVT alarm-server push frames
  (JSON / binary / HTTP / length-prefixed)
- `sdk_http` / `sdk_http_client` typed clients
- `pytvt.device_sdk` plate-event DTO/parser surface: `PlateEvent`,
  `PlateEventStream`, `PlateStreamStats`, `PlatePayloadError`,
  `parse_ipc_plate_payload`, and `parse_nvr_plate_payload`

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

- `pytvt.platform_sdk` package as a whole
- `ManagementClient`, especially `platform_sdk`, `sidecar`, and other management runtime selection details
- Management protocol-native backend in `src/pytvt/management/native.py`
- Sidecar backend (`sidecar` runtime mode) — SDK-agnostic; some management operations not yet implemented
- Management evidence model details while validation is ongoing
- Manifest-based comparison tooling contracts in `tools/` (operational, not package API)
- Live `DeviceSession.subscribe_plate_events()` activation semantics until the 1.3.2
  callback/subscription path has completed a read-only fleet conformance pilot. The
  parser/DTO contract is stable; device renewal timing remains provisional.

## Export Policy

Current exports are intentionally preserved to avoid downstream breakage.
Future tightening should be versioned and announced via changelog entries,
not introduced as silent cleanup.
