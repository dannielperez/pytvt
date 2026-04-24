"""SDK namespace abstraction for TVT management and device SDK families.

TVT ships multiple C SDK libraries that share conceptual operations but use
different symbol naming conventions (namespaces):

    NET_SDK_*   — NVR/camera device SDK (libdvrnetsdk.so)
                  Sync call model.  Distributed as .so/.dll.

    Plat_*      — NVMS PlatformSDK (libPlatClientSDK.so / PlatClientSDK.lib)
                  Async callback model.  Distributed as .so (Linux) / .lib (Windows).

    NET_CLIENT_*— NVMS NetClient internal layer (libNetClientSDK.dylib / .so)
                  C++ mangled exports.  Not directly callable via ctypes without
                  demangling; included for evidence/probe purposes only.

This module provides:

    SdkNamespace      — enum of known symbol namespaces
    CAPABILITY_SYMBOLS— maps each management capability to per-namespace symbol names
    ARGTYPES          — per-namespace ctypes argtypes for each capability
    RESTYPES          — per-namespace ctypes restype for each capability
    NamespacedLibrary — wraps a loaded CDLL with namespace-aware symbol resolution;
                        the primary shared helper used by both sdk.py and platform_sdk.py

Usage
-----
    lib = ctypes.CDLL("/path/to/libPlatClientSDK.so")
    ns_lib = NamespacedLibrary(lib, SdkNamespace.PLAT)

    fn = ns_lib.require_function("login")        # raises if missing
    fn = ns_lib.get_function("get_ipc_devices")  # None if not in this namespace
    ns_lib.bind_function("login")                # applies argtypes/restype in-place

    caps = ns_lib.probe_capabilities()  # dict of capability -> present bool
    name = ns_lib.symbol_name("logout") # "Plat_LogOutEx"
"""

from __future__ import annotations

import ctypes
import ctypes as ct
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .exceptions import CapabilityNotAvailable, MissingSymbolError


class SdkNamespace(Enum):
    """Known TVT SDK symbol namespaces."""

    NET_SDK = "net_sdk"
    """NVR/camera device SDK — NET_SDK_* symbols, synchronous call model."""

    PLAT = "plat"
    """NVMS PlatformSDK — Plat_* symbols, async callback model."""

    NET_CLIENT = "net_client"
    """NVMS NetClient layer — NET_CLIENT_* symbols, C++ mangled, probe-only."""


# ---------------------------------------------------------------------------
# Symbol map: capability -> {namespace: symbol_name}
# ---------------------------------------------------------------------------
# Each entry names the symbol that fulfils this capability in each namespace.
# Omitting a namespace means that capability is not available under that namespace.

CAPABILITY_SYMBOLS: dict[str, dict[SdkNamespace, str]] = {
    # Session lifecycle — shared across NET_SDK and PLAT
    "init": {
        SdkNamespace.NET_SDK: "NET_SDK_Init",
        SdkNamespace.PLAT: "Plat_InitializeEx",
    },
    "uninit": {
        SdkNamespace.NET_SDK: "NET_SDK_Cleanup",
        SdkNamespace.PLAT: "Plat_UnInitializeEx",
    },
    "login": {
        SdkNamespace.NET_SDK: "NET_SDK_Login",
        SdkNamespace.PLAT: "Plat_LoginEx",
    },
    "login_ex": {
        # NET_SDK_LoginEx is the extended login (explicit connect type + device SN)
        SdkNamespace.NET_SDK: "NET_SDK_LoginEx",
        # NET_CLIENT equivalent — C++ mangled, probe evidence only
        SdkNamespace.NET_CLIENT: "NET_CLIENT_LoginServerUnit",
    },
    "logout": {
        SdkNamespace.NET_SDK: "NET_SDK_Logout",
        SdkNamespace.PLAT: "Plat_LogOutEx",
    },

    # Plat_*-only: async message callback registration
    "set_message_callback": {
        SdkNamespace.PLAT: "Plat_SetMessageCBEx",
    },

    # NAT helpers (NET_SDK only)
    "set_nat2_addr": {
        SdkNamespace.NET_SDK: "NET_SDK_SetNat2Addr",
    },

    # Info / enumeration
    "get_device_info": {
        SdkNamespace.NET_SDK: "NET_SDK_GetDeviceInfo",
    },
    "get_ipc_devices": {
        # Synchronous IPC device inventory
        SdkNamespace.NET_SDK: "NET_SDK_GetDeviceIPCInfo",
        # NET_CLIENT counterpart — mangled, probe-only evidence
        SdkNamespace.NET_CLIENT: "NET_CLIENT_RequestAllChannelsInfo",
    },
    "get_ch_status": {
        SdkNamespace.NET_SDK: "NET_SDK_GetDeviceCHStatus",
    },

    # Alarm channel
    "setup_alarm_chan": {
        SdkNamespace.NET_SDK: "NET_SDK_SetupAlarmChan",
    },
    "close_alarm_chan": {
        SdkNamespace.NET_SDK: "NET_SDK_CloseAlarmChan",
    },
    "get_last_error": {
        SdkNamespace.NET_SDK: "NET_SDK_GetLastError",
    },
}

