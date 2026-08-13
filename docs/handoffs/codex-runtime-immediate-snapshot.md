# Runtime immediate snapshot admission handoff

- Branch: `codex/runtime-immediate-snapshot`
- Objective: expose atomic immediate-admission intent for runtime snapshot calls.
- Behavior: `SyncRuntimeClient.capture_snapshot(..., require_immediate_admission=True)` adds the typed runtime admission envelope while preserving queued admission by default. Protocol version 2 prevents an older runtime from silently ignoring the admission requirement.
- Boundary: callers do not construct protocol payloads; socket framing and admission encoding remain in `pytvt.runtime_client`.
- Validation: `tests/test_runtime_client.py` — 48 passed; focused Ruff — passed.
- Safety: no live recorder calls, secrets, release, merge, or deployment.
- Dependency: requires `pytvt-runtime` support for the `admission: immediate` execute envelope.
