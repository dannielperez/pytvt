"""Native SDK adapter for LAN device IP modification.

Evidence used for the callable surfaces:
- Linux Device Net SDK header ``DVR_NET_SDK.h`` documents
  ``BOOL NET_SDK_ModifyDeviceNetInfo(NET_SDK_DEVICE_IP_INFO*)``.
- The Linux 2.1.0 SDK also exports ``NET_SDK_SetDeviceIP`` on some builds.
- The macOS MonitorClient SDK exports the C++-mangled synchronous overload
  ``NET_CLIENT_RequestModifyDeviceIp(unsigned int, char const*, unsigned int, CBufferData*&)``.

No SDK binaries or headers are bundled by pytvt.
"""

from __future__ import annotations

import contextlib
import ctypes as ct
import ipaddress
import os
import platform
import re
import threading
import time
from dataclasses import dataclass
from xml.sax.saxutils import escape

from . import bindings as sdk_bindings
from .client import NetSdkCapabilityError, NetSdkClient, NetSdkError
from .loader import (
    NetSdkUnavailable,
    load_sdk,
    python_architecture,
    resolve_sdk_library_path,
    sdk_binary_arches,
)


class SdkStepTimeout(RuntimeError):
    """Raised when a native SDK call exceeds the watchdog budget."""


def _sdk_call_with_watchdog(label: str, timeout: float, fn, *args):
    """Run ``fn(*args)`` in a daemon thread, enforcing ``timeout`` seconds.

    Native TVT SDK entrypoints (notably ``NET_CLIENT_Start``) release the
    GIL and may never return, which defeats Python's ``signal.alarm``-based
    timeouts. Running in a daemon thread lets us abandon the call cleanly;
    the thread dies with the interpreter. On timeout, ``SdkStepTimeout`` is
    raised so callers surface a diagnostic error instead of hanging.
    """
    result: dict = {}

    def runner():
        try:
            result["value"] = fn(*args)
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, name=f"sdk-watchdog:{label}", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise SdkStepTimeout(f"{label} exceeded {timeout:.1f}s watchdog (thread abandoned)")
    if "error" in result:
        raise result["error"]
    return result.get("value")


# macOS exports C++ symbols with one leading underscore in dlsym/ctypes lookups.
# `nm` shows an extra underscore in output formatting, so do not mirror that here.
MACOS_REQUEST_MODIFY_DEVICE_IP = "_Z32NET_CLIENT_RequestModifyDeviceIpjPKcjRP11CBufferData"
MACOS_REQUEST_MODIFY_DEVICE_IP_OBSERVER = "_Z32NET_CLIENT_RequestModifyDeviceIpjPKcjPvP13CWaitObserver"

# MonitorClient runtime lifecycle (required before any NET_CLIENT_Request* call).
# Mangled names verified against libNetClientSDK.dylib v2.0.5:
#   __Z18NET_CLIENT_InitialjP11InterlockedPKcS2_jj  <- nm (two leading underscores)
#   __Z16NET_CLIENT_Startv
#   __Z15NET_CLIENT_Stopv
#   __Z15NET_CLIENT_Quitv
#   __Z23NET_CLIENT_GetLastErrorv
# dlsym strips one underscore; ctypes.CDLL lookup uses the single-underscore form.
MACOS_NET_CLIENT_INITIAL = "_Z18NET_CLIENT_InitialjP11InterlockedPKcS2_jj"
MACOS_NET_CLIENT_START = "_Z16NET_CLIENT_Startv"
MACOS_NET_CLIENT_STOP = "_Z15NET_CLIENT_Stopv"
MACOS_NET_CLIENT_QUIT = "_Z15NET_CLIENT_Quitv"
MACOS_NET_CLIENT_GET_LAST_ERROR = "_Z23NET_CLIENT_GetLastErrorv"


@dataclass(frozen=True, slots=True)
class DeviceIpModifyResult:
    """Result for a native LAN device IP modification attempt."""

    success: bool
    mac: str
    old_ip: str | None
    new_ip: str
    error_code: int | None
    error_message: str | None
    sdk_function_used: str | None
    elapsed_ms: int


