# Platform inventory resource GUID handoff

- Branch: `fix/platform-inventory-resource-guid`
- Scope: expose the PlatformSDK resource and parent GUIDs through the typed
  normalized model and JSON-safe inventory payload.
- Boundary: GUID extraction remains inside `pytvt`; vendor `raw_data` is not
  exported to downstream applications.
- Compatibility: existing numeric IDs and constructors are unchanged. The two
  new payload keys are additive.
- Validation: `pytest -q tests/test_platform_operations.py`.
- Follow-up after merge: repin `pytvt-runtime`, then UniqueOS, and make the
  UniqueOS platform sync prefer `guid` / `parent_guid` before the legacy
  fallback.
