# Handoff — `fix/platform-jpeg-framing`

- **Objective:** make bounded PlatformSDK JPEG capture tolerate C-buffer NUL padding while retaining strict, non-sensitive framing validation.
- **Changed:** the PlatformSDK wrapper now requires a JPEG SOI marker at byte zero, strips only trailing NUL padding, requires the remaining payload to end at an EOI marker, and returns that exact framed JPEG. Other failures report byte counts/categories without image contents or credentials.
- **Why:** UAT PlatformSDK login, typed inventory, GUID projection, callback warm-up, and persistent-session reuse are healthy, but three real online camera captures returned bytes rejected by the prior `endswith(EOI)` check.
- **Validation:** targeted PlatformSDK capture tests and the complete PlatformSDK test module pass; Ruff check/format pass.
- **Risk:** bounded SDK-wrapper-only parsing change. No Django/vendor leakage, retries, connection multiplication, file API, disk writes, or automatic capture enablement.
- **Next:** run wrapper risk reviews, publish a draft PR, repin `pytvt-runtime`, deploy the reviewed chain to UAT, and repeat two sequential captures. If UAT still fails, the new payload-free error category will distinguish missing SOI, missing EOI, and non-NUL trailer without exposing media.
