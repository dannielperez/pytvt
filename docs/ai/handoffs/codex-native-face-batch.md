# Handoff — native face capture batch client

- **Branch:** `codex/native-face-batch`
- **State:** implementation complete; draft PR pending CI.
- **Outcome:** `SdkHttpClient.search_face_capture_images()` calls one bounded
  bridge endpoint that returns a typed native face page plus per-item JPEG/gap.
- **Why:** the separate search and image methods remain available, but composing
  them in an application repeats native login/queue overhead and can exceed the
  caller's task budget. The batch contract permits one disposable bridge worker
  and one recorder login for the complete page.
- **Validation:** response count, base64, JPEG magic, recorder-local timestamps,
  and native channel identity are validated before typed results are returned.
  The JSON response has a 40 MiB ceiling.
- **Compatibility:** additive API; existing clients and separate endpoints are
  unchanged. Package version is 1.3.1.
- **Tests:** 38 SDK HTTP client tests passed; full functional suite 1,180 passed.
  Two isolated wheel/sdist guardrails could not download hatchling in this
  workstation's build sandbox and remain CI-authoritative. Targeted ruff and
  format checks passed.
- **Next:** pin this commit in pytvt-api, implement `/face/captures/batch` inside
  one worker session, then consume only the high-level batch from UniqueOS.
