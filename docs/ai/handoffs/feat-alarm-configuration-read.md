# Alarm configuration read correction

The web Alarm/getAlarmInfoList endpoint returns linkage configuration, not event history. The web history method now raises CapabilityNotAvailable; native history is unchanged. A new list_alarm_configuration method reads one bounded page (25 channels maximum), retains nested destinations, rejects incomplete pages and duplicate channel IDs, and reports the lossy projection explicitly. Unknown action values are not exposed. No write method was added because scalar audio and missing SOP data cannot preserve a complete configuration.

Changed: client.py delegation, web_backend.py endpoint semantics, web_envelope.py bounded nested projection, new alarm_configuration.py parser, test_web_alarms.py regression tests.

Validation: full tests: 1600 passed, two packaging tests initially failed from expired private package-index authentication. Re-running packaging guardrail tests with the public PyPI index: 4 passed. Targeted web alarm/session tests: 44 passed. Changed-file Ruff passes. No live vendor calls in tests.

Risk: web callers previously receiving false historical records now receive an explicit unavailable exception. UI/native observation does not validate a production web login. Audio OFF was observed for pilot native-client intrusion rows despite duplicate stored audio entries; do not equate duplicates with audible alarms or guaranteed CPU savings.

Next: independent review, merge SDK before consuming gitlink. Verify complete native read/write/restore before any repair feature. Current SDK main has an unrelated memory-unit fix after this branch point; no overlap with this change.

## Continuation
CI exposed formatting drift in the new web backend method. Applied formatter and reran all SDK tests with public-index build dependencies: 1602 passed. Repository-wide SDK Ruff check and format check pass. No behavior or production changes in this follow-up.
