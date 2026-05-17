"""Tests for pytvt.web_api.errors — exception mapping and raise_for_status."""

from __future__ import annotations

import pytest

from pytvt.web_api.errors import (
    AuthenticationError,
    DeviceOfflineError,
    PasswordExpiredError,
    PermissionDeniedError,
    ServiceNotEnabledError,
    SystemBusyError,
    UnsupportedFunctionError,
    WebApiError,
    raise_for_status,
)


class TestExceptionHierarchy:
    def test_all_inherit_from_webapi_error(self):
        for cls in (
            AuthenticationError,
            PasswordExpiredError,
            PermissionDeniedError,
            UnsupportedFunctionError,
            ServiceNotEnabledError,
            SystemBusyError,
            DeviceOfflineError,
        ):
            assert issubclass(cls, WebApiError)

    def test_webapi_error_stores_codes(self):
        exc = WebApiError("test", status_code=400, sub_code=12)
        assert exc.status_code == 400
        assert exc.sub_code == 12
        assert str(exc) == "test"

    def test_webapi_error_defaults(self):
        exc = WebApiError("msg")
        assert exc.status_code == 0
        assert exc.sub_code == 0


class TestRaiseForStatus:
    def test_200_0_does_nothing(self):
        raise_for_status(200, 0)  # should not raise

    def test_200_0_with_string_does_nothing(self):
        raise_for_status(200, 0, "OK")  # should not raise

    def test_401_raises_authentication_error(self):
        with pytest.raises(AuthenticationError) as exc_info:
            raise_for_status(401, 0, "bad creds")
        assert exc_info.value.status_code == 401

    def test_401_28_raises_password_expired(self):
        with pytest.raises(PasswordExpiredError) as exc_info:
            raise_for_status(401, 28)
        assert exc_info.value.sub_code == 28

    def test_403_raises_permission_denied(self):
        with pytest.raises(PermissionDeniedError):
            raise_for_status(403, 0)

    def test_404_raises_unsupported(self):
        with pytest.raises(UnsupportedFunctionError):
            raise_for_status(404, 0)

    def test_400_12_raises_unsupported(self):
        with pytest.raises(UnsupportedFunctionError) as exc_info:
            raise_for_status(400, 12)
        assert "Unsupported function" in str(exc_info.value)

    def test_400_34_raises_system_busy(self):
        with pytest.raises(SystemBusyError):
            raise_for_status(400, 34)

    def test_400_28_raises_password_expired(self):
        with pytest.raises(PasswordExpiredError):
            raise_for_status(400, 28)

    def test_400_generic_raises_webapi_error(self):
        with pytest.raises(WebApiError):
            raise_for_status(400, 0)

    def test_unknown_code_raises_webapi_error(self):
        with pytest.raises(WebApiError) as exc_info:
            raise_for_status(500, 0)
        assert "Unknown error" in str(exc_info.value)

    def test_status_string_appended(self):
        with pytest.raises(WebApiError) as exc_info:
            raise_for_status(400, 5, "foobar detail")
        msg = str(exc_info.value)
        assert "foobar detail" in msg

    def test_status_string_not_duplicated(self):
        with pytest.raises(WebApiError) as exc_info:
            raise_for_status(400, 34, "System busy")
        msg = str(exc_info.value)
        # "System busy" is already in the description, shouldn't be appended
        assert msg.count("System busy") == 1

    def test_sub_code_shown_in_message(self):
        with pytest.raises(WebApiError) as exc_info:
            raise_for_status(400, 12)
        assert "400/12" in str(exc_info.value)
