# Runtime media and health handoff

- **Branch:** `codex/runtime-media-health` (stacked on `codex/runtime-event-ack`)
- **Outcome:** `SyncRuntimeClient` exposes typed `capture_snapshot()`,
  `get_device_info()`, and `probe_device_health()` operations over the existing
  persistent NetSDK session pool.
- **Images:** snapshot replies are strict-base64 decoded, capped at the runtime's
  25 MiB hard maximum, and rejected unless they carry JPEG start/end markers.
- **Health:** the health probe uses the existing read-only `deviceInfo` worker
  operation, so repeated polls reuse the logged-in recorder session and retain
  runtime queue/deadline/login-cooldown protection.
- **Boundary:** callers provide recorder identity and typed options; raw runtime
  job schemas and response validation remain inside pytvt.
- **Validation:** focused runtime-client tests pass (`23 passed`); full pytest
  passes (`1284 passed`). Targeted Ruff check and format check pass.
- **Dependency:** pin this commit into pytvt-runtime before UniqueOS enables
  runtime-backed Access Control snapshots or device-health polling.
