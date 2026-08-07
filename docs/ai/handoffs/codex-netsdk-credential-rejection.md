# NetSDK credential-rejection contract

## Scope

- Added `NetSdkCredentialRejectedError`, a typed `NetSdkError` subclass.
- Moved direct-login credential error-code classification into `pytvt`.
- Exported the exception from `pytvt.device_sdk` for orchestration consumers.
- Kept transport and other transient failures as generic `NetSdkError` values.
- No live recorder or vendor service was contacted.

## Validation

- `uv run ruff check src/pytvt/device_sdk/client.py src/pytvt/device_sdk/__init__.py tests/test_netsdk_client.py`: passed.
- `uv run ruff format --check src/pytvt/device_sdk/client.py src/pytvt/device_sdk/__init__.py tests/test_netsdk_client.py`: passed.
- `uv run pytest -q tests/test_netsdk_client.py`: 144 passed.
- Full `uv run pytest -q`: 1,221 passed; two pre-existing package-build
  guardrail tests could not install Hatchling because the configured private
  package index rejected the local credentials. No functional test failed.

## Consumer follow-up

`pytvt-api` can catch `NetSdkCredentialRejectedError` without importing raw
`SdkError` values. This keeps vendor authentication semantics inside `pytvt`.
