# IPC vehicle HTTP push decoding

`pytvt.vehicle_push.parse_ipc_vehicle_push(body, received_at=..., channel_id=...)`
converts captured IPC XML 1.7 VEHICLE/VEHICE listInfo messages into existing
PlateEvent DTOs. Callers must authenticate the sender and bind its camera/channel.
No listener, network operation, authorization or relay action is added.

The decoder separates the empty-plate overview item from plate items, accepts
base64 whitespace, bounds bodies to 8 MiB and images to 4 MiB, rejects duplicate
fields/unsupported namespaces/DTD/entities, and preserves unknown safety fields.
JPEG framing is checked; full image integrity remains a consumer responsibility.
Confidence uses the count attribute (0–10000) and is rounded down to percent.
Capture timestamps are Unix microseconds, never replaced with receipt time.
Only explicit approach/away directions are normalized. Replay IDs are independent
of receipt time. NVR v2 messages are unsupported.

The listInfo layout is grounded in captured TVT IPC 1.7 evidence. Timestamp units
and direction names are corroborated by the related manufacturer reference:
https://videos.cctvcamerapros.com/developer/docs/applications/license-plate-recognition-camera-api/
This is not installed-firmware acceptance; verify clock/direction/image encoding
with live camera events before enabling state-changing downstream actions.

Validation: 22 decoder tests and 24 existing native plate-event tests pass.