# Reverse map: symbol_name -> (namespace, capability)
SYMBOL_TO_CAPABILITY: dict[str, tuple[SdkNamespace, str]] = {
    sym: (ns, cap)
    for cap, ns_map in CAPABILITY_SYMBOLS.items()
    for ns, sym in ns_map.items()
}


def capabilities_for_namespace(ns: SdkNamespace) -> dict[str, str]:
    """Return {capability: symbol_name} for all capabilities available in *ns*."""
    return {
        cap: ns_map[ns]
        for cap, ns_map in CAPABILITY_SYMBOLS.items()
        if ns in ns_map
    }


# ---------------------------------------------------------------------------
# ctypes argtypes / restype tables
# ---------------------------------------------------------------------------
# Import netsdk types lazily so this module stays importable even when pytvt.netsdk
# is not yet installed; the bindings are only applied when the library is loaded.

def _netsdk_types() -> Any:
    from pytvt.netsdk import types as _t  # noqa: PLC0415
    return _t


def _build_argtypes() -> dict[str, dict[SdkNamespace, tuple[Any, ...]]]:
    """Return argtypes per (capability, namespace) — evaluated lazily at import time."""
    t = _netsdk_types()
    return {
        "init": {
            SdkNamespace.NET_SDK: (),
            SdkNamespace.PLAT: (ct.c_char_p, ct.c_int),
        },
        "uninit": {
            SdkNamespace.NET_SDK: (),
            SdkNamespace.PLAT: (),
        },
        "login": {
            SdkNamespace.NET_SDK: (
                ct.c_char_p,
                ct.c_ushort,
                ct.c_char_p,
                ct.c_char_p,
                ct.POINTER(t.NET_SDK_DEVICEINFO),
            ),
            SdkNamespace.PLAT: (
                ct.c_char_p,
                ct.c_ushort,
                ct.c_char_p,
                ct.c_char_p,
            ),
        },
        "login_ex": {
            SdkNamespace.NET_SDK: (
                ct.c_char_p,
                ct.c_ushort,
                ct.c_char_p,
                ct.c_char_p,
                ct.POINTER(t.NET_SDK_DEVICEINFO),
                ct.c_int,
                ct.c_char_p,
            ),
        },
        "logout": {
            SdkNamespace.NET_SDK: (ct.c_long,),
            SdkNamespace.PLAT: (ct.c_int,),
        },
        "set_message_callback": {
            # The callback type is defined in platform_sdk.py; we use c_void_p here
            # and let callers override argtypes after acquiring the function.
            SdkNamespace.PLAT: (ct.c_void_p, ct.c_void_p),
        },
        "set_nat2_addr": {
            SdkNamespace.NET_SDK: (ct.c_char_p, ct.c_ushort),
        },
        "get_device_info": {
            SdkNamespace.NET_SDK: (ct.c_long, ct.POINTER(t.NET_SDK_DEVICEINFO)),
        },
        "get_ipc_devices": {
            SdkNamespace.NET_SDK: (
                ct.c_long,
                ct.POINTER(t.NET_SDK_IPC_DEVICE_INFO),
                ct.c_long,
                ct.POINTER(ct.c_long),
            ),
        },
        "get_ch_status": {
            SdkNamespace.NET_SDK: (
                ct.c_long,
                ct.POINTER(t.NET_SDK_CH_DEVICE_STATUS),
                ct.c_long,
                ct.POINTER(ct.c_long),
            ),
        },
        "setup_alarm_chan": {
            SdkNamespace.NET_SDK: (ct.c_long,),
        },
        "close_alarm_chan": {
            SdkNamespace.NET_SDK: (ct.c_long,),
        },
        "get_last_error": {
            SdkNamespace.NET_SDK: (),
        },
    }


