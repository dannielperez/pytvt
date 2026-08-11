# Runtime event acknowledgement handoff

- **Branch:** `codex/runtime-event-ack`
- **Outcome:** the typed runtime LPR client supports opt-in sequence/ack
  delivery. A consumer calls `stream.ack()` only after durable persistence;
  polling before that replays the same sequence.
- **Compatibility:** legacy streams retain their at-most-once start/poll/stop
  contract. Acknowledged mode is explicitly negotiated with
  `deliveryMode=acked` and fails closed if the runtime does not echo support.
- **Identity:** acknowledged mode requires a caller-supplied stable `stream_id`
  so a new client process can reattach to retained evidence.
- **Bounds:** acknowledged mode requires at least two event slots and buffer
  capacity above one maximum payload, matching the runtime's hard reservation
  for its single retained delivery.
- **Failure behavior:** context exit with a pending delivery detaches without
  stopping the remote stream so a stable stream id can reattach. Destructive
  abandonment is explicit via `close(discard_unacked=True)`.
- **Validation:** runtime-client tests pass (`19 passed`); full pytest passes
  (`1280 passed`) when isolated builds use the public PyPI index. Targeted Ruff
  check and format pass; repository-wide Ruff still reports unrelated existing
  findings in `research/` and `tools/`.
- **Dependency:** the private `pytvt-runtime` implementation must land before an
  application opts into acknowledged delivery.
