HANDOFF: codex/direct-camera-rtsp · 2026-08-07 16:15 UTC
- objective: Resolve standard TVT IPC RTSP profiles without treating standalone cameras as NVRs.
- state: done
- PR: pending draft · changed: `device_sdk/manager.py`, RTSP snapshot tests
- validations: `PYTHONPATH="$PWD/src" python3 -m pytest -q tests/test_rtsp_snapshot_backend.py` OK 33 passed · ruff check/format OK
- blockers: deployment and live-vendor verification remain owner-gated
- next: review and merge the SDK PR before the dependent UniqueOS gitlink update · knowledge: n/a
