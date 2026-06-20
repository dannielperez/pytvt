"""Symbol inspection helpers for evidence-driven SDK capability reporting."""

from __future__ import annotations

import ctypes
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

SYMBOL_GROUP_PATTERNS = (
    re.compile(r"^NET_SDK_"),
    re.compile(r"^Plat_"),
)

LIFECYCLE_HINT_PATTERN = re.compile(r"(Login|Logout|Init|Cleanup)", re.IGNORECASE)

WINDOWS_REFERENCE_SYMBOLS: tuple[str, ...] = (
    "NET_SDK_Init",
    "NET_SDK_Login",
    "NET_SDK_LoginEx",
    "NET_SDK_Logout",
    "NET_SDK_Cleanup",
    "NET_SDK_GetDeviceIPCInfo",
)

CAPABILITY_SYMBOL_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "supports_init": ("NET_SDK_Init",),
    "supports_login": ("NET_SDK_Login",),
    "supports_login_ex": ("NET_SDK_LoginEx",),
    "supports_logout": ("NET_SDK_Logout",),
    "supports_cleanup": ("NET_SDK_Cleanup",),
    "supports_device_enumeration": ("NET_SDK_GetDeviceIPCInfo",),
}


class SymbolInspectionError(Exception):
    """Raised when symbol inventory cannot be collected."""


def _load_library(sdk_path: str) -> ctypes.CDLL:
    path = Path(sdk_path)
    if not path.exists() or not path.is_file():
        raise SymbolInspectionError(f"SDK path is not a file: {sdk_path!r}")
    try:
        return ctypes.CDLL(str(path))
    except OSError as exc:
        raise SymbolInspectionError(f"ctypes could not load SDK: {exc}") from exc


def list_exported_symbols(sdk_path: str) -> tuple[set[str], dict[str, Any]]:
    """Return exported symbols from nm with ctypes fallback for curated probes."""
    if not sdk_path:
        return set(), {"source": "none", "error": "sdk_path_not_configured"}

    nm_path = shutil.which("nm")
    if nm_path:
        try:
            result = subprocess.run(
                [nm_path, "-D", "--defined-only", sdk_path],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                symbols = set()
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if parts:
                        symbols.add(parts[-1])
                return symbols, {"source": "nm", "error": None}
            stderr = (result.stderr or "").strip() or f"nm exited with status {result.returncode}"
        except OSError as exc:
            stderr = f"nm execution failed: {exc}"
    else:
        stderr = "nm_not_available"

    # Fallback: only curated symbol probing via ctypes; never guesses arg counts.
    try:
        lib = _load_library(sdk_path)
    except SymbolInspectionError as exc:
        return set(), {"source": "ctypes_probe", "error": str(exc)}

    discovered: set[str] = set()
    candidates = set(WINDOWS_REFERENCE_SYMBOLS)
    for symbol in candidates:
        if getattr(lib, symbol, None) is not None:
            discovered.add(symbol)
    return discovered, {"source": "ctypes_probe", "error": stderr}


def symbol_exists(exported_symbols: set[str], name: str) -> bool:
    return name in exported_symbols


def build_symbol_inventory(exported_symbols: set[str]) -> list[dict[str, Any]]:
    """Return filtered inventory for target groups with explicit presence flags."""
    filtered = sorted(
        name
        for name in exported_symbols
        if any(pattern.search(name) for pattern in SYMBOL_GROUP_PATTERNS)
        or (name.startswith("NET_") and LIFECYCLE_HINT_PATTERN.search(name))
    )
    return [{"name": name, "present": True, "args_count": None} for name in filtered]


def build_symbol_presence_checks(
    exported_symbols: set[str], names: list[str] | tuple[str, ...]
) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "present": symbol_exists(exported_symbols, name),
            "args_count": None,
        }
        for name in names
    ]


def build_windows_parity_report(exported_symbols: set[str]) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for symbol in WINDOWS_REFERENCE_SYMBOLS:
        report.append(
            {
                "symbol": symbol,
                "windows_reference": True,
                "active_sdk_present": symbol_exists(exported_symbols, symbol),
            }
        )
    return report


def build_symbol_capability_evidence(exported_symbols: set[str]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for capability, symbols in CAPABILITY_SYMBOL_REQUIREMENTS.items():
        present = [name for name in symbols if symbol_exists(exported_symbols, name)]
        evidence[capability] = {
            "source": "symbols",
            "symbols": list(symbols),
            "confirmed": bool(present),
            "present_symbols": present,
        }
    return evidence
