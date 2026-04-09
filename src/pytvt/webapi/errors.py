"""TVT HTTP API error codes and exceptions.

Error codes from Section 1.3.6 of the TVT HTTP API Protocol User Guide v2.0.0.
"""

from __future__ import annotations


class WebApiError(Exception):
    """Base exception for TVT Web API errors."""

    def __init__(self, message: str, *, status_code: int = 0, sub_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.sub_code = sub_code


class AuthenticationError(WebApiError):
    """401 — wrong credentials or session expired."""


class PasswordExpiredError(WebApiError):
    """401 subCode=28 — password has expired and must be changed."""


class PermissionDeniedError(WebApiError):
    """403 — insufficient permissions for the requested operation."""


class UnsupportedFunctionError(WebApiError):
    """400 subCode=12 — device does not support the requested function."""


class ServiceNotEnabledError(WebApiError):
    """The HTTP API service is not enabled on the device."""


class SystemBusyError(WebApiError):
    """400 subCode=34 — system is busy, try again later."""


class DeviceOfflineError(WebApiError):
    """The device is not reachable or refused the connection."""


# ── Error code mapping ───────────────────────────────────────────────

# statusCode → subCode → (ExceptionClass, human description)
# Based on Section 1.3.6 of the protocol document.

ERROR_MAP: dict[int, dict[int, tuple[type[WebApiError], str]]] = {
    200: {
        0: (WebApiError, "OK"),
    },
    400: {
        0: (WebApiError, "Bad request"),
        5: (WebApiError, "Parameter out of range"),
        7: (WebApiError, "Invalid XML format"),
        9: (WebApiError, "Invalid XML content"),
        12: (UnsupportedFunctionError, "Unsupported function"),
        14: (WebApiError, "Address already in use"),
        17: (WebApiError, "Duplicate item"),
        19: (WebApiError, "Item does not exist"),
        22: (WebApiError, "Maximum items exceeded"),
        28: (PasswordExpiredError, "Password expired"),
        31: (WebApiError, "Upgrade in progress"),
        32: (WebApiError, "No disk present"),
        34: (SystemBusyError, "System busy"),
    },
    401: {
        0: (AuthenticationError, "Unauthorized — wrong credentials"),
        28: (PasswordExpiredError, "Password expired"),
    },
    403: {
        0: (PermissionDeniedError, "Permission denied"),
    },
    404: {
        0: (UnsupportedFunctionError, "Endpoint not found"),
    },
}


def raise_for_status(status_code: int, sub_code: int = 0, status_string: str = "") -> None:
    """Raise the appropriate exception for a TVT API error response.

    Does nothing if ``status_code`` is 200 and ``sub_code`` is 0 (success).
    """
    if status_code == 200 and sub_code == 0:
        return

    bucket = ERROR_MAP.get(status_code, {})
    entry = bucket.get(sub_code) or bucket.get(0)

    if entry:
        exc_class, description = entry
    else:
        exc_class = WebApiError
        description = f"Unknown error"

    msg = f"TVT API error {status_code}"
    if sub_code:
        msg += f"/{sub_code}"
    msg += f": {description}"
    if status_string and status_string.lower() not in description.lower():
        msg += f" ({status_string})"

    raise exc_class(msg, status_code=status_code, sub_code=sub_code)
