# Preserve PlatformSDK area hierarchy

## Change
- `platform_sdk/inventory.py`: export every area with its GUID, parent GUID and cycle-bounded ancestor path; recover missing parent GUIDs from the resource node index. Root `sites` remains compatible.
- `runtime_client.py`: preserve optional `areas` across typed runtime decoding. Omission stays distinct from an authoritative empty list.
- Tests cover nested areas, recorder/channel parents, cyclic or missing ancestors, legacy omission, explicit empty lists and malformed runtime area rows.

## Evidence and validation
- Owner-authorized NVMS2 inspection on 2026-09-23 showed Comercio > Caridad > branches. The old snapshot exported only root sites, discarding those branches.
- Regression failed before the change with `KeyError: areas`.
- Targeted SDK tests: 108 passed. Repository Ruff check and format check passed (153 files).
- Independent SDK-boundary and stability reviews: OK. No model or migration changes.
- Runtime worker `_inventory` returns the complete dictionary after bounded JSON validation; it does not strip this additive field. Its SDK dependency must be updated and deployed by the owner alongside the consuming application.

## Limits / next steps
- Does not certify monitoring-center video, repair all-offline state reporting, discover transfer servers, or map vendor branches to canonical customer sites.
- Same-day native totals matched the fresh snapshot: 250 recorders and 5,395 channels. Native status was 228 recorders online; channel status fluctuated around 4,346 online / 1,049 offline. Channel totals include zero/unassigned channels and must not replace the manual 827/1,103 camera assessment.
- Owner merges and deploys SDK/runtime/application changes; then rerun authorized sync and verify hierarchy before issuing a final camera report. No deployment or merge performed.