def _build_restypes() -> dict[str, dict[SdkNamespace, Any]]:
    return {
        "init": {
            SdkNamespace.NET_SDK: ct.c_bool,
            SdkNamespace.PLAT: ct.c_bool,
        },
        "uninit": {
            SdkNamespace.NET_SDK: ct.c_bool,
            SdkNamespace.PLAT: ct.c_bool,
        },
        "login": {
            SdkNamespace.NET_SDK: ct.c_long,
            SdkNamespace.PLAT: ct.c_int,
        },
        "login_ex": {
            SdkNamespace.NET_SDK: ct.c_long,
        },
        "logout": {
            SdkNamespace.NET_SDK: ct.c_bool,
            SdkNamespace.PLAT: ct.c_bool,
        },
        "set_message_callback": {
            SdkNamespace.PLAT: ct.c_bool,
        },
        "set_nat2_addr": {
            SdkNamespace.NET_SDK: ct.c_bool,
        },
        "get_device_info": {
            SdkNamespace.NET_SDK: ct.c_bool,
        },
        "get_ipc_devices": {
            SdkNamespace.NET_SDK: ct.c_bool,
        },
        "get_ch_status": {
            SdkNamespace.NET_SDK: ct.c_bool,
        },
        "setup_alarm_chan": {
            SdkNamespace.NET_SDK: ct.c_long,
        },
        "close_alarm_chan": {
            SdkNamespace.NET_SDK: ct.c_bool,
        },
        "get_last_error": {
            SdkNamespace.NET_SDK: ct.c_uint,
        },
    }


# ---------------------------------------------------------------------------
# NamespacedLibrary
# ---------------------------------------------------------------------------

