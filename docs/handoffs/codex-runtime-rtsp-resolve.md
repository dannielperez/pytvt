# Runtime RTSP resolve handoff

- **Branch:** `codex/runtime-rtsp-resolve`
- **Outcome:** `SyncRuntimeClient.resolve_rtsp_url()` now owns the bounded
  `rtspUrl` job schema and returns an immutable `RuntimeRtspUrl` DTO.
- **Safety:** channel and stream selectors are bounded before IPC. Runtime
  responses must be a credential-bearing `rtsp://`/`rtsps://` URL with a host,
  a valid optional port, no control characters, and at most 4096 UTF-8 bytes.
- **Boundary:** the public package owns transport schema and validation; callers
  do not construct raw runtime jobs or parse vendor responses.
- **Validation:** all 40 runtime-client tests and 1,299 package tests pass;
  targeted Ruff check and format pass. The two isolated wheel/sdist guardrails
  remain environment-blocked because the build sandbox cannot install
  `hatchling`, matching the prior runtime slices.
- **Next:** pin this commit in pytvt-runtime, then route UniqueOS direct live-view
  URL resolution through the warm session while preserving NAT behavior.
