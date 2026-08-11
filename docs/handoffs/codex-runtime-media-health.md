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
- **Deadlines:** each typed call sends its local deadline in the request envelope,
  allowing pytvt-runtime to clamp queue wait and native execution to the same
  caller budget instead of leaving orphaned work after a socket timeout.
- **Boundary:** callers provide recorder identity and typed options; raw runtime
  job schemas and response validation remain inside pytvt.
- **Validation:** focused runtime-client tests pass (`24 passed`); the full code
  suite passes (`1283 passed`) and Ruff check/format pass. The two isolated
  wheel/sdist guardrails are currently blocked installing `hatchling` in their
  temporary build environments; the full suite passed (`1284 passed`) before
  the deadline-only follow-up commit.
- **Dependency:** pin this commit into pytvt-runtime before UniqueOS enables
  runtime-backed Access Control snapshots or device-health polling.