class NamespacedLibrary:
    """Wraps a loaded ctypes.CDLL with namespace-aware symbol resolution and binding.

    Both sdk.py (NET_SDK namespace) and platform_sdk.py (PLAT namespace) use this
    class for:
      - symbol name lookup via the shared CAPABILITY_SYMBOLS table
      - argtypes / restype application
      - capability probing (which operations are present in this library)
      - consistent MissingSymbolError / CapabilityNotAvailable error semantics

    The login *flow* (sync vs async callback) differs per namespace and is therefore
    implemented in each backend class, not here.
    """

    def __init__(self, lib: ctypes.CDLL, namespace: SdkNamespace) -> None:
        self._lib = lib
        self.namespace = namespace
        self._argtypes: dict[str, dict[SdkNamespace, tuple[Any, ...]]] | None = None
        self._restypes: dict[str, dict[SdkNamespace, Any]] | None = None

    # ------------------------------------------------------------------
    # Lazy type tables (avoids import-time ctypes Structure instantiation)
    # ------------------------------------------------------------------

    def _get_argtypes(self) -> dict[str, dict[SdkNamespace, tuple[Any, ...]]]:
        if self._argtypes is None:
            self._argtypes = _build_argtypes()
        return self._argtypes

    def _get_restypes(self) -> dict[str, dict[SdkNamespace, Any]]:
        if self._restypes is None:
            self._restypes = _build_restypes()
        return self._restypes

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def symbol_name(self, capability: str) -> str | None:
        """Return the symbol name for *capability* in this namespace, or None."""
        return CAPABILITY_SYMBOLS.get(capability, {}).get(self.namespace)

    def get_function(self, capability: str) -> Any | None:
        """Return the ctypes function for *capability*, or None if unavailable."""
        name = self.symbol_name(capability)
        if name is None:
            return None
        return getattr(self._lib, name, None)

    def require_function(self, capability: str) -> Any:
        """Return the ctypes function for *capability* or raise a typed error.

        Raises CapabilityNotAvailable when the capability is not mapped to this
        namespace.  Raises MissingSymbolError when the symbol is mapped but absent
        from the loaded library.
        """
        name = self.symbol_name(capability)
        if name is None:
            raise CapabilityNotAvailable(
                f"Capability {capability!r} is not available in the "
                f"{self.namespace.value!r} SDK namespace."
            )
        fn = getattr(self._lib, name, None)
        if fn is None:
            raise MissingSymbolError(
                f"Symbol {name!r} (capability={capability!r}) not found in "
                f"{self.namespace.value!r} library."
            )
        return fn

    def bind_function(self, capability: str, *, argtypes: tuple[Any, ...] | None = None,
                      restype: Any = None) -> Any:
        """Resolve, type-annotate, and return the ctypes function for *capability*.

        If *argtypes* / *restype* are not supplied the shared tables are used.
        Callers may supply overrides for capabilities with complex types (e.g. the
        PLAT set_message_callback which needs the full CFUNCTYPE prototype).
        """
        fn = self.require_function(capability)

        if argtypes is None:
            at = self._get_argtypes().get(capability, {}).get(self.namespace)
            argtypes = at if at is not None else ()
        if restype is None:
            rt = self._get_restypes().get(capability, {}).get(self.namespace)
            restype = rt if rt is not None else ct.c_int

        fn.argtypes = list(argtypes)
        fn.restype = restype
        return fn

    def has_capability(self, capability: str) -> bool:
        """Return True if *capability* is mapped and present in the loaded library."""
        name = self.symbol_name(capability)
        if name is None:
            return False
        return getattr(self._lib, name, None) is not None

    def probe_capabilities(self) -> dict[str, bool]:
        """Return present/absent status for every capability in CAPABILITY_SYMBOLS."""
        return {cap: self.has_capability(cap) for cap in CAPABILITY_SYMBOLS}

    def probe_namespace_capabilities(self) -> dict[str, bool]:
        """Return present/absent status for capabilities mapped to this namespace only."""
        ns_caps = capabilities_for_namespace(self.namespace)
        return {
            cap: getattr(self._lib, sym, None) is not None
            for cap, sym in ns_caps.items()
        }

    def login_path_ready(self) -> bool:
        """Return True if the minimum init/login/logout triple is callable."""
        return all(self.has_capability(cap) for cap in ("init", "login", "logout"))

    def enumerate_exported_symbols(self) -> set[str]:
        """Return exported symbol names from the underlying library using nm."""
        # Retrieve the file path from the library object
        lib_path: str | None = None
        for attr in ("_name", "name"):
            val = getattr(self._lib, attr, None)
            if isinstance(val, str) and val:
                lib_path = val
                break
        if not lib_path:
            return set()
        nm = shutil.which("nm")
        if not nm:
            return set()
        try:
            result = subprocess.run(
                [nm, "-D", "--defined-only", lib_path],
                capture_output=True, text=True, check=False,
            )
        except OSError:
            return set()
        if result.returncode != 0:
            return set()
        symbols: set[str] = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts:
                symbols.add(parts[-1])
        return symbols

    def as_dict(self) -> dict[str, Any]:
        """Return a diagnostics dict describing this namespace binding."""
        lib_path: str | None = None
        for attr in ("_name", "name"):
            val = getattr(self._lib, attr, None)
            if isinstance(val, str) and val:
                lib_path = val
                break
        return {
            "namespace": self.namespace.value,
            "lib_path": lib_path,
            "capabilities": self.probe_namespace_capabilities(),
            "login_path_ready": self.login_path_ready(),
        }

    # ------------------------------------------------------------------
    # Convenience wrappers for the session lifecycle shared by all namespaces
    # ------------------------------------------------------------------

    def call_init(self, *extra_args: Any) -> bool:
        """Call the init symbol for this namespace.

        NET_SDK: NET_SDK_Init()
        PLAT:    Plat_InitializeEx(None, 0)
        """
        fn = self.bind_function("init")
        if self.namespace == SdkNamespace.PLAT:
            return bool(fn(None, 0))
        return bool(fn())

    def call_uninit(self) -> bool:
        """Call the uninit symbol if present; return True on success or if absent."""
        if not self.has_capability("uninit"):
            return True
        fn = self.bind_function("uninit")
        return bool(fn())

    def call_logout(self, handle: int) -> bool:
        """Call the logout symbol for *handle*.

        NET_SDK: NET_SDK_Logout(handle)     handle is c_long
        PLAT:    Plat_LogOutEx(handle)      handle is c_int (loginID)
        """
        fn = self.bind_function("logout")
        return bool(fn(handle))

    def call_get_last_error(self) -> int | None:
        """Return the last SDK error code, or None if the symbol is absent."""
        if not self.has_capability("get_last_error"):
            return None
        fn = self.bind_function("get_last_error")
        try:
            return int(fn())
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def detect_namespace(exported_symbols: set[str]) -> SdkNamespace | None:
    """Infer the SDK namespace from the set of exported symbols."""
    plat_count = sum(1 for s in exported_symbols if s.startswith("Plat_"))
    net_sdk_count = sum(1 for s in exported_symbols if s.startswith("NET_SDK_"))
    net_client_count = sum(1 for s in exported_symbols if "NET_CLIENT_" in s)

    if plat_count > net_sdk_count and plat_count > net_client_count:
        return SdkNamespace.PLAT
    if net_sdk_count >= plat_count and net_sdk_count > 0:
        return SdkNamespace.NET_SDK
    if net_client_count > 0:
        return SdkNamespace.NET_CLIENT
    return None


def make_namespaced_library(
    lib: ctypes.CDLL,
    namespace: SdkNamespace | None = None,
    *,
    exported_symbols: set[str] | None = None,
) -> NamespacedLibrary:
    """Create a NamespacedLibrary, auto-detecting namespace when not supplied."""
    if namespace is None:
        if exported_symbols is None:
            # Build a probe set from the library object itself using known symbol names
            all_symbols: set[str] = set()
            for ns_map in CAPABILITY_SYMBOLS.values():
                for sym in ns_map.values():
                    if getattr(lib, sym, None) is not None:
                        all_symbols.add(sym)
            exported_symbols = all_symbols
        namespace = detect_namespace(exported_symbols) or SdkNamespace.NET_SDK
    return NamespacedLibrary(lib, namespace)
