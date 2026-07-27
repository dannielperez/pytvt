# Handoff — T-1770 pytvt error retryability

## Outcome

- Added an overridable `NvrApiError.retryable` property that defaults to `False`.
- Documented the structural classification: NVR response rejections and deterministic
  validation/crypto failures use `NvrApiError`, while transport `OSError` and
  `TimeoutError` exceptions propagate unchanged.
- Extended the existing model tests to cover errors with and without an error code,
  inherited behavior, and a subclass override.
- No transient vendor error codes were guessed or added.

## Validation

- `python3 -m pytest tests/test_models.py -q` — 26 passed.
- `python3 -m ruff check src/pytvt/models.py tests/test_models.py` — passed.
- `python3 -m ruff format --check src/pytvt/models.py tests/test_models.py` — passed.
- Full SDK suite — 1,111 passed and 4 environment-only failures:
  - two alarm-server tests could not bind a localhost socket in the sandbox;
  - two packaging guardrail tests could not install `hatchling` in their isolated
    build environments because external package resolution was unavailable.

## Consumer follow-up

After this SDK change merges and UniqueOS bumps the pytvt submodule pin, map
`NvrApiError.retryable` into the TVT adapter's `AdapterResult`.
