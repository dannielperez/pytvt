"""Tests for ManagementClient's "web" backend selection (TVT-4).

No network: `_select_backend()` is exercised directly (mirrors how the other
forced-mode branches are unit-tested) rather than a full `login()` round trip,
since `WebManagementBackend` opens a live `WebSession` when no `transport` is
injected and `ManagementClient` does not expose one for the web mode.
"""

from __future__ import annotations

from pytvt.platform_sdk.client import ManagementClient
from pytvt.platform_sdk.native import NativeManagementBackend
from pytvt.platform_sdk.web_backend import WebManagementBackend


def test_web_backend_mode_selects_web_management_backend() -> None:
    client = ManagementClient("nvms.example", backend_mode="web")

    backend = client._select_backend()

    assert isinstance(backend, WebManagementBackend)


def test_web_backend_mode_forwards_web_ctor_params() -> None:
    client = ManagementClient(
        "nvms.example",
        backend_mode="web",
        web_scheme="https",
        web_port=8443,
        web_timeout=5.0,
        web_verify_tls=False,
    )

    backend = client._select_backend()

    assert isinstance(backend, WebManagementBackend)
    assert backend.host == "nvms.example"
    assert backend.scheme == "https"
    assert backend.port == 8443
    assert backend.timeout == 5.0
    assert backend.verify_tls is False


def test_backend_name_reports_web_once_selected() -> None:
    client = ManagementClient("nvms.example", backend_mode="web")
    client._backend = client._select_backend()

    assert client.backend_name == "web"


def test_web_mode_never_reports_sdk_support() -> None:
    client = ManagementClient("nvms.example", backend_mode="web", sdk_path="/opt/tvtsdk/libTVTNet.so")

    assert client.supports_sdk() is False


def test_auto_mode_does_not_fall_back_to_web() -> None:
    """ "auto" stays SDK-first -> native_protocol; "web" is opt-in only, never implicit."""
    client = ManagementClient("nvms.example", backend_mode="auto")

    backend = client._select_backend()

    assert isinstance(backend, NativeManagementBackend)
