"""Locate and load the libdvrnetsdk.so shared library.

Search order:
1. ``$PYTVT_NETSDK_LIB`` environment variable (explicit path)
2. ``<package>/lib/libdvrnetsdk.so`` (vendored alongside this module)
3. System library path (LD_LIBRARY_PATH)

Raises :class:`NetSdkUnavailable` if the library cannot be loaded
(wrong platform, missing file, etc.).
"""

from __future__ import annotations

import ctypes as ct
import os
import platform
from pathlib import Path


class NetSdkUnavailable(ImportError):
    """Raised when the native SDK library cannot be loaded."""


def _find_lib() -> str:
    """Resolve the path to libdvrnetsdk.so."""
    # 1. Explicit override
    env = os.environ.get("PYTVT_NETSDK_LIB")
    if env:
        return env

    # 2. Vendored copy next to this file
    pkg_lib = Path(__file__).parent / "lib" / "libdvrnetsdk.so"
    if pkg_lib.exists():
        return str(pkg_lib)

    # 3. System search
    return "libdvrnetsdk.so"


def load_sdk() -> ct.CDLL:
    """Load and return the SDK shared library handle.

    Returns:
        ctypes CDLL handle for libdvrnetsdk.so.

    Raises:
        NetSdkUnavailable: On non-Linux or when the library is missing.
    """
    if platform.system() != "Linux":
        raise NetSdkUnavailable(
            f"TVT NetSDK requires Linux (current: {platform.system()}). "
            "Set $PYTVT_NETSDK_LIB to override."
        )

    arch = platform.machine()
    if arch not in ("x86_64", "aarch64"):
        raise NetSdkUnavailable(
            f"TVT NetSDK supports x86_64 and aarch64 (current: {arch})."
        )

    lib_path = _find_lib()
    try:
        return ct.CDLL(lib_path)
    except OSError as exc:
        raise NetSdkUnavailable(
            f"Cannot load TVT NetSDK from {lib_path!r}: {exc}. "
            "Ensure libdvrnetsdk.so and its dependencies "
            "(libShareLib.so, libNatClientSDK.so, libcrypto.so.1.1) "
            "are available."
        ) from exc
