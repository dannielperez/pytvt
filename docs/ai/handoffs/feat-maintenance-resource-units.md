# Maintenance resource units

Follow-up to merged #113 and #115. Expose memory_mib and uptime_seconds on the
existing MaintenanceResourceSample so application code need not parse vendor
strings. Unknown formats stay None. Existing percentage fields remain unchanged.

Validation: 48 maintenance/web-crypto tests passed; targeted lint and formatting
passed. This is needed by UniqueOS #5833. No live login or appliance mutation.
