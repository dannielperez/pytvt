"""Locate and load the libdvrnetsdk.so shared library.

Search order:
1. ``$PYTVT_NETSDK_LIB`` environment variable (explicit path)
2. ``<package>/lib/libdvrnetsdk.so`` (vendored alongside this module)
3. ``tvt-api/tvt/bin/linux*/`` submodule in the project tree
4. System library path (LD_LIBRARY_PATH)

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


def _arch_dir() -> str:
    """Return the platform-specific lib directory name."""
    arch = platform.machine()
    if arch == "aarch64":
        return "linux-arm64"
    return "linux"


def _find_lib() -> str:
    """Resolve the path to libdvrnetsdk.so."""
    # 1. Explicit override
    env = os.environ.get("PYTVT_NETSDK_LIB")
    if env:
        return env

    # 2. Vendored copy next to this file  (<package>/netsdk/lib/)
    pkg_lib = Path(__file__).parent / "lib" / "libdvrnetsdk.so"
    if pkg_lib.exists():
        return str(pkg_lib)

    # 3. tvt-api submodule in the project tree (walk up to find it)
    arch_dir = _arch_dir()
    anchor = Path(__file__).resolve().parent  # .../src/pytvt/netsdk/
    for parent in anchor.parents:
        candidate = parent / "tvt-api" / "tvt" / "bin" / arch_dir / "libdvrnetsdk.so"
        if candidate.exists():
            return str(candidate)
        # Also check tvt repo directly (e.g. if cloned alongside)
        candidate = parent / "tvt" / "bin" / arch_dir / "libdvrnetsdk.so"
        if candidate.exists():
            return str(candidate)

    # 4. System search
    return "libdvrnetsdk.so"


def _lib_dir(lib_path: str) -> str | None:
    """Return the directory containing the library, or None for system search."""
    p = Path(lib_path)
    if p.is_absolute() and p.parent.is_dir():
        return str(p.parent)
    return None


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

    # Pre-load companion .so files from the same directory so the
    # dynamic linker can resolve them without LD_LIBRARY_PATH.
    lib_dir = _lib_dir(lib_path)
    if lib_dir:
        for dep in ("libcrypto.so.1.1", "libcrypto.so", "libShareLib.so", "libNatClientSDK.so.1", "libNatClientSDK.so"):
            dep_path = Path(lib_dir) / dep
            if dep_path.exists():
                try:
                    ct.CDLL(str(dep_path))
                except OSError:
                    pass

    try:
        return ct.CDLL(lib_path)
    except OSError as exc:
        raise NetSdkUnavailable(
            f"Cannot load TVT NetSDK from {lib_path!r}: {exc}. "
            "Ensure libdvrnetsdk.so and its dependencies "
            "(libShareLib.so, libNatClientSDK.so, libcrypto.so.1.1) "
            "are available."
        ) from exc


def is_netsdk_available() -> bool:
    """Check if the native SDK can be loaded on this platform.

    Returns True if we're on Linux with the right arch and the library
    file can be found. Does NOT actually load the library.
    """
    if platform.system() != "Linux":
        return False
    if platform.machine() not in ("x86_64", "aarch64"):
        return False
    lib_path = _find_lib()
    # If it resolved to a bare filename, check if it exists anywhere
    if not Path(lib_path).is_absolute():
        return False
    return Path(lib_path).exists()
