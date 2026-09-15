# Protocol cleanup

- Scope: baseline `af4b19c92becf63809978f933791ed42113442a8`.
- `src/pytvt/protocol.py`: remove unused envelope-length unpack and unused
  response decode; return the body slice directly. Signatures, byte output,
  JSON parsing, network calls and timeouts are unchanged.
- `tests/test_protocol_response_body.py`: 12 cases cover short envelopes,
  inaccurate declared lengths, empty bodies and non-UTF-8 bytes.
- Validation: `PYTHONPATH=src /Users/dperez/git/UniqueOS/.venv/bin/python -m pytest
  tests/test_protocol.py tests/test_protocol_response_body.py tests/test_discovery.py
  -q --tb=short` — 66 passed. Existing 54 tests also passed before editing.
- Ruff check and format check for both changed Python files passed.
- Stability reviewer: OK. SDK-boundary reviewer: OK. No schema changes.
- Risk: low; targeted suite only. No live vendor calls. Broader SDK unused-import
  signals remain outside this bounded cleanup.
- Next: publish this branch before publishing the UniqueOS gitlink that references
  it; human reviews and merges. Nothing deployed.
