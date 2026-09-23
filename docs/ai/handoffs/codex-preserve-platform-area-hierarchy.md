# Preserve PlatformSDK area hierarchy

## Change
- `platform_sdk/inventory.py`: export every area with its GUID, parent GUID and cycle-bounded ancestor path; recover missing parent GUIDs from the resource node index. Root `sites` remains compatible.
- `runtime_client.py`: preserve optional `areas` across typed runtime decoding. Omission stays distinct from an authoritative empty list.
- Tests cover nested areas, recorder/channel parents, cyclic or missing ancestors, legacy omission, explicit empty lists and malformed runtime area rows.

## Evidence and validation
- Native NVMS2 inspection confirmed nested customer branches. The old snapshot exported only root sites, discarding those branches.
- Regression failed before the change with `KeyError: areas`.
- Targeted SDK tests: 108 passed. Repository Ruff check and format check passed (153 files).
- Independent SDK-boundary and stability reviews: OK. No model or migration changes.
- Runtime worker `_inventory` returns the complete dictionary after bounded JSON validation; it does not strip this additive field. Its SDK dependency must be updated and deployed by the owner alongside the consuming application.

## Limits / next steps
- Does not certify monitoring-center video, repair all-offline state reporting, discover transfer servers, or map vendor branches to canonical customer sites.
- Channel totals can include zero/unassigned channels and must not be represented as a physical installed-camera census.
- Owner merges and deploys SDK/runtime/application changes; then rerun authorized sync and verify hierarchy before issuing a final camera report. No deployment or merge performed.
