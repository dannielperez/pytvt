# NVR Face Recognition / AI support

pytvt support for TVT NVR back-end ("Enable Detection by NVR") face analytics
over the web CGI API, plus the Alarm Server push path. This page records the
command surface **and its validation status** — what is proven against a live
device vs. what still needs a capture or a device test.

Target used for validation: `NVMS-9000` firmware (a gate NVR with NVR-side face
detection enabled on a standard camera).

## Validated against a live NVR

| Method | CGI command | Notes |
|--------|-------------|-------|
| `NvrClient.query_ai_resource()` | `queryAIResourceDetail` | AI-compute pool + per-channel allocation. `total_occupancy` is a percent. |
| `NvrClient.query_nvr_face_detection(ch)` | `queryBackFaceMatch` | "Enable Detection by NVR" switch + schedule. Response omits `<status>` on success. |
| `NvrClient.set_nvr_face_detection(ch, on)` | **`editRealFaceMatch`** | Write paired with the `queryBackFaceMatch` read (note the asymmetric name). Validated via an idempotent write-back (no state change). |
| `NvrClient.query_face_db_groups()` | `queryFacePersonnalInfoGroupList` | allow / reject / limited groups. |
| `NvrClient.query_face_match_config(ch)` | `queryFaceMatchConfig` | Returns raw `<content>` (firmware-variable shape). |
| `NvrClient.search_face_events(ch, start, end)` | `searchImageByImageV2` | "By Event" face-event index; compact `<i>` records decoded to `FaceEvent` (channel, `img_id`, `frame_time`). Count matched the web client live. |
| `NvrClient.get_face_snapshot(ch, img_id, frame_time)` | `requestChSnapFaceImage` | Cropped-face JPEG (base64 CDATA). Returned a valid 464×464 JPEG live. |
| `NvrClient.query_alarm_server()` | `queryAlarmServerParam` | Push target: address/url/port/format + `alarm_types` (decimal codes; `16` = face match) + heartbeat. |
| `NvrClient.set_alarm_server(cfg)` | `editAlarmServerParam` | Validated via idempotent write-back (config unchanged, push stayed disabled). |
| `channel_guid(n)` / `Channel.guid` | — | `{0000000N-…}` GUID for per-channel AI commands. |
| `alarm_protocol.TVT_ALARM_CODES` | — | Extended with AI/face codes; shares `NET_SDK_N9000_ALARM_TYPE` space. |
| `alarm_server.AlarmServer` | — | Bounded TCP receiver; end-to-end unit-tested with a face frame. |
| CLI: `ai-resource`, `face-detection <ch>`, `face-db`, `alarm-server` | — | All run against the live NVR. |

## Pending / NOT yet validated — do not rely on without testing

1. **`set_alarm_server()` enabling a real push target — write path validated,
   real redirect NOT tested.** The command is accepted and the idempotent
   write-back is clean, but pointing the NVR at a live listener and confirming a
   face event actually arrives has not been exercised end-to-end (would change a
   production NVR's push config). **Test on a lab/again-revert NVR before relying
   on it.**

2. **Real-time NetSDK face subscription — NOT implemented.** `device_sdk`'s
   `PlateEventStream.ingest` silently drops any command that isn't
   `VEHICLE`/`NVR_VEHICLE`, so wiring `FACE_MATCH` into it would drop every face
   event. A correct version needs its own face-payload binary decoder
   (`parse_*_face_payload`), which **requires a captured live face callback
   payload** to reverse/validate. Deferred rather than shipped drop-silently.

3. **Face database person enrollment — NOT implemented.** Adding/removing people
   and face images lives in the web client's `facePersonnalInfoMgr` module, which
   was not retrievable statically. Read-side group listing
   (`query_face_db_groups`) is done; person-level writes await that module's
   command capture.

## How commands were discovered (reproducible)

The web client dispatches every action as `Communication.Request({url:"<cmd>", data:xml})`.
Commands/payloads were extracted from the device's own JS
(`http://<nvr>/js/app/AlarmCfg/{vfd,faceCompare,alarmServer}.js`) — e.g. grep
`url:"<cmd>"` and read the adjacent `GetRequestHeader(...)+"<condition>…"` payload
builder — then confirmed live via `NvrClient._post`. Use Python substring search,
not regex with large quantifiers, on the minified JS (catastrophic backtracking).
