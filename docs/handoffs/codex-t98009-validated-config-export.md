# Validated device-configuration export handoff

- **Ticket:** Zammad #98009
- **Branch:** `codex/t98009-validated-config-export`
- **Scope:** Harden the existing per-device TVT NetSDK configuration export so a backup consumer receives a verified artifact rather than only a successful native return code.
- **Behavior:** `DeviceSession.export_config()` writes to a mode-0600 temporary sibling, rejects failed or zero-byte exports, computes SHA-256, atomically publishes the completed artifact, and returns `ConfigExportResult(path, size_bytes, sha256)`.
- **Failure safety:** A failed/empty export is removed and an existing known-good destination remains unchanged.
- **Boundary:** This operation covers a compatible recorder/device configuration. It does not claim to export the central NVMS database, A510 management host, transfer-server configuration, retention state, or restore readiness.
- **Production safety:** No live device call, credential access, production export, restore, upload, or deployment was performed in this change.
- **Validation:** 3 focused tests pass; Ruff check and formatting pass; `git diff --check` passes. The full suite has 1,459 passing tests. Its two isolated-package tests initially failed because the host's configured private Python index returned HTTP 401 for Hatchling; rerunning the complete four-test packaging guardrail with `PIP_INDEX_URL=https://pypi.org/simple` passed.
- **Next integration step:** Merge/release pytvt, pin that version in UniqueOS, then implement the NVMS provider job that writes to an isolated staging path, records the returned size/hash, uploads through the approved encrypted immutable-backup provider, verifies retention/immutability metadata, and records an isolated restore test.
