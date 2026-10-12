# Alarm-server push fixtures

| File | Provenance | Status |
|---|---|---|
| `ipc_v1_9_send_alarm_status_motion.xml` | TVT "TVTAPI v1.9 - Draft, For IP Media Device", §5.3.2 Tips 2 — "The data sent to the server when the alarm is issued" | **DOCUMENTED** (vendor example, not a live capture) |
| `ipc_v1_9_heartbeat.xml` | same document, §5.3.2 Tips 3 — "The heartbeat data send to server" | **DOCUMENTED** |
| `ipc_v1_9_alarm_status_full.xml` | same document, §5.3.1 `GetAlarmStatus` response; §5.3.4 says SendAlarmStatus carries "the whole alarmStatusInfo element" of it | **DOCUMENTED** shape; the `true` values here are set by the test author to exercise multiple active alarms |

The vendor example's device identity (name, serial, addresses) is the
document's own placeholder data.

**NVR push (CAPTURED 2026-10-12):** only the **heartbeat** has been captured
so far. It is `<DeviceInfo>` at the root, with no `<config>` wrapper and no
namespace, sometimes preceded by an XML declaration. It uses `DeviceName` / `SN`
capitalisation and a colon-separated MAC, and is posted as
`application/soap+xml`. The NVR **alarm** body has not been captured yet and
stays `unknown`.
| `nvr_captured_heartbeat_http.bin` | **Live capture** 2026-10-12 from a TVT NVR's Alarm Server push (UniqueOS receiver). Full HTTP request. Values sanitised (device name, serial, IP, MAC replaced with placeholder / RFC 5737 / RFC 7042 documentation values); structure, header set, tabs and CDATA preserved verbatim. | **CAPTURED** |
| `nvr_captured_heartbeat_decl_http.bin` | Same NVR, second captured heartbeat variant: identical body preceded by an `<?xml version="1.0" encoding="UTF-8"?>` declaration and trailing newline. | **CAPTURED** |
| `nvr_captured_motion_raise_http.bin` | **Live capture** 2026-10-12, same NVR: alarm push `<config><alarmStatusInfo><motionAlarm type="boolean" id="5" name="IP Camera">true…`, `<DataTime>`, nested `<DeviceInfo>`. Device identity sanitised; element names, attributes, camera name, ids, timestamps and whitespace verbatim. | **CAPTURED** |
| `nvr_captured_motion_clear_http.bin` | Same NVR: `motionAlarm id="12" name="LPR Entrada"` **false** (explicit clear) | **CAPTURED** |
| `nvr_captured_front_offline_raise_http.bin` | Same NVR: `frontOfflineAlarm id="12" name="LPR Entrada"` **true** (element not in the IPC document) | **CAPTURED** |

**Channel numbering (CAPTURED, 2026-10-12):** in the NVR dialect the `id`
attribute is the **1-based channel shown in the NVR UI**. Verified against the
recorder's own inventory: `id="12" name="LPR Entrada"` is channel 12 "LPR
Entrada" (NetSDK index 11), and `id="13" name="LPR PORTON SALIDA"` is channel 13.
`DataTime` matched neither UTC nor local receipt time on the capture (recorder
clock or timezone unknown), so it is kept raw.
