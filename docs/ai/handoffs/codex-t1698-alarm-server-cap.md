# T-1698 — AlarmServer connection cap

- Outcome: added a hard, configurable concurrent-connection cap to `AlarmServer`; excess connections are closed immediately and reported as `AlarmServerCapacityError`.
- Safety: admission uses a bounded semaphore, thread-registry access is locked, callback errors remain isolated, and shutdown now spends at most one second joining handlers in aggregate.
- Compatibility: `max_connections` is optional and defaults to 32; existing constructor calls keep working.
- Validation:
  - `ruff check` / `ruff format --check`: pass.
  - `pytest tests/test_face_api.py -q`: 32 passed.
  - Full suite: 1107 passed; 2 environment-only failures because the reused UniqueOS virtualenv lacks the optional `build` package required by packaging guardrails.
- No live NVR calls, deployment, release, or merge performed.
