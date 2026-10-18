# NVMS device status scope and pagination

Issue: UniqueOS #5789. Authorized production read-only probes confirmed blank areaId returns successful zero devices; nine visible root areas report 251 devices. Installed webmonitor secFrameworkMaster and systemStatus/deviceStatus JavaScript confirms organization IDs, XML item attributes, total attribute and UTC timestamps.

Changes: preserve id/pId and content attributes in envelopes; query every authorized root, page using advertised totals, bound collection to 100 requests/60 seconds plus the in-flight request timeout; reject cycles, missing IDs, repeated rows, changed totals and incomplete pages. Strip device credential fields through an allowlist. Add optional typed device name, preserve UTC times.

Validation: mocked web tests and full SDK tests (results in PR); read-only production process validation returned 251 unique IDs, 196 online and 55 offline, no credential fields. This probe did not deploy code or write mirrors. No production credentials, permissions, streams or service restarts changed.

Risk: snapshot can fail explicitly during inventory churn; next poll retries. An empty organization scope is an error rather than healthy empty inventory. No inventory/channel enumeration added. Device states reflect management connectivity, not successful audio/video playback.
