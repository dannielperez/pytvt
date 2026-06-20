"""Locate and load a vendor-supplied SDK shared library.

Supports:
- Linux: libdvrnetsdk.so (x86_64, aarch64)
- macOS: libNetClientSDK.dylib (via Rosetta x86_64 if needed)

Search order:
1. Explicit ``sdk_path`` argument passed to :func:`load_sdk`
2. ``$TVT_SDK_PATH`` environment variable
3. Legacy ``$PYTVT_NETSDK_LIB`` environment variable
4. On macOS: auto-detect ~/git/tvt-sdk/sdk/macos/*/binaries/
5. System library search path

No SDK binary is bundled with the public package. Users must obtain the
library separately from the vendor and point pytvt at that installation.
"""

from __future__ import annotations

import ctypes as ct
import os
import platform
import struct
import subprocess
from ctypes.util import find_library
from pathlib import Path

SDK_PATH_ENV_VAR = "TVT_SDK_PATH"
LEGACY_SDK_PATH_ENV_VAR = "PYTVT_NETSDK_LIB"
LIB_NAME = "libdvrnetsdk.so"
MAC_LIB_NAME = "libNetClientSDK.dylib"
_OPTIONAL_DEPENDENCY_GROUPS: tuple[tuple[str, ...], ...] = (
    ("libcrypto.so.1.1", "libcrypto.so"),
    ("libShareLib.so",),
)
_NAT_DEPENDENCY_GROUP: tuple[str, ...] = ("libNatClientSDK.so.1", "libNatClientSDK.so")
_MAC_COMPANION_LIBS: tuple[str, ...] = (
    "libNetCommon.dylib",
    "libShareLib.dylib",
    "libNatClientSDK.dylib",
    "libUserManager.dylib",
    "libScheduleSDK.dylib",
    "libTriggerManager.dylib",
    "libEMapSDK.dylib",
    "libNetSocket.dylib",
    "libMemPool.dylib",
    "libNodeManager.dylib",
    "libCommonFileSDK.dylib",
    "liblibeay32.dylib",
    "libssleay32.dylib",
)


class NetSdkUnavailable(ImportError):
    """Raised when the native SDK library cannot be loaded."""


def _platform_name() -> str:
    """Return standardized platform name: 'linux' or 'darwin'."""
    sys = platform.system()
    return "darwin" if sys == "Darwin" else sys.lower()


def _arch_dir() -> str:
    """Return the platform-specific lib directory name."""
    arch = platform.machine()
    if arch == "aarch64":
        return "linux-arm64"
    return "linux"


def _is_darwin() -> bool:
    """Check if running on macOS."""
    return _platform_name() == "darwin"


def _autodetect_macos_sdk_path() -> str | None:
    """Best-effort lookup for ~/git/tvt-sdk/sdk/macos/*/binaries/libNetClientSDK.dylib."""
    env_sdk = os.environ.get(SDK_PATH_ENV_VAR) or os.environ.get(LEGACY_SDK_PATH_ENV_VAR)
    if env_sdk:
        env_path = Path(env_sdk).expanduser()
        if env_path.is_file() and env_path.name == MAC_LIB_NAME:
            return str(env_path.parent)
        if env_path.is_dir() and (env_path / MAC_LIB_NAME).exists():
            return str(env_path)
        if env_path.is_dir() and (env_path / "binaries" / MAC_LIB_NAME).exists():
            return str(env_path / "binaries")

    home = Path.home()
    sdk_root = home / "git" / "tvt-sdk" / "sdk" / "macos"

    if not sdk_root.exists():
        return None

    try:
        candidates: list[Path] = sorted(
            (sdk_root / entry.name / "binaries" for entry in sdk_root.iterdir() if entry.is_dir()), reverse=True
        )

        for candidate in candidates:
            lib = candidate / MAC_LIB_NAME
            if lib.exists():
                return str(candidate)
    except (OSError, StopIteration):
        pass

    # Optional fallback: local app bundle that may carry companion frameworks.
    iptool_frameworks = Path("/Applications/IPTool.app/Contents/Frameworks")
    if (iptool_frameworks / MAC_LIB_NAME).exists():
        return str(iptool_frameworks)

    return None


def _requested_sdk_path(sdk_path: str | os.PathLike[str] | None = None) -> str | None:
    if sdk_path is not None:
        return os.fspath(sdk_path)
    return os.environ.get(SDK_PATH_ENV_VAR) or os.environ.get(LEGACY_SDK_PATH_ENV_VAR)


