# Alarm-server push fixtures

| File | Provenance | Status |
|---|---|---|
| `ipc_v1_9_send_alarm_status_motion.xml` | TVT "TVTAPI v1.9 - Draft, For IP Media Device", §5.3.2 Tips 2 — "The data sent to the server when the alarm is issued" | **DOCUMENTED** (vendor example, not a live capture) |
| `ipc_v1_9_heartbeat.xml` | same document, §5.3.2 Tips 3 — "The heartbeat data send to server" | **DOCUMENTED** |
| `ipc_v1_9_alarm_status_full.xml` | same document, §5.3.1 `GetAlarmStatus` response; §5.3.4 says SendAlarmStatus carries "the whole alarmStatusInfo element" of it | **DOCUMENTED** shape; the `true` values here are set by the test author to exercise multiple active alarms |

The vendor example's device identity (name, serial, addresses) is the
document's own placeholder data. **No NVR (NVMS-9000) push capture exists yet**:
the NVR web UI uses a different XML dialect (`<request ... clientType="WEB">`)
and its Alarm Server payload must be captured before it is parsed.
