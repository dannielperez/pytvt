# fix/platform-channel-zero-index

## Outcome

- Preserve `nChlNO=0` as typed `PlatformResource.channel_number=0`.
- Continue using `-1` only when the SDK channel number is absent or `None`.
- Keep vendor field interpretation inside `pytvt`; consumers receive only the
  existing typed resource projection.

## Why

- PlatformSDK inventory is zero-based, but the previous truthiness fallback
  converted valid channel zero to the unknown sentinel `-1`.
- A bounded UAT canary proved the persistent session design is healthy: a
  channel-local capture-result failure preserved the one shared login, the
  next inventory reused it across 5,291 channels, login-failure counters stayed
  unchanged, and the idle worker retired to zero processes and zero CPU.
- Canonical channel selection therefore depends on accurate typed inventory,
  not more workers, retries, or arbitrary GUID probing.

## Validation

- `pytest -q tests/test_platform_sdk.py` — 87 passed.
- `pytest -q --ignore=tests/test_python_only_guardrail.py` — 1,372 passed.
- `ruff check` and `ruff format --check` on changed Python files — passed.
- Automatic capture remains disabled pending a known-good canonical channel
  canary.

## Review fanout

- SDK-boundary reviewer: pending.
- Stability reviewer: pending.
- Migration-safety reviewer: n/a (no schema or data changes).