def _human_sdk_function(name: str | None) -> str | None:
    if not name:
        return None
    if name in {MACOS_REQUEST_MODIFY_DEVICE_IP, MACOS_REQUEST_MODIFY_DEVICE_IP_OBSERVER}:
        return "NET_CLIENT_RequestModifyDeviceIp"
    return name


def normalize_mac(mac: str) -> str:
    """Normalize a MAC address to colon-separated uppercase form."""
    raw = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    if len(raw) != 12:
        raise ValueError("mac must contain 12 hexadecimal digits")
    return ":".join(raw[i : i + 2].upper() for i in range(0, 12, 2))


def _valid_ip(value: str, field: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid IP address") from exc


def _last_error(lib: object) -> int | None:
    for name in (
        "NET_SDK_GetLastError",
        "NET_CLIENT_GetLastError",
        MACOS_NET_CLIENT_GET_LAST_ERROR,
    ):
        func = getattr(lib, name, None)
        if func is not None:
            try:
                func.restype = ct.c_uint
                func.argtypes = []
                return int(func())
            except Exception:
                return None
    return None


def _symbol(lib: object, *names: str) -> tuple[str, object] | tuple[None, None]:
    for name in names:
        func = getattr(lib, name, None)
        if func is not None:
            return name, func
    return None, None


def _scan_match_by_mac_or_ip(
    *,
    mac: str,
    ip: str | None,
    sdk_path: str | None,
    timeout_ms: int,
) -> dict[str, object] | None:
    """Best-effort LAN scan match by normalized MAC or IP."""
    with NetSdkClient(sdk_path=sdk_path) as sdk:
        for device in sdk.discover(timeout_ms=timeout_ms):
            if device.mac == mac or (ip and device.ip == ip):
                return {
                    "ip": device.ip,
                    "mac": device.mac,
                    "product": device.product,
                    "device_name": device.device_name,
                }
    return None


def _load_modify_capability(sdk_path: str | None) -> tuple[object, dict[str, object], str]:
    diagnostics = sdk_ip_modify_diagnostics(sdk_path)
    try:
        lib = load_sdk(sdk_path)
    except NetSdkUnavailable:
        raise
    try:
        symbol = sdk_bindings.bind_device_ip_modify(lib)
    except RuntimeError as exc:
        raise NetSdkCapabilityError(str(exc)) from exc
    diagnostics["selected_sdk_function"] = _human_sdk_function(symbol)
    return lib, diagnostics, symbol


def _netclient_mangled_initial(lib: object) -> object | None:
    """Locate NET_CLIENT_Initial by its C++-mangled name.

    Signature (from demangled nm -gU):
        NET_CLIENT_Initial(unsigned int thread_count,
                           Interlocked* interlocked,
                           char const* work_dir,
                           char const* log_dir,
                           unsigned int log_size,
                           unsigned int log_level)
    """
    fn = getattr(lib, MACOS_NET_CLIENT_INITIAL, None) or getattr(lib, "NET_CLIENT_Initial", None)
    if fn is None:
        return None
    fn.restype = ct.c_bool
    fn.argtypes = [ct.c_uint, ct.c_void_p, ct.c_char_p, ct.c_char_p, ct.c_uint, ct.c_uint]
    return fn


def _netclient_mangled_nullary(lib: object, mangled: str, unmangled: str) -> object | None:
    """Bind a no-arg bool-returning NET_CLIENT_* lifecycle function."""
    fn = getattr(lib, mangled, None) or getattr(lib, unmangled, None)
    if fn is None:
        return None
    fn.restype = ct.c_bool
    fn.argtypes = []
    return fn


def _netclient_lifecycle_start(lib: object, watchdog: float = 10.0) -> tuple[bool, str | None]:
    """Call NET_CLIENT_Initial + NET_CLIENT_Start.

    This is the macOS MonitorClient runtime bootstrap that must run exactly
    once before any NET_CLIENT_* request call. Without it, request APIs
    like NET_CLIENT_RequestModifyDeviceIp either hang or silently no-op.

    Each sub-call is wrapped in a SIGALRM watchdog so a hung SDK call
    surfaces as a clean error instead of an indefinite stall.
    """
    initial = _netclient_mangled_initial(lib)
    start = _netclient_mangled_nullary(lib, MACOS_NET_CLIENT_START, "NET_CLIENT_Start")
    if initial is None or start is None:
        return False, "NET_CLIENT_Initial or NET_CLIENT_Start not exported"

    # Safe defaults: 8 worker threads, no Interlocked object, work/log dir unset,
    # 10 MB log size, log level 0. IPTool.app uses similar values.
    try:
        ok_init = bool(
            _sdk_call_with_watchdog(
                "NET_CLIENT_Initial",
                watchdog,
                initial,
                8,
                None,
                b"",
                b"",
                10 * 1024 * 1024,
                0,
            )
        )
    except SdkStepTimeout as exc:
        return False, str(exc)
    except OSError as exc:
        return False, f"NET_CLIENT_Initial raised {exc!r}"
    if not ok_init:
        return False, "NET_CLIENT_Initial returned false"

    try:
        ok_start = bool(
            _sdk_call_with_watchdog(
                "NET_CLIENT_Start",
                watchdog,
                start,
            )
        )
    except SdkStepTimeout as exc:
        return False, str(exc)
    except OSError as exc:
        return False, f"NET_CLIENT_Start raised {exc!r}"
    if not ok_start:
        return False, "NET_CLIENT_Start returned false"

    return True, None


def _netclient_lifecycle_stop(lib: object, watchdog: float = 5.0) -> None:
    """Best-effort NET_CLIENT_Stop + NET_CLIENT_Quit teardown."""
    for mangled, plain in (
        (MACOS_NET_CLIENT_STOP, "NET_CLIENT_Stop"),
        (MACOS_NET_CLIENT_QUIT, "NET_CLIENT_Quit"),
    ):
        fn = _netclient_mangled_nullary(lib, mangled, plain)
        if fn is None:
            continue
        with contextlib.suppress(OSError, SdkStepTimeout):
            _sdk_call_with_watchdog(plain, watchdog, fn)


def _invoke_netclient_modify(
    *,
    lib: object,
    request: bytes,
    timeout_ms: int,
) -> tuple[bool, str | None, int | None, str | None]:
    timeout_ms = max(1, timeout_ms)

    # Bootstrap the MonitorClient runtime. On macOS NET_CLIENT_RequestModifyDeviceIp
    # requires the Initial/Start pair first; without it the call silently fails or
    # hangs. On Linux NET_SDK-only builds this branch is skipped (symbols absent).
    #
    # Known issue: on macOS the vendor's NET_CLIENT_Initial/Start routines install
    # Foundation/runloop machinery that can wedge the host process indefinitely
    # under Rosetta — a Python-level watchdog cannot interrupt it because the
    # native thread continues to hold runtime locks. For that reason the macOS
    # path is gated behind the opt-in environment variable
    # ``PYTVT_ENABLE_MACOS_NETCLIENT_MODIFY=1``. By default we fail fast with a
    # clear diagnostic pointing to the supported Linux-SDK path (the
    # ``data/sdk_change_ip.py`` Docker harness), which is hang-proof.
    lifecycle_ok = False
    if getattr(lib, MACOS_NET_CLIENT_START, None) is not None or getattr(lib, "NET_CLIENT_Start", None) is not None:
        if platform.system() == "Darwin" and os.environ.get("PYTVT_ENABLE_MACOS_NETCLIENT_MODIFY", "0") != "1":
            return (
                False,
                "NET_CLIENT_RequestModifyDeviceIp",
                None,
                (
                    "macOS NET_CLIENT runtime is disabled because NET_CLIENT_Initial/Start "
                    "is known to hang under Rosetta and cannot be interrupted from Python. "
                    "Use the Linux SDK via Docker instead (see data/sdk_change_ip.py), or "
                    "set PYTVT_ENABLE_MACOS_NETCLIENT_MODIFY=1 to attempt the macOS path "
                    "at your own risk."
                ),
            )
        lifecycle_ok, lifecycle_error = _netclient_lifecycle_start(lib)
        if not lifecycle_ok:
            return (
                False,
                "NET_CLIENT_RequestModifyDeviceIp",
                None,
                (f"NET_CLIENT runtime bootstrap failed: {lifecycle_error}"),
            )

    try:
        buffer_data = getattr(lib, MACOS_REQUEST_MODIFY_DEVICE_IP, None)
        if buffer_data is not None:
            buffer_data.restype = ct.c_bool
            buffer_data.argtypes = [ct.c_uint, ct.c_char_p, ct.c_uint, ct.POINTER(ct.c_void_p)]
            response = ct.c_void_p()
            try:
                called_ok = bool(
                    _sdk_call_with_watchdog(
                        "NET_CLIENT_RequestModifyDeviceIp(CBufferData)",
                        max(2.0, timeout_ms / 1000 + 5),
                        buffer_data,
                        0,
                        request,
                        timeout_ms,
                        ct.byref(response),
                    )
                )
            except SdkStepTimeout as exc:
                return False, "NET_CLIENT_RequestModifyDeviceIp", None, str(exc)
            if called_ok:
                return True, "NET_CLIENT_RequestModifyDeviceIp", None, None
            first_error = _last_error(lib)
        else:
            first_error = None

        observer = getattr(lib, MACOS_REQUEST_MODIFY_DEVICE_IP_OBSERVER, None)
        if observer is not None:
            observer.restype = ct.c_bool
            observer.argtypes = [ct.c_uint, ct.c_char_p, ct.c_uint, ct.c_void_p, ct.c_void_p]
            try:
                called_ok = bool(
                    _sdk_call_with_watchdog(
                        "NET_CLIENT_RequestModifyDeviceIp(CWaitObserver)",
                        max(2.0, timeout_ms / 1000 + 5),
                        observer,
                        0,
                        request,
                        timeout_ms,
                        None,
                        None,
                    )
                )
            except SdkStepTimeout as exc:
                return False, "NET_CLIENT_RequestModifyDeviceIp", None, str(exc)
            if called_ok:
                return True, "NET_CLIENT_RequestModifyDeviceIp", None, None
            second_error = _last_error(lib)
            if buffer_data is not None:
                return (
                    False,
                    "NET_CLIENT_RequestModifyDeviceIp",
                    second_error,
                    (
                        "NET_CLIENT_RequestModifyDeviceIp failed via CBufferData and CWaitObserver overloads "
                        f"(first_error={first_error}, second_error={second_error})"
                    ),
                )
            return (
                False,
                "NET_CLIENT_RequestModifyDeviceIp",
                second_error,
                (f"NET_CLIENT_RequestModifyDeviceIp failed via CWaitObserver overload (error={second_error})"),
            )

        return (
            False,
            "NET_CLIENT_RequestModifyDeviceIp",
            first_error,
            (f"NET_CLIENT_RequestModifyDeviceIp failed via CBufferData overload (error={first_error})"),
        )
    finally:
        if lifecycle_ok:
            _netclient_lifecycle_stop(lib)


def scan_device_match(
    *,
    mac: str,
    ip: str | None = None,
    timeout_ms: int = 3000,
    sdk_path: str | None = None,
) -> dict[str, object]:
    """Scan for a target device by MAC and optional IP without mutating state."""
    norm_mac = normalize_mac(mac)
    norm_ip = _valid_ip(ip, "ip") if ip else None
    diagnostics = sdk_ip_modify_diagnostics(sdk_path)
    match: dict[str, object] | None = None
    scan_error: str | None = None

    if diagnostics.get("symbols", {}).get("NET_SDK_DiscoverDevice"):
        try:
            match = _scan_match_by_mac_or_ip(mac=norm_mac, ip=norm_ip, sdk_path=sdk_path, timeout_ms=timeout_ms)
        except Exception as exc:
            scan_error = str(exc)
    else:
        scan_error = "NET_SDK_DiscoverDevice unavailable"

    return {
        "scan_only": True,
        "matched": bool(match),
        "mac": norm_mac,
        "ip": norm_ip,
        "timeout_ms": int(timeout_ms),
        "diagnostics": diagnostics,
        "target_match": match,
        "scan_error": scan_error,
    }


def build_netclient_modify_ip_xml(
    *,
    mac: str | None,
    old_ip: str | None,
    new_ip: str,
    subnet_mask: str,
    gateway: str,
    username: str | None,
    password: str | None,
) -> bytes:
    """Build the NET_CLIENT request XML for the IPTool modify command."""
    parts = ["<request><content><item>"]
    if mac:
        parts.append(f"<mac>{escape(mac)}</mac>")
    parts.extend(
        [
            f"<newIP>{escape(new_ip)}</newIP>",
            f"<netmask>{escape(subnet_mask)}</netmask>",
            f"<gateway>{escape(gateway)}</gateway>",
        ]
    )
    if old_ip:
        parts.append(f"<oldIP>{escape(old_ip)}</oldIP>")
    if username:
        parts.append(f"<username>{escape(username)}</username>")
    if password:
        parts.append(f"<password>{escape(password)}</password>")
    parts.append("</item></content></request>")
    return "".join(parts).encode("utf-8")


def sdk_ip_modify_diagnostics(sdk_path: str | None = None) -> dict[str, object]:
    """Return SDK diagnostics relevant to scan and IP modification support."""
    diagnostics: dict[str, object] = {
        "platform": platform.system(),
        "machine_architecture": platform.machine(),
        "python_architecture": python_architecture(),
        "sdk_path": None,
        "sdk_architecture": [],
        "selected_sdk_function": None,
        "symbols": {
            "scan": False,
            "ip_modify": False,
            "NET_SDK_DiscoverDevice": False,
            "NET_SDK_ModifyDeviceNetInfo": False,
            "NET_SDK_SetDeviceIP": False,
            "NET_CLIENT_RequestModifyDeviceIp": False,
        },
        "load_error": None,
    }
    try:
        lib_path = resolve_sdk_library_path(sdk_path)
        diagnostics["sdk_path"] = lib_path
        diagnostics["sdk_architecture"] = sorted(sdk_binary_arches(lib_path))
        lib = load_sdk(sdk_path)
    except Exception as exc:
        diagnostics["load_error"] = str(exc)
        return diagnostics

    symbols = diagnostics["symbols"]
    assert isinstance(symbols, dict)
    symbols["NET_SDK_DiscoverDevice"] = hasattr(lib, "NET_SDK_DiscoverDevice")
    symbols["NET_SDK_ModifyDeviceNetInfo"] = hasattr(lib, "NET_SDK_ModifyDeviceNetInfo")
    symbols["NET_SDK_SetDeviceIP"] = hasattr(lib, "NET_SDK_SetDeviceIP")
    symbols["NET_CLIENT_RequestModifyDeviceIp"] = hasattr(lib, MACOS_REQUEST_MODIFY_DEVICE_IP) or hasattr(
        lib, MACOS_REQUEST_MODIFY_DEVICE_IP_OBSERVER
    )
    symbols["scan"] = bool(symbols["NET_SDK_DiscoverDevice"])
    symbols["ip_modify"] = bool(
        symbols["NET_SDK_ModifyDeviceNetInfo"]
        or symbols["NET_SDK_SetDeviceIP"]
        or symbols["NET_CLIENT_RequestModifyDeviceIp"]
    )
    if symbols["ip_modify"]:
        try:
            diagnostics["selected_sdk_function"] = _human_sdk_function(sdk_bindings.bind_device_ip_modify(lib))
        except RuntimeError:
            diagnostics["selected_sdk_function"] = None
    return diagnostics


def modify_device_ip_by_mac(
    mac: str,
    old_ip: str | None,
    new_ip: str,
    subnet_mask: str,
    gateway: str,
    username: str | None = None,
    password: str | None = None,
    timeout: float = 5.0,
    sdk_path: str | None = None,
) -> DeviceIpModifyResult:
    """Modify a LAN device IP address using the native SDK provisioning path."""
    norm_mac = normalize_mac(mac)
    norm_old_ip = _valid_ip(old_ip, "old_ip") if old_ip else None
    norm_new_ip = _valid_ip(new_ip, "new_ip")
    norm_mask = _valid_ip(subnet_mask, "subnet_mask")
    norm_gateway = _valid_ip(gateway, "gateway")
    started = time.perf_counter()

    lib, _, bound_symbol = _load_modify_capability(sdk_path)
    human_symbol = _human_sdk_function(bound_symbol)

    if hasattr(lib, "NET_SDK_Init"):
        function_used = "NET_SDK_SetDeviceIP" if hasattr(lib, "NET_SDK_SetDeviceIP") else "NET_SDK_ModifyDeviceNetInfo"
        try:
            with NetSdkClient(
                sdk_path=sdk_path, connect_timeout=int(timeout * 1000), recv_timeout=int(timeout * 1000)
            ) as sdk:
                sdk.set_device_ip_by_mac(
                    norm_mac,
                    password or "",
                    ip=norm_new_ip,
                    netmask=norm_mask,
                    gateway=norm_gateway,
                )
            return DeviceIpModifyResult(
                success=True,
                mac=norm_mac,
                old_ip=norm_old_ip,
                new_ip=norm_new_ip,
                error_code=None,
                error_message=None,
                sdk_function_used=function_used,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
        except NetSdkError as exc:
            return DeviceIpModifyResult(
                success=False,
                mac=norm_mac,
                old_ip=norm_old_ip,
                new_ip=norm_new_ip,
                error_code=int(exc.code) if getattr(exc, "code", None) is not None else None,
                error_message=str(exc),
                sdk_function_used=function_used,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )

    request = build_netclient_modify_ip_xml(
        mac=norm_mac,
        old_ip=norm_old_ip,
        new_ip=norm_new_ip,
        subnet_mask=norm_mask,
        gateway=norm_gateway,
        username=username,
        password=password,
    )
    ok, used_symbol, error_code, error_message = _invoke_netclient_modify(
        lib=lib,
        request=request,
        timeout_ms=int(timeout * 1000),
    )
    return DeviceIpModifyResult(
        success=ok,
        mac=norm_mac,
        old_ip=norm_old_ip,
        new_ip=norm_new_ip,
        error_code=error_code,
        error_message=None if ok else error_message,
        sdk_function_used=_human_sdk_function(used_symbol or human_symbol),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def modify_device_ip_by_ip(
    old_ip: str,
    new_ip: str,
    subnet_mask: str,
    gateway: str,
    username: str | None = None,
    password: str | None = None,
    timeout: float = 5.0,
    sdk_path: str | None = None,
) -> DeviceIpModifyResult:
    """Modify a LAN device IP address using old/new IP fields (no MAC required)."""
    norm_old_ip = _valid_ip(old_ip, "old_ip")
    norm_new_ip = _valid_ip(new_ip, "new_ip")
    norm_mask = _valid_ip(subnet_mask, "subnet_mask")
    norm_gateway = _valid_ip(gateway, "gateway")
    started = time.perf_counter()

    lib, _, bound_symbol = _load_modify_capability(sdk_path)
    human_symbol = _human_sdk_function(bound_symbol)

    request = build_netclient_modify_ip_xml(
        mac=None,
        old_ip=norm_old_ip,
        new_ip=norm_new_ip,
        subnet_mask=norm_mask,
        gateway=norm_gateway,
        username=username,
        password=password,
    )
    ok, used_symbol, error_code, error_message = _invoke_netclient_modify(
        lib=lib,
        request=request,
        timeout_ms=int(timeout * 1000),
    )
    return DeviceIpModifyResult(
        success=ok,
        mac="",
        old_ip=norm_old_ip,
        new_ip=norm_new_ip,
        error_code=error_code,
        error_message=None if ok else error_message,
        sdk_function_used=_human_sdk_function(used_symbol or human_symbol),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def verify_device_ip_after_modify(
    *,
    mac: str,
    new_ip: str,
    sdk_path: str | None = None,
    timeout_ms: int = 3000,
) -> dict[str, object]:
    """Verify IP change by rescanning LAN for the target MAC/new IP."""
    norm_mac = normalize_mac(mac)
    norm_new_ip = _valid_ip(new_ip, "new_ip")
    diagnostics = sdk_ip_modify_diagnostics(sdk_path)
    symbols = diagnostics.get("symbols", {})
    can_scan = bool(isinstance(symbols, dict) and symbols.get("NET_SDK_DiscoverDevice"))
    if not can_scan:
        return {
            "verified": False,
            "reason": "NET_SDK_DiscoverDevice unavailable for verification",
            "match": None,
            "diagnostics": diagnostics,
        }

    try:
        match = _scan_match_by_mac_or_ip(mac=norm_mac, ip=norm_new_ip, sdk_path=sdk_path, timeout_ms=timeout_ms)
    except Exception as exc:
        return {
            "verified": False,
            "reason": str(exc),
            "match": None,
            "diagnostics": diagnostics,
        }

    verified = bool(match and match.get("ip") == norm_new_ip and match.get("mac") == norm_mac)
    return {
        "verified": verified,
        "status": "verified" if verified else "blocked",
        "blocked": not verified,
        "reason": None if verified else "scan did not confirm device at requested new IP",
        "match": match,
        "verification_timeout_ms": int(timeout_ms),
        "mac": norm_mac,
        "new_ip": norm_new_ip,
        "diagnostics": diagnostics,
    }


def validate_modify_ip_dry_run(
    *,
    mac: str,
    new_ip: str,
    subnet_mask: str,
    gateway: str,
    old_ip: str | None = None,
    sdk_path: str | None = None,
) -> dict[str, object]:
    """Validate SDK loading, symbols, arguments, and best-effort scan matching."""
    norm_mac = normalize_mac(mac)
    norm_new_ip = _valid_ip(new_ip, "new_ip")
    norm_mask = _valid_ip(subnet_mask, "subnet_mask")
    norm_gateway = _valid_ip(gateway, "gateway")
    norm_old_ip = _valid_ip(old_ip, "old_ip") if old_ip else None
    diagnostics = sdk_ip_modify_diagnostics(sdk_path)
    match: dict[str, object] | None = None
    scan_error: str | None = None

    if diagnostics.get("symbols", {}).get("NET_SDK_DiscoverDevice"):
        try:
            match = _scan_match_by_mac_or_ip(
                mac=norm_mac,
                ip=norm_old_ip,
                sdk_path=sdk_path,
                timeout_ms=3000,
            )
        except Exception as exc:
            scan_error = str(exc)

    return {
        "dry_run": True,
        "status": "ready" if match else "blocked",
        "blocked": not bool(match),
        "valid_arguments": True,
        "mac": norm_mac,
        "old_ip": norm_old_ip,
        "new_ip": norm_new_ip,
        "subnet_mask": norm_mask,
        "gateway": norm_gateway,
        "verification_timeout_ms": 3000,
        "diagnostics": diagnostics,
        "target_match": match,
        "scan_error": scan_error,
    }


__all__ = [
    "DeviceIpModifyResult",
    "MACOS_REQUEST_MODIFY_DEVICE_IP",
    "build_netclient_modify_ip_xml",
    "modify_device_ip_by_mac",
    "modify_device_ip_by_ip",
    "normalize_mac",
    "scan_device_match",
    "sdk_ip_modify_diagnostics",
    "verify_device_ip_after_modify",
    "validate_modify_ip_dry_run",
]
