# Runtime channel health handoff

- **Branch:** `codex/runtime-channel-health` (stacked on `codex/runtime-event-ack`)
- **Outcome:** `SyncRuntimeClient.scan_channels()` exposes the existing bounded
  runtime `scan` operation as typed recorder/channel DTOs.
- **Boundary:** raw `maxCameras`, status strings, response keys, and validation
  remain inside pytvt. Callers receive immutable channels with a boolean online
  state and cannot accept duplicate, malformed, or over-limit responses.
- **Validation:** focused runtime-client tests pass (27); the full code suite
  passes (1286) and Ruff passes. The two isolated wheel/sdist guardrails remain
  environment-blocked while installing `hatchling` in temporary build envs.
- **Next:** pin this commit in pytvt-runtime and route UniqueOS TVT full-status
  polling through the warm persistent session.
