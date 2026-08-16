# Handoff — NetSDK session-invalidating errors

## Outcome

- Added `NetSdkError.invalidates_session` so SDK consumers can distinguish
  authentication/connection failures from request-local operation failures.
- Authentication, initialization, link-limit, version, and network failures
  invalidate the login handle. Channel, parameter, unsupported-operation, busy,
  and recorder-operation failures preserve it.
- Errors without a native code, unknown codes, and recognized-but-unreviewed codes
  fail closed and invalidate the handle.

## Validation

- Full pytest: 1,332 passed.
- Ruff check and format check: passed.
- Package guardrails: passed using public PyPI because the configured private
  package index rejected isolated-build authentication.

## Consumer follow-up

- `pytvt-runtime` should transmit this classification across its worker protocol
  and invalidate only sessions whose error is classified as invalidating.
