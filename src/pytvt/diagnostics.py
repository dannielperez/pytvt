"""Runtime diagnostics for pytvt installation and SDK availability.

Provides a single ``diagnostics()`` function that inspects the current
environment and returns a structured report of what is available, what
is missing, and any configuration issues.

Usage::

    from pytvt.diagnostics import diagnostics
    report = diagnostics()
    print(report)          # human-readable summary
    report.to_dict()       # machine-readable dict
"""

from __future__ import annotations

import ctypes as ct
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .device_sdk.loader import (
    _NAT_DEPENDENCY_GROUP,
    _OPTIONAL_DEPENDENCY_GROUPS,
    LEGACY_SDK_PATH_ENV_VAR,
    LIB_NAME,
    SDK_PATH_ENV_VAR,
    NetSdkUnavailable,
    _candidate_targets,
    _find_lib,
    _lib_dir,
)


@dataclass
class LibraryStatus:
    """Status of a single shared library."""

    name: str
    loaded: bool
    path: str | None = None
    error: str | None = None


@dataclass
class DiagnosticsReport:
    """Structured diagnostics report for pytvt runtime state."""

    pytvt_version: str = ""
    python_version: str = ""
    platform_system: str = ""
    platform_machine: str = ""
    sdk_path_env: str | None = None
    sdk_path_legacy_env: str | None = None
    sdk_path_resolved: str | None = None
    sdk_path_exists: bool = False
    sdk_path_is_absolute: bool = False
    sdk_available: bool = False
    nat_available: bool = False
    main_library: LibraryStatus | None = None
    companion_libraries: list[LibraryStatus] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict."""
        d = {
            "pytvt_version": self.pytvt_version,
            "python_version": self.python_version,
            "platform": f"{self.platform_system}/{self.platform_machine}",
            "sdk_path": {
                "env": self.sdk_path_env,
                "legacy_env": self.sdk_path_legacy_env,
                "resolved": self.sdk_path_resolved,
                "exists": self.sdk_path_exists,
                "is_absolute": self.sdk_path_is_absolute,
            },
            "sdk_available": self.sdk_available,
            "nat_available": self.nat_available,
        }
        if self.main_library:
            d["main_library"] = {
                "name": self.main_library.name,
                "loaded": self.main_library.loaded,
                "path": self.main_library.path,
                "error": self.main_library.error,
            }
        d["companion_libraries"] = [
            {"name": lib.name, "loaded": lib.loaded, "path": lib.path, "error": lib.error}
            for lib in self.companion_libraries
        ]
        d["warnings"] = self.warnings
        return d

    def __str__(self) -> str:
        lines = [
            f"pytvt diagnostics ({self.pytvt_version})",
            f"  Python:   {self.python_version}",
            f"  Platform: {self.platform_system}/{self.platform_machine}",
            "",
            "SDK path:",
            f"  $TVT_SDK_PATH:      {self.sdk_path_env or '(not set)'}",
            f"  $PYTVT_NETSDK_LIB:  {self.sdk_path_legacy_env or '(not set)'}",
            f"  Resolved:           {self.sdk_path_resolved or '(none)'}",
            f"  Exists:             {self.sdk_path_exists}",
            f"  Absolute:           {self.sdk_path_is_absolute}",
            "",
            f"SDK available: {self.sdk_available}",
            f"NAT available: {self.nat_available}",
        ]

        if self.main_library:
            status = "OK" if self.main_library.loaded else f"FAILED ({self.main_library.error})"
            lines.append(f"\n  {self.main_library.name}: {status}")
            if self.main_library.path:
                lines.append(f"    path: {self.main_library.path}")

        if self.companion_libraries:
            lines.append("")
            lines.append("Companion libraries:")
            for lib in self.companion_libraries:
                status = "OK" if lib.loaded else f"missing ({lib.error})" if lib.error else "missing"
                lines.append(f"  {lib.name}: {status}")

        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  - {w}")

        return "\n".join(lines)


def _probe_library(names: tuple[str, ...], lib_dir: str | None) -> LibraryStatus:
    """Attempt to load a library from candidate paths and report status."""
    for target in _candidate_targets(names, lib_dir):
        try:
            ct.CDLL(target)
            return LibraryStatus(name=names[0], loaded=True, path=target)
        except OSError:
            continue
    return LibraryStatus(name=names[0], loaded=False, error="not found in search paths")


def diagnostics(sdk_path: str | os.PathLike[str] | None = None) -> DiagnosticsReport:
    """Run a full diagnostic check and return a structured report.

    Parameters
    ----------
    sdk_path:
        Explicit SDK path override.  If ``None``, uses ``$TVT_SDK_PATH``
        then ``$PYTVT_NETSDK_LIB`` then system search.
    """
    report = DiagnosticsReport(
        pytvt_version=__version__,
        python_version=platform.python_version(),
        platform_system=platform.system(),
        platform_machine=platform.machine(),
        sdk_path_env=os.environ.get(SDK_PATH_ENV_VAR),
        sdk_path_legacy_env=os.environ.get(LEGACY_SDK_PATH_ENV_VAR),
    )

    # --- Warnings ---
    if report.sdk_path_legacy_env and not report.sdk_path_env:
        report.warnings.append(f"Using legacy ${LEGACY_SDK_PATH_ENV_VAR}; migrate to ${SDK_PATH_ENV_VAR}.")

    if report.platform_system != "Linux":
        report.warnings.append(
            f"Native SDK requires Linux (current: {report.platform_system}). Only pure-Python backends available."
        )
        return report

    if report.platform_machine not in ("x86_64", "aarch64"):
        report.warnings.append(f"Unsupported architecture: {report.platform_machine}.")
        return report

    # --- Resolve SDK path ---
    try:
        lib_path = _find_lib(sdk_path)
        report.sdk_path_resolved = lib_path
    except NetSdkUnavailable as exc:
        report.sdk_path_resolved = None
        report.warnings.append(str(exc))
        return report

    resolved_p = Path(lib_path).expanduser()
    report.sdk_path_exists = resolved_p.exists()
    report.sdk_path_is_absolute = resolved_p.is_absolute()

    if not report.sdk_path_is_absolute and report.sdk_path_env:
        report.warnings.append(
            "SDK path resolves to a relative path. Use an absolute path to avoid working-directory-dependent behavior."
        )

    lib_dir = _lib_dir(lib_path)

    # --- Main library probe ---
    try:
        ct.CDLL(lib_path)
        report.main_library = LibraryStatus(name=LIB_NAME, loaded=True, path=lib_path)
        report.sdk_available = True
    except OSError as exc:
        report.main_library = LibraryStatus(name=LIB_NAME, loaded=False, path=lib_path, error=str(exc))
        return report

    # --- Companion library probes ---
    for names in _OPTIONAL_DEPENDENCY_GROUPS:
        report.companion_libraries.append(_probe_library(names, lib_dir))

    nat_status = _probe_library(_NAT_DEPENDENCY_GROUP, lib_dir)
    report.companion_libraries.append(nat_status)
    report.nat_available = nat_status.loaded

    return report
