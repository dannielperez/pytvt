# Complete channel status reads

Confirmed against installed live.jsp.js and the production read-only API: Common/getNodeList returns 5,411 channels with total=5411; OrgRes/getOfflineChlList returns 1,570 IDs, all present in inventory. Legacy OrgRes queryNodeList is unavailable (404). State fields in the channel tree are empty, so do not interpret them as healthy.

Implement list_channels from the complete tree plus offline set using the same semantics as the installed client. Require a positive bounded total, exact count, unique/nonempty identities and a successful offline subset. Preserve unknown recorder parent IDs rather than inferring from names/IPs. Only safe explicit raw fields retained. XML request helper inserts the current token and rebuilds it once after session expiry without modifying caller payloads.

Tests: mocked protocol coverage includes partial counts, missing/duplicate IDs, out-of-scope offline IDs, failed offline response, redaction and reauthentication token freshness. Live collection proved platform connection status only, not media playback or audio quality.
