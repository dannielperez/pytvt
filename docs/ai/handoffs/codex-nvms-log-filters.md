# NVMS web log request compatibility

Installed NVMS controller requires `types[]`, UTC start/end timestamps and a
`lang_id` cookie. Browser uses 0x0409 by default. Discover operation types from
supported sections of getLogEventDictionary rather than inventing enums.
Status requests use the installed UI's LOG_STATE_ALL.

Changes: repeated form encoding, dictionary section extraction, required filters,
bounded default 15-minute UTC window, browser language cookie.
Validation: 195 web SDK tests pass; corrected process-only live diagnostic with
uniqueos account logged in, read 19 server statuses and 10 records from each log.
No production deployment is implied by that diagnostic. Explicit historical
exports must supply their date range; this default is for current polling.
