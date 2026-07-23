# License-plate event ingestion

`pytvt.device_sdk` owns the TVT ABI, callback-lifetime, and payload parsing required to
consume camera-produced plate recognition. Applications receive immutable normalized
events; they do not parse native structures or hold callback pointers.

## Supported event shapes

| TVT event | Source | Parsed payload |
|---|---|---|
| `SmartEventType.VEHICLE` (20) | direct IPC | vehicle header + original image + one or more plate items/crops |
| `SmartEventType.NVR_VEHICLE` (29) | NVR | plate metadata + original image descriptor/data + plate crop descriptor/data |

Both produce `PlateEvent`. Fields unavailable in one vendor shape remain `None` or an
empty tuple; missing evidence is explicit through `is_partial` and `warnings`.

## Fixture-first use

Pure parsers are the safest first integration point:

```python
from pytvt.device_sdk import parse_nvr_plate_payload

event = parse_nvr_plate_payload(
    copied_callback_payload,
    user_id=login_handle,
    channel_id=channel,
)
```

For a native 1.3.2+ session with all three subscription symbols available:

```python
from pytvt.device_sdk import SmartEventType

with session.subscribe_plate_events(
    channels=[0, 1],
    commands=[SmartEventType.NVR_VEHICLE],
    max_events=256,
    setup_timeout=30.0,
    experimental=True,
) as events:
    event = events.get(timeout=5.0)
    stats = events.stats()
```

The stream is process-global because the vendor callback is process-global. Live
registration is rejected unless the caller explicitly passes `experimental=True`, and
the process must contain exactly one live `NetSdkClient`. A second stream/client or
generic `subscribe_v2` callback is rejected until the first one closes.
Registration is atomic across requested channels/commands; a partial failure rolls back
successful subscriptions and clears the callback.

## Bounds and failure behavior

- callback data is copied before the native callback returns;
- payloads default to a 16 MiB cap and individual images to 8 MiB;
- queue capacity defaults to 256 events;
- queued image evidence also has an independent 64 MiB aggregate cap, so the count
  bound cannot multiply large callback images into multi-gigabyte memory growth;
- registration has a 30-second aggregate setup deadline by default and rejects
  non-finite, non-positive, or greater-than-300-second overrides; the deadline is
  checked between native registrations and any completed partial setup is rolled back;
- default backpressure drops the oldest buffered event and increments
  `events_dropped`; `DROP_NEWEST` is also available;
- malformed UTF-8, invalid counts/lengths, truncation, and oversized images are rejected
  and counted without raising across the C ABI;
- zero-byte image descriptors are normalized to missing evidence and mark the event
  partial instead of presenting an empty byte string as a complete image;
- `close()`, session logout, and client cleanup all attempt unsubscribe before releasing
  the SDK callback; setup failure compensates subscriptions already created;
- failed unsubscribe keeps the device-session handle valid for retry, and a successful
  stream close wakes readers blocked in `get()` with `RuntimeError` after buffered events
  have been consumed.

The aggregate setup deadline limits a sequence of native registrations but cannot
interrupt one native call that never returns. Run provisional live subscriptions inside
a disposable, externally supervised process when a hard wall-clock stop is required.

`PlateStreamStats` is intentionally in-memory. A long-running consumer should export its
counters through its own metrics system and persist only policy-approved plate/evidence
data with bounded retention.

## Deliberate boundaries

- The camera remains the recognizer; `pytvt` does not implement OCR.
- `edge_match` normalizes the camera-local comparison result as evidence, not as an
  authorization decision. Applications should keep allow/deny/watch policy in their
  own audited domain.
- No relay or rolling-gate command is issued by plate ingestion.
- Subscription renewal timing is not guessed. Opaque renewal/unsubscribe tokens and
  unvalidated vendor times remain private; `PlateSubscriptionInfo` exposes only normalized
  source/channel identity. Long-running renewal behavior remains provisional until a
  read-only conformance pilot validates units and firmware behavior.
- No live device call runs on import, parsing, or stream construction. Only the explicit
  `subscribe_plate_events()` method registers with a logged-in device.
