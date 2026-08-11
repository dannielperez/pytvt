# Runtime device-clock handoff

- **Branch:** `codex/runtime-device-clock`
- **Outcome:** `SyncRuntimeClient.read_device_time()` now owns the read-only
  `deviceTime` job schema and returns an immutable `RuntimeDeviceTime` DTO.
- **Safety:** the public method cannot send `setTimestamp`. Responses must prove
  `action=get`, contain no mutation timestamp/error, and carry a bounded,
  parseable naive wall-clock value.
- **Semantics:** TVT does not report a timezone offset for this read, so the DTO
  explicitly exposes `timezone_known=False`; consumers must not infer UTC drift.
- **Boundary:** raw runtime jobs and response validation remain in pytvt.
- **Validation:** all 46 runtime-client tests and 1,305 package tests pass;
  targeted Ruff check and format pass. The two isolated wheel/sdist guardrails
  remain environment-blocked because the build sandbox cannot install
  `hatchling`, matching the prior runtime slices.
- **Next:** pin this commit in pytvt-runtime, then expose a separately bounded
  UniqueOS diagnostic seam without adding latency to channel-health polling.
