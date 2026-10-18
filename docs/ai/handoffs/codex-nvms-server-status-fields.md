# NVMS server status attributes

The October 18 production server-status response contains attribute-only items: id, name, ip, port, type, state and UTC lastOnline/lastOffline fields. The envelope retained only id, losing names, roles, endpoint and online state. Preserve the explicitly allowlisted operational attributes; arbitrary credential/session attributes remain excluded. Normalize observed RSU/MDU/ADU/TVW/JSU/ANS role codes; unknown vendor codes remain explicit. Carry typed endpoint and vendor role through PlatformServerStatus.as_dict. Reject anonymous server rows instead of inventing index identities. Legacy name-identified element responses remain supported.

Evidence inspected through one authorized existing session; no secrets or original production payload committed. Tests use synthetic hosts and fixtures. No service configuration or credentials changed. App follow-up marks unobserved historical server rows stale without deleting links and persists the typed endpoint fields. Counts indicate status, not verified media or forwarding capacity.

Validation: targeted and broader web/maintenance tests, Ruff checks. Base is the combined SDK branch preserving resource sampling, dictionary caching and maintenance proof correction.
