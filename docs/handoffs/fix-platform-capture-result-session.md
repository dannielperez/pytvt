# fix/platform-capture-result-session

## Outcome

- Added public `PlatformCaptureError`, a `ProtocolError` subtype with explicit
  `invalidates_session=False` semantics.
- Empty, oversized, and malformed buffers returned after a successful
  `Plat_CaptureJpgPictureDataEx` call now use that typed error.
- The classification is owned by `pytvt`; consumers do not interpret native
  payloads or vendor error codes.

## Evidence

- A bounded UAT canary proved PlatformSDK login, inventory reuse, idle
  retirement, and fresh login all succeed without credential, transient, or
  cooldown failures.
- Separate UAT capture attempts reached the native function but returned zero
  bytes for the selected channels. That is a capture-result failure, not login
  evidence.
- Automatic capture remains disabled pending a known-good channel canary.

## Validation

- `pytest -q tests/test_platform_sdk.py` — 84 passed.
- `pytest -q --ignore=tests/test_python_only_guardrail.py` — 1,369 passed.
- Full `pytest -q` — 1,371 passed; the two isolated-build guardrails could
  not install `hatchling` from this host, and local `--no-isolation` builds
  likewise confirmed that the backend is not installed.
- `ruff check` and `ruff format --check` on all changed Python files — passed.
- A pytvt-runtime follow-up must consume `PlatformCaptureError` and preserve the
  persistent worker session before UAT rollout.
