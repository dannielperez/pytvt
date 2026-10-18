# Authentication interface boundaries

The Python package contains adapters for several vendor interfaces. Being in
`pytvt` does not make an HTTP adapter a native SDK transport. Select the interface
and its credential explicitly; never try different accounts or hash variants to
discover which one works.

| Interface | Implementation | Password handling |
| --- | --- | --- |
| A510 DeviceWeb maintenance, normally port 8000 | `maintenance.MaintenanceClient` | Plaintext maintenance-account input; sends SHA256(uppercase MD5 hex + nonce) in XML to `/doLogin`. |
| NVMS management web | `platform_sdk.web_session`, `web_crypto` | Same password-proof primitive; separate `/service/login/` handshake, username/session encryption and account context. Not interchangeable with DeviceWeb. |
| Native platform library | `platform_sdk.platform_backend` | Passes UTF-8 password to the vendor login function. The wrapper must not apply the DeviceWeb hash. |
| Device/NVR XML | `xml_api` | Its nonce flow uses SHA512(uppercase MD5 hex + `#` + nonce); legacy flow differs. |
| Head-variant binary device protocol | `protocol._encrypt_password_head` | SHA1 of uppercase MD5 hex plus the formatted numeric nonce, returning raw bytes. |

These are descriptions of the current adapters, not a claim that every firmware
uses the same scheme. Hashing a challenge proof is distinct from encrypting the
transport. Do not change a native or device protocol to match a web protocol.

The maintenance uppercase correction is grounded in the incident appliance's
served `js/app/login.js` and `js/lib` SparkMD5 source captured on 2026-10-18:
login calls `SparkMD5.hash(pwd)` then `CryptoJS.SHA256(password+nonce)`, and the
served SparkMD5 implementation returns uppercase hex. Offline regression tests
check the maintenance request and existing management-web helper against the
same non-secret vector. Successful live maintenance authentication is still
unverified; this evidence does not establish the cause of a rejected login.

Credentials remain scoped to an account and interface even when the underlying
hash primitive matches. Configuration should identify interface, endpoint,
credential reference, and firmware/protocol evidence. Missing credentials or a
lockout must stop attempts, rather than initiate account or protocol fallback.

Evidence provenance is separate from transport support: a file downloaded by
an HTTP adapter is a web-endpoint export, but must not be described as a browser
GUI download. Record the actual export method, collection time, incident window,
and checksum. A GUI-only evidence request requires the actual GUI workflow.
