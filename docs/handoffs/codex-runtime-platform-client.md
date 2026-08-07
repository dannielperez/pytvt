# Server SDK runtime client handoff

## Scope

- Added a typed `get_platform_inventory()` Unix-runtime operation and validated
  `RuntimePlatformInventoryResult` DTO.
- Classified PlatformSDK authentication failures with a structured
  `credential_rejected` field for downstream cooldown policy.
- Raised the bounded runtime response allowance to include a 32 MiB Server SDK
  result plus its protocol envelope.
- Bumped pytvt to 1.3.4.

## Validation

- `uv run ruff check src tests` — pass.
- `uv run ruff format --check src tests` — pass.
- `uv run pytest` — 1,237 passed; two isolated-build guard tests failed only
  because the host Python 3.13 subprocess could not install hatchling.
- `uv build` — wheel and sdist built successfully.
- Built artifacts contain no JavaScript or native SDK library files.

## Compatibility and risk

- Protocol version remains 1; the change is additive.
- Platform inventory uses a 60-second operation-specific absolute deadline,
  leaving headroom above the runtime's 45-second native budget without
  lengthening ordinary device calls.
- The typed operation does not expose raw ctypes structures or arbitrary SDK
  calls.
- No live TVT/NVMS calls were made.
