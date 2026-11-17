# IPC HTTP POST config inspection (read-only)

`WebApiClient.query_http_post_config()` reads a TVT IPC's HTTP POST
(alarm/smart-event push) destinations so a caller can check where a camera
pushes before enrolling it. It returns an immutable `IpcHttpPostConfig`
(`pytvt.web_api.models`). `pytvt.web_api.http_post.http_post_target_matches()`
compares it with an expected receiver. Both are pure apart from the one GET.

## Provenance: DOC-derived, not captured

The command name and XML shape come from an OEM reference. They were NOT
captured from cameras installed for UniqueOS:

- <https://videos.cctvcamerapros.com/developer/docs/api-reference/alarm/http-post-webhook-config/>
  ("Tested: IPC v1.9 (firmware 5.1.4.0, API version 1.7)"). It defines
  `GetHttpPostConfig` ("Endpoint /GetHttpPostConfig, Method POST or GET,
  Products IPC, Request Body None") and the response
  `<config version="1.7" xmlns="http://www.ipc.com/ver10">` with `<httpPost>`
  (v1) and `<httpPostV2><postUrlConf><urlList maxCount="3">` (v2).
- The same page states that on v2.1 firmware the command "return[s] HTTP 400
  with errorCode="1" (Invalid Request)" and is not listed by `GetSupportedAPIs`.
  That surfaces as `WebApiError` (status 400).
- <https://videos.cctvcamerapros.com/developer/docs/api-reference/events/ipc-event-format/>
  documents the pushed payloads (keepalive, alarm status, smart data). It is
  context only: nothing here parses push bodies.

**Validation status: UNIT-only.** Synthetic fixtures are built from the OEM
example (`tests/test_webapi_http_post.py`). No live device has been queried.

## What is parsed

| Model field | v1 `httpPost` | v2 `urlList/item` |
|---|---|---|
| `enabled` | `bSwitch` | `switch` |
| `protocol` | `""` (no scheme field) | `url/protocol` (`http`/`https`) |
| `host` | `serverIp` | `url/domain` |
| `port` | `serverPort` | `url/port` |
| `path` | `URL` (absent on newer firmware) | `url/path` |
| `authentication` | — | `url/authentication` |
| `keepalive_seconds` | `keepaliveTimeval` | `keepaliveTimeval` |
| `heartbeat_enabled` | — | `heatBeatSwitch` |
| `online` | `onlineStatus` | — |
| `data_types` | — | `subscribeDateType` (5.1.x) or `subDataType` (5.3.1 / v2.1) |
| `events` | — | `subscriptionEvents` |

Credentials are never stored. The OEM page shows no user/password fields.
If firmware adds any (element names matching user/account/login or
pass/pwd/secret/token/key/credential/cert/private, or `user:pass@` in the
host), only `has_username` / `has_password` are set, and only for non-empty
values. A query string is removed from `path`, and `has_query` records that
it was there. Unmodelled leaf elements are kept in `unknown_fields` as
`(path, text)` pairs, so firmware drift stays visible. They are capped at 32
entries of 256 characters, and secret-looking names are dropped.

Anything the reference does not describe raises `NvrApiResponseShapeError`.
That includes a missing `httpPost`/`httpPostV2`, a `urlList` count mismatch,
more than `maxCount` slots, a duplicate `urlId`, a non-boolean switch, a bad
port, a foreign namespace, DOCTYPE/ENTITY, a body over 256 KiB or more than
2048 elements. An unrecognised shape is never reported as "empty" or
"disabled".

## Conflict detection

`http_post_target_matches(config, host=, port=, path=, protocol=)` returns
`HttpPostTargetMatch` with one of these statuses:

- `MATCH`: a slot for our host is enabled and its protocol, port and path are equal.
- `DIFFERS`: a slot names our host, but `differing_fields` (enabled/protocol/port/path) differ.
- `THIRD_PARTY`: no slot names our host, and an enabled slot pushes elsewhere.
- `NOT_CONFIGURED`: no slot names our host, and no enabled slot pushes elsewhere.

`third_party_targets` (enabled foreign slots) is filled in for every status, so
`MATCH` can still report a coexisting receiver. A v1 target never proves a
`MATCH` because v1 has no protocol field.

## Not implemented, on purpose

`SetHttpPostConfig` exists in the reference but has no wrapper. Writing a
camera's push config is a separate, approved change. The first live use of
this read must be a single read on one approved camera, and its result must be
compared against this document before relying on the parser fleet-wide.
