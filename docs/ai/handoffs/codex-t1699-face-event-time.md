# Handoff — codex/t1699-face-event-time

## Outcome

- `FaceEvent` now exposes a timezone-aware `occurred_at` value parsed at the
  pytvt boundary.
- `NvrClient.search_face_events()` converts the recorder's epoch seconds and
  seven-digit 100 ns suffix into a UTC `datetime`.
- The original `timestamp` and `frame_time` strings remain unchanged for
  snapshot lookup and backward compatibility.

## Validation

- `uv run pytest -q tests/test_face_api.py` — 20 passed.
- `uv run ruff check ...` — passed.
- `uv run ruff format --check ...` — passed.
- Full SDK suite — 1,094 passed; two packaging guardrail tests could not install
  `hatchling` in their isolated build environment because external package
  resolution was unavailable.
- SDK-boundary review — WARNING resolved by appending the new dataclass field
  after every existing field and adding a positional-constructor compatibility
  regression; targeted suite now 20 passed.

## Consumer follow-up

Pin this SDK commit in UniqueOS, consume `FaceEvent.occurred_at` in
`access_control.face_observations`, and let `NvrClient` retain ownership of its
default web-CGI port instead of passing an application constant.
