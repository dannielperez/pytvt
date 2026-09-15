# A510 maintenance audit

`pytvt.maintenance.MaintenanceClient` uses the appliance's :8000 DeviceWeb API. It authenticates once, captures `querySystemInfo`, requests `operationsLogDownLoad`, streams a same-origin ZIP, reads a second resource sample and logs out. HTTPS verifies the server certificate. Rejected logins are not retried.

`collect_audit(client, tz_name=..., start=..., end=...)` returns normalized observations and coverage metadata. Server log timestamps require an explicit timezone; they do not carry their own offset. The collector does not change vendor configuration. Export compression adds load.

The analyzer streams members without extracting paths. Limits: 1 GB compressed download, 8 GB expanded size, 512 members, 900-second download budget, 600-second analysis budget. Oversized log lines are skipped and counted. Raw lines, user passwords, database backup command text and license XML are never returned. The archive is held in a private temporary file and removed on exit. SHA-256 and actual service coverage are retained.

Counts represent matching log messages, not unique source alarms, deliveries, dropped events or root causes. Rotation can omit history. Missing signatures do not prove recovery. Recognized evidence includes worker capacity, device authority, linkage activity, heartbeat disconnects, multicast failures, decoder overrun, registration rejection, restart signatures and decoder-output ceiling. Other warning/error/fatal records are counted without copying their text.

Validation: synthetic protocol/archive tests plus a three-file sample from the September 15 export matched the independent AlarmServer recount (172,450 linkage rows). Live authentication/download using this new Python client has not been validated against the deployed appliance; the endpoint and hashing contract come from its DeviceWeb JavaScript.
