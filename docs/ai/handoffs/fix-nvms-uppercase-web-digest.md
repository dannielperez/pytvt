# NVMS uppercase web digest

Installed NVMS uses uppercase MD5 hex before the SHA256 login proof and as the ASCII AES authentication-ID key. Correct the shared helper and add fixed login/AES vectors.

Validation: 53 crypto/session tests pass; scoped Ruff checks pass. Live read-only login with the dedicated UniqueOS account succeeded and returned 19 server rows during incident validation. No secrets or raw production logs are included.

Risk: other NVMS firmware variants have not been validated. No automatic case retries were added because failed logins can lock accounts. Production deployment and UniqueOS vendor update remain pending owner review.