def _sdk_root_candidates(root: Path) -> list[Path]:
    """Return candidate library paths for the given SDK root directory."""
    if _is_darwin():
        return [
            root / MAC_LIB_NAME,
            root / "binaries" / MAC_LIB_NAME,
        ]

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
    """Resolve an explicit SDK path to a library file."""
    candidate = Path(raw_path).expanduser()

    if candidate.exists() and candidate.is_dir():
        for path in _sdk_root_candidates(candidate):
            if path.exists():
                return str(path)

        lib_name = MAC_LIB_NAME if _is_darwin() else LIB_NAME
        raise NetSdkUnavailable(
            f"No {lib_name} found under {candidate}. Set {SDK_PATH_ENV_VAR} to the library file "
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
    """Find SDK library via system search or platform-specific defaults."""
    if _is_darwin():
        return MAC_LIB_NAME
    return find_library("dvrnetsdk") or find_library("libdvrnetsdk") or LIB_NAME


def _find_lib(sdk_path: str | os.PathLike[str] | None = None) -> str:
    """Resolve the path or soname for the SDK library.

    On macOS, also checks for auto-detected ~/git/tvt-sdk/sdk/macos/*/binaries/ path.
    """
    requested = _requested_sdk_path(sdk_path)

    if requested:
        return _resolve_explicit_lib_path(requested)

    # On macOS, try auto-detect before system search
    if _is_darwin():
        auto_path = _autodetect_macos_sdk_path()
        if auto_path:
            lib = Path(auto_path) / MAC_LIB_NAME
            if lib.exists():
                return str(lib)

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
    """Pre-load optional and required companion libraries for the SDK."""
    if _is_darwin():
        _preload_macos_companions(lib_dir)
    else:
        _preload_linux_companions(lib_dir, require_nat=require_nat)


def _preload_linux_companions(lib_dir: str | None, *, require_nat: bool = False) -> None:
    """Pre-load Linux SDK companion libraries."""
    for names in _OPTIONAL_DEPENDENCY_GROUPS:
        _load_first_available(names, lib_dir)

    nat_loaded, nat_error = _load_first_available(_NAT_DEPENDENCY_GROUP, lib_dir)
    if require_nat and not nat_loaded:
        detail = f" Details: {nat_error}" if nat_error else ""
        raise NetSdkUnavailable(
            "TVT AutoNAT requires libNatClientSDK.so from the vendor SDK installation. "
            f"Set {SDK_PATH_ENV_VAR} to the SDK root or library directory.{detail}"
        )


def _preload_macos_companions(lib_dir: str | None) -> None:
    """Pre-load macOS MonitorClient SDK companion libraries.

    Searches in:
    1. The SDK lib_dir
    2. /Applications/MonitorClient.app/Contents/Frameworks
    """
    search_dirs: list[str | None] = [lib_dir]

    frameworks_dir = "/Applications/MonitorClient.app/Contents/Frameworks"
    if Path(frameworks_dir).exists():
        search_dirs.append(frameworks_dir)

    # Also try environment variable override
    env_frameworks = os.environ.get("TVT_MAC_FRAMEWORKS_PATH")
    if env_frameworks:
        search_dirs.append(env_frameworks)

    iptool_frameworks = "/Applications/IPTool.app/Contents/Frameworks"
    if Path(iptool_frameworks).exists():
        search_dirs.append(iptool_frameworks)

    for lib_name in _MAC_COMPANION_LIBS:
        for search_dir in search_dirs:
            if search_dir:
                try:
                    lib_path = str(Path(search_dir) / lib_name)
                    ct.CDLL(lib_path)
                    break  # Successfully loaded, move to next lib
                except OSError:
                    pass  # Try next search_dir


def _darwin_binary_arches(path: Path) -> set[str]:
    """Return architecture names reported by `lipo -archs` for *path*."""
    try:
        proc = subprocess.run(
            ["lipo", "-archs", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return set()

    if proc.returncode != 0:
        return set()
    return {token.strip() for token in proc.stdout.split() if token.strip()}


def sdk_binary_arches(path: str | os.PathLike[str]) -> set[str]:
    """Return detectable architecture names for a vendor SDK binary."""
    p = Path(path).expanduser()
    if not p.exists():
        return set()
    if p.suffix == ".dylib":
        return _darwin_binary_arches(p)
    try:
        proc = subprocess.run(
            ["file", str(p)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return set()
    output = proc.stdout.lower()
    if "x86-64" in output or "x86_64" in output:
        return {"x86_64"}
    if "aarch64" in output or "arm64" in output:
        return {"aarch64"}
    return set()


def python_architecture() -> str:
    """Return the Python process architecture width and machine."""
    return f"{platform.machine()}-{struct.calcsize('P') * 8}"


def resolve_sdk_library_path(sdk_path: str | os.PathLike[str] | None = None) -> str:
    """Resolve the SDK library path without loading it."""
    return _find_lib(sdk_path)


def _validate_macos_library_arch(lib_path: str) -> None:
    """Prevent process-kill crashes from loading x86_64-only SDK into arm64 Python."""
    runtime_arch = platform.machine()
    if runtime_arch != "arm64":
        return

    path = Path(lib_path).expanduser()
    if not path.exists() or path.suffix != ".dylib":
        return

    archs = _darwin_binary_arches(path)
    if not archs:
        return

    if "arm64" not in archs and "x86_64" in archs:
        raise NetSdkUnavailable(
            "Detected x86_64-only macOS TVT SDK on Apple Silicon. "
            "Run under Rosetta (for example: `arch -x86_64 /usr/bin/python3 ...`) "
            "or provide a universal/arm64 SDK build."
        )


def load_sdk(
    sdk_path: str | os.PathLike[str] | None = None,
    *,
    require_nat: bool = False,
) -> ct.CDLL:
    """Load and return the SDK shared library handle.

    Supports:
    - Linux: libdvrnetsdk.so (x86_64, aarch64)
    - macOS: libNetClientSDK.dylib (x86_64 or via Rosetta on arm64)

    Args:
        sdk_path: Explicit path to SDK library or root directory.
        require_nat: If True, ensure libNatClientSDK is available (Linux only).

    Returns:
        ctypes CDLL handle for the loaded library.

    Raises:
        NetSdkUnavailable: On unsupported platform or when the library is missing.
    """
    if _is_darwin():
        return _load_sdk_macos(sdk_path)
    else:
        return _load_sdk_linux(sdk_path, require_nat=require_nat)


def _load_sdk_linux(
    sdk_path: str | os.PathLike[str] | None = None,
    *,
    require_nat: bool = False,
) -> ct.CDLL:
    """Load SDK on Linux."""
    sys_name = platform.system()
    if sys_name != "Linux":
        raise NetSdkUnavailable(
            f"TVT NetSDK on Linux requires Linux (current: {sys_name}). "
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


def _load_sdk_macos(sdk_path: str | os.PathLike[str] | None = None) -> ct.CDLL:
    """Load SDK on macOS (libNetClientSDK.dylib)."""
    sys_name = platform.system()
    if sys_name != "Darwin":
        raise NetSdkUnavailable(
            f"TVT NetSDK on macOS requires macOS (current: {sys_name}). "
            f"Install the vendor SDK separately and set {SDK_PATH_ENV_VAR} to its path."
        )

    lib_path = _find_lib(sdk_path)
    _validate_macos_library_arch(lib_path)

    lib_dir = _lib_dir(lib_path)
    _preload_companion_libraries(lib_dir)

    try:
        return ct.CDLL(lib_path)
    except OSError as exc:
        raise NetSdkUnavailable(
            f"Cannot load TVT NetSDK from {lib_path!r}: {exc}. "
            f"Install the vendor SDK separately and set {SDK_PATH_ENV_VAR} to the library file or SDK root. "
            "On macOS, you can also use auto-detection of ~/git/tvt-sdk/sdk/macos/*/binaries/. "
            "Required companion libraries are typically installed with MonitorClient.app."
        ) from exc


def ensure_nat_support(sdk_path: str | os.PathLike[str] | None = None) -> None:
    """Validate that the NAT companion library can be loaded (Linux only).

    Raises:
        NetSdkUnavailable: If NAT support is unavailable or not on Linux.
    """
    if not platform.system() == "Linux":
        raise NetSdkUnavailable(
            f"TVT AutoNAT requires Linux (current: {platform.system()}). "
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
    """Check if the native SDK can be loaded on this platform.

    Supports both Linux (libdvrnetsdk.so) and macOS (libNetClientSDK.dylib).

    Args:
        sdk_path: Explicit path to SDK library or root directory.
        require_nat: If True, also check NAT support (Linux only).

    Returns:
        True if SDK is available and loadable, False otherwise.
    """
    sys_name = platform.system()

    if sys_name == "Linux":
        return _is_netsdk_available_linux(sdk_path, require_nat=require_nat)
    elif sys_name == "Darwin":
        return _is_netsdk_available_macos(sdk_path)

    return False


def _is_netsdk_available_linux(
    sdk_path: str | os.PathLike[str] | None = None,
    *,
    require_nat: bool = False,
) -> bool:
    """Check if the native SDK is available on Linux."""
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


def _is_netsdk_available_macos(sdk_path: str | os.PathLike[str] | None = None) -> bool:
    """Check if the native SDK is available on macOS."""
    try:
        lib_path = _find_lib(sdk_path)
    except NetSdkUnavailable:
        return False

    try:
        _validate_macos_library_arch(lib_path)
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
