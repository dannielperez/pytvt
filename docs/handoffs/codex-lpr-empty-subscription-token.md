# Empty smart-subscription address handoff

- **Branch / PR:** `codex/lpr-empty-subscription-token` / draft PR #70.
- **Outcome:** pytvt accepts a successful `NET_SDK_SmartSubscrib` result when
  this supported Linux SDK leaves `serverAddress` empty. The value is preserved
  for the matching unsubscribe call, whose existing failure/retry fencing is
  unchanged.
- **Tests:** plate-subscription lifecycle `18 passed`; Ruff check and format
  pass. Full suite: `1261 passed`; the two pre-existing isolated-build tests
  remain environment-blocked while installing `hatchling`. GitHub build and
  Python 3.10-3.14 jobs all pass.
- **Live UAT:** setup took 4.778 seconds. A plate was matched internally to the
  later recorder-history observation without logging its value: native callback
  28.890 seconds after occurrence, poll 159.819 seconds, native advantage
  130.929 seconds, and poll-to-database persistence 0.651 seconds. Counters were
  one parsed and zero dropped/malformed/rejected.
- **Recovery:** after an abrupt client exit, deterministic reattachment took
  0.006 seconds and clean unsubscribe succeeded. Final candidate health showed
  zero active sessions, streams, and pending requests.
- **Deployment state:** UniqueOS remains on its working `nvr_poll` fallback.
  The isolated candidate services were stopped and the temporary recorder `/32`
  route was removed. Reviewed SDK/source staging directories remain on the UAT
  host for a later owner-approved promotion.
- **Next:** merge PR #70, update the installed runtime's pytvt revision, stage
  the reviewed newer Linux SDK, provide a durable host-to-WireGuard route, then
  select `runtime` for the source and repeat persistence/prefill UAT. Do not
  remove `nvr_poll`; it remains the per-source compatibility fallback.
