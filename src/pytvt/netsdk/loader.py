"""Locate and load a vendor-supplied ``libdvrnetsdk.so`` shared library.

Search order:
1. Explicit ``sdk_path`` argument passed to :func:`load_sdk`
2. ``$TVT_SDK_PATH`` environment variable
3. Legacy ``$PYTVT_NETSDK_LIB`` environment variable
4. System library search path

No SDK binary is bundled with the public package. Users must obtain the
library separately from the vendor and point pytvt at that installation.
"""

from __future__ import annotations

import ctypes as ct
import os
import platform
from ctypes.util import find_library
from pathlib import Path

SDK_PATH_ENV_VAR = "TVT_SDK_PATH"
LEGACY_SDK_PATH_ENV_VAR = "PYTVT_NETSDK_LIB"
LIB_NAME = "libdvrnetsdk.so"
_OPTIONAL_DEPENDENCY_GROUPS: tuple[tuple[str, ...], ...] = (
    ("libcrypto.so.1.1", "libcrypto.so"),
    ("libShareLib.so",),
)
_NAT_DEPENDENCY_GROUP: tuple[str, ...] = ("libNatClientSDK.so.1", "libNatClientSDK.so")


class NetSdkUnavailable(ImportError):
    """Raised when the native SDK library cannot be loaded."""


def _arch_dir() -> str:
    """Return the platform-specific lib directory name."""
    arch = platform.machine()
    if arch == "aarch64":
        return "linux-arm64"
    return "linux"


def _requested_sdk_path(sdk_path: str | os.PathLike[str] | None = None) -> str | None:
    if sdk_path is not None:
        return os.fspath(sdk_path)
    return os.environ.get(SDK_PATH_ENV_VAR) or os.environ.get(LEGACY_SDK_PATH_ENV_VAR)


def _sdk_root_candidates(root: Path) -> list[Path]:
    arch_dir = _arch_dir()
    return [
        root / LIB_NAME,
        root / "lib" / LIB_NAME,
        root / arch_dir / LIB_NAME,
        root / "bin" / LIB_NAME,
        root / "bin" / arch_dir / LIB_NAME,
        root / "tvt" / "bin" / arch_dir / LIB_NAME,
    ]


def _resolve_explicit_lib_path(raw_path: str) -> str:
    candidate = Path(raw_path).expanduser()

    if candidate.exists() and candidate.is_dir():
        for path in _sdk_root_candidates(candidate):
            if path.exists():
                return str(path)
        raise NetSdkUnavailable(
            f"No {LIB_NAME} found under {candidate}. Set {SDK_PATH_ENV_VAR} to the library file "
            "or to the vendor SDK root directory."
        )

    if candidate.exists():
        return str(candidate)

    if any(sep in raw_path for sep in (os.sep, "/", "\\")) or raw_path.startswith("."):
        raise NetSdkUnavailable(
            f"Configured SDK path {raw_path!r} does not exist. Set {SDK_PATH_ENV_VAR} to the library file "
            "or to the vendor SDK root directory."
        )

    return raw_path


def _find_system_lib() -> str:
    return find_library("dvrnetsdk") or find_library("libdvrnetsdk") or LIB_NAME


def _find_lib(sdk_path: str | os.PathLike[str] | None = None) -> str:
    """Resolve the path or soname for ``libdvrnetsdk.so``."""
    requested = _requested_sdk_path(sdk_path)
    if requested:
        return _resolve_explicit_lib_path(requested)
    return _find_system_lib()


def _lib_dir(lib_path: str) -> str | None:
    """Return the directory containing the library, or None for system search."""
    p = Path(lib_path).expanduser()
    if p.name != lib_path and p.parent.is_dir():
        return str(p.parent.resolve())
    return None


def _candidate_targets(names: tuple[str, ...], lib_dir: str | None) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()

    if lib_dir:
        for name in names:
            target = str(Path(lib_dir) / name)
            if target not in seen:
                seen.add(target)
                targets.append(target)

    for name in names:
        if name not in seen:
            seen.add(name)
            targets.append(name)

    return targets


