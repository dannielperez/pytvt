# Handoff — typed face-search capability probe

## Outcome

- Added `NvrClient.probe_face_search`, a read-only probe for the exact
  `searchImageByImageV2` command used by face ingest.
- The probe reads typed Server Port, RTSP server, and API server state and runs
  the search with `result_limit=1` without fetching image bytes.
- Only the field-proven legacy rejection `536870934` becomes
  `FaceSearchApiStatus.UNSUPPORTED`; all transport, authentication, response
  shape, and unknown vendor failures still raise.
- No recorder configuration is changed and no alternate endpoint is guessed.

## Consumer follow-up

UniqueOS must bump this submodule pin and provide an exact-device,
operator-triggered command that persists the typed result in non-secret device
metadata. It must not mark transient or unknown failures unsupported.

## Validation

- `tests/test_face_api.py` — 35 passed.
- Targeted Ruff check/format and `git diff --check` — passed.
- Targeted mypy with imported modules skipped and the existing
  `no-any-return` baseline disabled reports no issues in the two changed source
  modules. The unfiltered invocation reaches the repository's existing
  55-error imported-module baseline.
- Stability reviewer: OK. SDK-boundary reviewer: OK.
