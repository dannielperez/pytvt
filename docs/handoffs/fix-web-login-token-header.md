# Handoff — `fix/web-login-token-header`

## Status

- Implementation complete; PR pending.
- Live UAT validation confirmed the missing header was the HTTP 400 cause.
- Further live retries stopped after NVMS returned invalid-credential code
  `536870948`; the configured credential must be corrected separately.

## Change

- Stage the `reqLogin` token before `doLogin`, matching the shipped NVMS jQuery
  client and causing the second handshake request to carry the required
  `token` header.
- Clear staged token/cookie/session state after any transport, protocol, or
  authentication failure.

## Validation

- `uv run pytest tests/test_web_session.py -q` — 29 passed.
- `uv run ruff check ...` — passed.
- `uv run ruff format --check ...` — passed.
- Full suite — 1,331 passed; two package-build guardrails failed only because
  the system Python build-isolation environment could not install `hatchling`.
- `uv build --wheel --out-dir dist` — passed.
- Live read-only NVMS probe: before fix, `doLogin` returned HTTP 400 for missing
  `token`; with staged token, the server evaluated credentials and returned the
  typed invalid-credential response.

## Next

1. Merge and pin this pytvt revision in downstream runtime/UniqueOS packaging.
2. Add `web_port`/`web_scheme` propagation to the UniqueOS TVT platform adapter.
3. Correct the UAT NVMS web credential before enabling fleet health ingestion.
