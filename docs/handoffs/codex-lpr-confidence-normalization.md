# LPR confidence-normalization handoff

- **Branch:** `codex/lpr-confidence-normalization`.
- **Problem:** live persistent-runtime UAT parsed a channel-6 NVR plate callback
  without transport errors, but UniqueOS rejected its typed metadata with
  `ValidationError` before observation persistence. The NVR contract's only
  plausible unbounded percentage field is unsigned `plateConfidence`.
- **Change:** IPC and NVR parsers retain confidence values from 0 through 100.
  Out-of-range unsigned values become `None` and add the bounded
  `plate_confidence_out_of_range` warning, preserving the plate observation as
  partial metadata instead of exporting a vendor sentinel as a percentage.
- **Boundary:** normalization remains inside pytvt. No Django validation,
  persistence, retry, credential, or gate-policy behavior is included.
- **Validation:** `tests/test_plate_events.py` passes 19 tests; full suite passes
  1,263 tests with only the two known isolated-build failures caused by the
  environment being unable to install `hatchling`; Ruff check/format and diff
  check pass.
- **Live gate:** a privacy-safe diagnostic is waiting for the next callback and
  will print only rejected field names. Do not promote this candidate or select
  the runtime backend until it confirms `confidence`, then repeat a real event
  through observation creation and automatic-prefill correlation.
- **Rollback:** retain the deployed `nvr_poll` source selection and revert this
  SDK-only change if the diagnostic identifies a different field.
