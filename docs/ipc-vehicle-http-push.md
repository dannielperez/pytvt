# IPC vehicle HTTP push evidence

Live IPC 1.7 VEHICLE payloads from IP-5IR4A3H4-MZ-LR firmware
5.2.2.7101B241021.IC5.U1(13A10) can append bytes after JPEG EOI in
both the overview and plate-crop base64 buffers. A complete live example had
one extra byte on each image. Rejecting a JPEG solely because EOI is not the
last buffer byte rejects valid vehicle observations.

The decoder keeps the existing decoded-size limit and JPEG SOI/EOI checks,
and returns only the image through its last EOI marker. It does not retain
transport trailers. Complete image integrity remains the consumer's job;
framing alone must not be treated as image validation or authorization.

Synthetic regression fixtures contain no real plates or images. Validation:
51 tests in test_vehicle_push.py and test_plate_events.py passed. Live capture
accepted approach direction, confidence and capture timestamp after this fix.
Camera clock synchronization must be independently checked; receipt time is
never substituted for capture time.

The subscription also sends alarmStatusInfo/vehiceAlarm boolean status frames.
These decode to no observations so consumers can acknowledge them without
inventing an arrival. Mixed status/untyped detection envelopes remain rejected.

## Decoded fields and validation status

Status key: **LIVE** = asserted against a live capture from the camera above;
**DOC/UNIT** = shape and values taken from the OEM reference
(CCTV Camera Pros "IPC Event Format (v1.x)", LPR-IP4 firmware 5.3.1,
https://videos.cctvcamerapros.com/developer/docs/api-reference/events/ipc-event-format/)
and covered only by synthetic unit fixtures. No live capture is kept as a test
fixture, so every field added after the first release is DOC/UNIT only.

| Wire field (per item unless noted) | `PlateEvent` slot | Rule | Status |
|---|---|---|---|
| `plateNumber` | `plate` | ≤ 32 chars | LIVE |
| `PlateConfidence@count` | `confidence` | 0–10000 → floor percent | LIVE |
| root `currentTime` | `occurred_at` | 16-digit Unix µs, never receipt time | LIVE |
| `vehicleDirect` | `direction` | `approach`/`away` only | LIVE (`approach`) |
| `targetImageData` (item 0 / plate item) | `full_image` / `plate_image` | JPEG framing | LIVE |
| `plateCharCount` | `declared_plate_char_count` | integer 0–32, else `None` | DOC/UNIT |
| `leftTop`, `rightBottom` x/y | `plate_rect` | `(left, top, right, bottom)`, normalized 0–10000 (NetSDK units may differ) | DOC/UNIT |
| `PlateWidth`, `PlateHeight` | `plate_size` | crop pixels, both > 0 | DOC/UNIT |
| `SourceImageWidth`, `SourceImageHeight` | `full_image_size` | frame pixels, both > 0 | DOC/UNIT |
| `vehicleListType` | `edge_match` | `whiteList`→`ALLOWLIST`, `blackList`→`DENYLIST`; anything else or absent → `UNKNOWN`; `edge_match_code` stays `None` | DOC/UNIT |
| `plateColor@count` | `plate_color_code` | raw integer; `plate_color` stays `UNKNOWN` (code table undocumented; NetSDK table not assumed) | DOC/UNIT |
| `plateColorRate`, `plateStyle`, `vehiceColor`, `plateAngleH`, `plateAngleV` (`@count`) | `vendor_attributes` | raw string; meaning undocumented | DOC/UNIT |
| `vehiceId`, `province`, `vehicleListType`, `carAttr/{color,type,brand,model}`, `carRect/{x,y,width,height}` | `vendor_attributes` | raw string; no enum mapping (vocabulary undocumented, carRect units unconfirmed) | DOC/UNIT |
| root `deviceName`/`mac`/`sn`, root `deviceInfo/{deviceName,mac,macAddress,sn,ip,ipAddress}` | `source_device` | self-reported; conflicting values dropped | DOC/UNIT |

Deliberately left as raw strings or unread: `vehicle_color`, `vehicle_type`,
`vehicle_type_code`, `vehicle_color_code`, `vehicle_brand_code` and
`plate_color_confidence`. The reference gives `carAttr` as free text and the
numeric `plateStyle`/`vehiceColor`/`plateColorRate` meanings as undocumented, so
mapping them to the NetSDK code tables would be invented. Root `channelId`,
`plateCount` and `rightTop`/`leftBottom` are not read.

The newly read fields are best-effort and never reject a payload that the
original decoder accepted. A duplicated (ambiguous), over-128-character,
out-of-range or inverted value, or conflicting device identity, leaves that slot
`None`/absent and is named as `("_dropped", "<field>")` in `vendor_attributes`
(for example `plateCharCount`, `plate_rect`, `source_device/mac`). Names come from
the decoder's fixed field lists, so the entry count is bounded. Nothing is added
to `warnings`. Rejection rules for the original fields (plate, confidence,
direction, capture time, images, envelope) are unchanged. `source_device` is what
the sender says about itself: receivers may cross-check it against the bound
camera, but pytvt does not use it for authentication or authorization.

`is_partial` is deliberately never set on the push path (always `False`, as
before). A downstream consumer (UniqueOS vehicle arrival assessment) treats
`is_partial` and `warnings` as deny signals, so marking missing evidence here as
the NetSDK path does would change live arrival decisions. Changing it needs a
coordinated consumer change.

To check where a camera pushes before enrolment, use the read-only
`WebApiClient.query_http_post_config()`. See
[ipc-http-post-config.md](ipc-http-post-config.md). It is DOC-derived and
validated by unit tests only.
