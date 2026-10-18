# Lightweight NVMS resource sampling

Adds typed CPU/memory percentage samples and bounded uptime text from querySystemInfo without log compression. Unknown/invalid percentages remain unknown, including ambiguous memory units. Adds explicit authentication-rejection exception for caller circuit breakers, without retrying credentials.

Validation: maintenance unit tests, lint and formatting. No live authentication attempts or vendor writes. UniqueOS must bind a separately selected appliance credential before scheduling this API; memory/swap/OOM data unavailable through this endpoint must remain uncovered.
