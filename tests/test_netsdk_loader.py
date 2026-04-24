"""Tests for pytvt.netsdk.loader NAT companion validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pytvt.netsdk.loader import NetSdkUnavailable, ensure_nat_support, load_sdk


def _set_linux_platform(mock_platform) -> None:
    mock_platform.system.return_value = "Linux"
    mock_platform.machine.return_value = "x86_64"


class TestNatLoader:
    def test_load_sdk_missing_library_requires_external_sdk_installation(self) -> None:
        with (
            patch("pytvt.netsdk.loader.platform") as mock_platform,
            patch("pytvt.netsdk.loader._find_lib", return_value="libdvrnetsdk.so"),
            patch("pytvt.netsdk.loader._preload_companion_libraries"),
            patch("pytvt.netsdk.loader.ct.CDLL", side_effect=OSError("not found")),
        ):
            _set_linux_platform(mock_platform)

            with pytest.raises(NetSdkUnavailable, match="Install the vendor SDK separately"):
                load_sdk()

    def test_load_sdk_requires_nat_companion(self) -> None:
        with (
            patch("pytvt.netsdk.loader.platform") as mock_platform,
            patch("pytvt.netsdk.loader._find_lib", return_value="/opt/tvt/libdvrnetsdk.so"),
            patch(
                "pytvt.netsdk.loader._preload_companion_libraries",
                side_effect=NetSdkUnavailable("TVT AutoNAT requires libNatClientSDK.so"),
            ),
            patch("pytvt.netsdk.loader.ct.CDLL", return_value=MagicMock()),
        ):
            _set_linux_platform(mock_platform)

            with pytest.raises(NetSdkUnavailable, match="AutoNAT"):
                load_sdk(require_nat=True)

    def test_ensure_nat_support_preloads_nat_dependency(self) -> None:
        with (
            patch("pytvt.netsdk.loader.platform") as mock_platform,
            patch("pytvt.netsdk.loader._find_lib", return_value="/opt/tvt/libdvrnetsdk.so"),
            patch("pytvt.netsdk.loader._preload_companion_libraries") as mock_preload,
        ):
            _set_linux_platform(mock_platform)

            ensure_nat_support("/opt/tvt")

            mock_preload.assert_called_once_with(None, require_nat=True)