def _load_first_available(names: tuple[str, ...], lib_dir: str | None) -> tuple[bool, str | None]:
    errors: list[str] = []
    for target in _candidate_targets(names, lib_dir):
        try:
            ct.CDLL(target)
            return True, None
        except OSError as exc:
            errors.append(f"{target}: {exc}")
    if not errors:
        return False, None
    return False, "; ".join(errors)


def _preload_companion_libraries(lib_dir: str | None, *, require_nat: bool = False) -> None:
    for names in _OPTIONAL_DEPENDENCY_GROUPS:
        _load_first_available(names, lib_dir)

    nat_loaded, nat_error = _load_first_available(_NAT_DEPENDENCY_GROUP, lib_dir)
    if require_nat and not nat_loaded:
        detail = f" Details: {nat_error}" if nat_error else ""
        raise NetSdkUnavailable(
            "TVT AutoNAT requires libNatClientSDK.so from the vendor SDK installation. "
            f"Set {SDK_PATH_ENV_VAR} to the SDK root or library directory.{detail}"
        )


def load_sdk(
    sdk_path: str | os.PathLike[str] | None = None,
    *,
    require_nat: bool = False,
) -> ct.CDLL:
    """Load and return the SDK shared library handle.

    Returns:
        ctypes CDLL handle for libdvrnetsdk.so.

    Raises:
        NetSdkUnavailable: On non-Linux or when the library is missing.
    """
    if platform.system() != "Linux":
        raise NetSdkUnavailable(
            f"TVT NetSDK requires Linux (current: {platform.system()}). "
            f"Install the vendor SDK separately and set {SDK_PATH_ENV_VAR} to its path."
        )

    arch = platform.machine()
    if arch not in ("x86_64", "aarch64"):
        raise NetSdkUnavailable(f"TVT NetSDK supports x86_64 and aarch64 (current: {arch}).")

    lib_path = _find_lib(sdk_path)

    lib_dir = _lib_dir(lib_path)
    _preload_companion_libraries(lib_dir, require_nat=require_nat)

    try:
        return ct.CDLL(lib_path)
    except OSError as exc:
        raise NetSdkUnavailable(
            f"Cannot load TVT NetSDK from {lib_path!r}: {exc}. "
            f"Install the vendor SDK separately and set {SDK_PATH_ENV_VAR} to the library file or SDK root. "
            "Required companion libraries typically include libShareLib.so, libNatClientSDK.so, and libcrypto.so.1.1."
        ) from exc


def ensure_nat_support(sdk_path: str | os.PathLike[str] | None = None) -> None:
    """Validate that the NAT companion library can be loaded."""
    if platform.system() != "Linux":
        raise NetSdkUnavailable(
            f"TVT NetSDK requires Linux (current: {platform.system()}). "
            f"Install the vendor SDK separately and set {SDK_PATH_ENV_VAR} to its path."
        )

    arch = platform.machine()
    if arch not in ("x86_64", "aarch64"):
        raise NetSdkUnavailable(f"TVT NetSDK supports x86_64 and aarch64 (current: {arch}).")

    lib_path = _find_lib(sdk_path)
    _preload_companion_libraries(_lib_dir(lib_path), require_nat=True)


def is_netsdk_available(
    sdk_path: str | os.PathLike[str] | None = None,
    *,
    require_nat: bool = False,
) -> bool:
    """Check if the native SDK can be loaded on this platform."""
    if platform.system() != "Linux":
        return False
    if platform.machine() not in ("x86_64", "aarch64"):
        return False
    try:
        lib_path = _find_lib(sdk_path)
    except NetSdkUnavailable:
        return False

    try:
        _preload_companion_libraries(_lib_dir(lib_path), require_nat=require_nat)
    except NetSdkUnavailable:
        return False

    path_obj = Path(lib_path).expanduser()
    if path_obj.exists():
        return True

    try:
        ct.CDLL(lib_path)
    except OSError:
        return False
    return True
