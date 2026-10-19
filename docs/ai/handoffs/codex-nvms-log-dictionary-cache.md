# NVMS log dictionary request reduction

Cache successful operation-log type discovery and log-name enrichment within one authenticated backend session. Clear both caches on every login. Missing required types still fail closed; enrichment failure remains nonfatal and retryable. Log pages and their contents are never cached.

Live high-volume collection otherwise repeats two dictionary reads for every log page. This change reduces calls without increasing response size or dropping event types. 16 mocked web-log tests pass, including cross-page reuse and new-login invalidation; Ruff/format pass. Stacked on resource API PR113; no live production writes.
