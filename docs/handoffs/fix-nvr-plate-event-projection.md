# NVR plate event projection handoff

- **Branch:** `fix/nvr-plate-event-projection`
- **Symptom (UAT, 2026-08-18):** all 2,846 NVR plate observations in 24 h were
  `is_partial=True` with `trailing_payload_bytes` + `plate_confidence_out_of_range`,
  `confidence=None`, images typed `yuv` with `(0, 0)` dimensions.
- **Evidence:** one raw event-29 payload captured on the UAT host with the runtime's
  LPR SDK build (NVR 10.40.20.250, plate IWX354, 586,332 bytes). The documented
  `VEHICE_PLATE_INFO` layout is correct (plate, rect, channel GUID parse at the
  documented offsets; full JPEG SOI at 176, plate JPEG at 583,316). Deviations:
  `plateConfidence=9900` (basis points), both picture descriptors carry
  `iPicFormat=1` on real JPEG bytes with `iWidth=iHeight=0`, and the payload ends
  with 288 NUL bytes.
- **Fix:** NVR parser folds 101-10000 confidence onto 0-100; picture format
  trusts JPEG magic over the descriptor code; trailing all-NUL bytes no longer
  produce a warning (non-NUL trailing bytes still do). IPC parser contract is
  unchanged (percent only).
- **Validation:** 5 new tests fail-before/pass-after; full suite 1,381 passed;
  Ruff check/format; `git diff --check`.
- **Rollout:** merge → pytvt-runtime pin → UniqueOS UAT runtime pin → deploy;
  the Django `vendor/pytvt` gitlink is optional (parsing runs in the runtime).
