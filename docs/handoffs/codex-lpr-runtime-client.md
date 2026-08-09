# Persistent LPR runtime client handoff

- **Branch:** `codex/lpr-runtime-client`
- **Outcome:** public `pytvt` now owns a typed `subscribe_plate_events()` Unix-
  socket client. It presents the existing `PlateEvent` stream interface while
  validating lifecycle responses, enums, timestamps, bounds, stats, and base64
  image payloads before returning them to an application.
- **Safety:** start, poll, and stop use bounded socket exchanges. Empty polls
  raise `queue.Empty`; close is idempotent; malformed or oversized runtime
  responses fail closed. No vendor SDK binary or native call is involved.
- **Validation:** runtime-client tests `13 passed`; full suite `1249 passed`.
  Two packaging-isolation tests were environment-blocked because their nested
  Python 3.13 build environment could not install `hatchling`; scoped Ruff
  check and format pass.
- **Dependency:** private `pytvt-runtime` branch `codex/lpr-event-stream` pins
  commit `e471801654e92e0bad62a82e939ea5882fa2dac8`.
- **Next:** review/merge before the runtime and UniqueOS dependency PRs. A live
  recorder conformance check remains owner-gated.
