# Maintenance web authentication correction

The A510 DeviceWeb JavaScript served by the incident appliance uses uppercase
MD5 hex as input to SHA-256(passwordDigest + nonce). MaintenanceClient previously
used lowercase MD5. Correct only that web adapter; leave native SDK and device
protocol authentication unchanged. Document interface and evidence provenance
boundaries in docs/authentication-boundaries.md.

Validation: 31 tests passed across test_maintenance.py and test_web_crypto.py;
targeted Ruff lint and format checks passed. Tests use dummy credentials and
mocked I/O. No successful live Python-client login is claimed. The owner signed
into the browser separately; this does not validate the Python client. Earlier
rejected attempts reached lockout, so no credential/hash fallback is appropriate.

Related, independent work: pytvt #113 adds resource samples. This correction is
not included there as inspected on October 18. Preserve both changes during
integration. No deployment or server configuration changes are included.

Implementation continuation: incorporated existing PR #113 resource sampling and
added memory-size/uptime normalization, with unsupported values kept unknown.
48 maintenance/web-crypto tests pass. These typed values support UniqueOS capture
policy without putting vendor parsing in Django. No live credential proof has
been claimed from offline tests.
