# Recorder face readback

- Scope: `src/pytvt/xml_api.py` now reads enabled/schedule after a face-detection
  write and raises `NvrApiError` if the recorder silently ignores either value.
  `tests/test_face_api.py` covers rejected readback, missing enabled evidence,
  preserved schedule, disabling, and timeout without replay.
- Validation: targeted face/NVR tests 79 passed; changed-file Ruff check/format
  passed. Full suite: **1,485 passed**, including wheel/sdist guardrails, in an
  isolated test venv with public PyPI for build dependencies. No hardware calls.
- Risk: one additional bounded read per setter; firmware lacking authoritative
  readback now fails instead of reporting success. Existing return type and
  preserved-schedule behavior remain compatible.
- Review: SDK-boundary and stability lenses OK. No schema changes.
- Baseline debt: whole-tree Ruff reports 78 existing research/tools findings and
  12 existing unformatted files, tracked in UniqueOS task T-3883. Touched files
  are clean; unrelated baseline was not reformatted.
- Next: merge this SDK change before the consuming UniqueOS pin. Human owns merge
  and hardware validation. This does not configure camera-side face/LPR profiles.
