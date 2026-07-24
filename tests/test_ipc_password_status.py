"""Tests for NvrClient.edit_ipc_password_status (granular IPC password change).

Distinguishes a real change from the idempotent "already on target" case
(errorCode 536870962) and surfaces the errorCode on any other failure — the
primitive that lets a caller force-rotate online channels safely.
"""

import pytest

from pytvt.models import NvrApiError
from pytvt.xml_api import IPC_PASSWORD_ALREADY_SET, NvrClient


def _client(post_response):
    client = NvrClient("10.0.0.1", "admin", "pass")
    client._logged_in = True
    client._session_key = "session-key"
    client._security_ver = "1"
    client._encrypt_for_session = lambda plaintext, session_key: "ENC"  # type: ignore[method-assign]
    client._post = lambda path, body: post_response
    return client


def test_changed_on_success():
    client = _client("<response><status>success</status></response>")
    assert client.edit_ipc_password_status("dev-1", new_password="New@2026") == "changed"


def test_already_set_on_idempotent_code():
    client = _client(f"<response><status>failed</status><errorCode>{IPC_PASSWORD_ALREADY_SET}</errorCode></response>")
    assert client.edit_ipc_password_status("dev-1", new_password="New@2026") == "already-set"


def test_other_error_raises_with_code():
    client = _client("<response><status>failed</status><errorCode>536870931</errorCode></response>")
    with pytest.raises(NvrApiError) as exc_info:
        client.edit_ipc_password_status("dev-1", new_password="New@2026")
    assert exc_info.value.error_code == "536870931"


def test_requires_session_key():
    client = NvrClient("10.0.0.1", "admin", "pass")
    client._logged_in = True
    client._session_key = None
    with pytest.raises(NvrApiError):
        client.edit_ipc_password_status("dev-1", new_password="x")
