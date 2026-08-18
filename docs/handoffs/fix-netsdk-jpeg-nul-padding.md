# NetSDK JPEG NUL padding handoff

- **Branch:** `fix/netsdk-jpeg-nul-padding`
- **Symptom (UAT, 2026-08-18):** every runtime-routed TVT still capture failed
  `client_unavailable` since 2026-08-16 ~15:20 UTC. A bounded live reproduction
  from the Django container against a warm recorder session returned in 0.35 s
  with `RuntimeClientError("runtime returned an invalid snapshot")`; the raw
  payload was a complete 25,469-byte JPEG (`ffd8 … ffd9`) followed by one NUL byte.
- **Root cause:** NetSDK capture length includes NUL padding after the EOI marker;
  `runtime_client._parse_snapshot` requires the payload to end exactly at EOI.
- **Fix:** shared `pytvt._jpeg.strip_jpeg_nul_padding()` — removes trailing NUL
  bytes only when they follow a complete `SOI…EOI` image; otherwise the payload is
  unchanged. Applied in `DeviceSession.capture_jpeg()` (producer, both variants)
  and in `_parse_snapshot()` (consumer tolerance for an older runtime).
- **Boundary:** vendor quirk handled in pytvt; no consumer parses vendor bytes.
- **Validation:** 3 new tests failed before and pass after; full suite 1,376
  passed; Ruff check/format and `git diff --check` pass.
- **Rollout:** (1) merge; (2) UniqueOS bumps `vendor/pytvt` and ships the Django
  image — restores captures immediately even against the current runtime;
  (3) pytvt-runtime pins this commit so the producer is normalized as well.
