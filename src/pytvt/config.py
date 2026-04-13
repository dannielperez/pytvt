"""Configuration loading for pytvt.

Precedence (first wins):
    1. CLI flags  — applied by the caller after :func:`load_config` returns.
    2. Environment variables  (``TVT_USERNAME``, ``TVT_PORT``, …).
    3. JSON config file  (``config.json`` by default).
    4. Built-in defaults in :class:`~pytvt.models.ScannerConfig`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import ScannerConfig

# Default API URL for the TVT SDK Fastify server
DEFAULT_API_URL: str = os.getenv("TVT_API_URL", "http://localhost:3000")


# Path to the standalone scan_nvr.mjs script (for sdk-local backend).
#
# Resolution order:
#   1. ``$PYTVT_SCAN_SCRIPT`` environment variable — explicit override.
#   2. Walk up from this file looking for ``bridges/sdk_local/scan_nvr.mjs``
#      (works in a development / editable-install clone of the repo).
#
# The sdk-local backend is not available in a plain ``pip install pytvt``
# deployment because the Node.js bridge script and native libdvrnetsdk.so are
# not distributed with the Python package.  The backend will fail cleanly with
# a descriptive error when the script cannot be found.  Set ``PYTVT_SCAN_SCRIPT``
# to point at your local copy of ``scan_nvr.mjs`` to enable sdk-local support.
def _resolve_scan_script() -> Path:
    env_override = os.getenv("PYTVT_SCAN_SCRIPT")
    if env_override:
        return Path(env_override)
    # Walk up the directory tree looking for the bridge script (dev clone).
    anchor = Path(__file__).resolve().parent
    for parent in [anchor, *anchor.parents]:
        candidate = parent / "bridges" / "sdk_local" / "scan_nvr.mjs"
        if candidate.exists():
            return candidate
    # Return a non-existent path so callers can check ``.exists()`` and report
    # a clear error rather than silently failing.
    return Path(__file__).resolve().parent / "_not_found_" / "scan_nvr.mjs"


SCAN_SCRIPT: Path = _resolve_scan_script()

# Mapping from config-file / env-var keys → (ScannerConfig field, type cast)
_FIELD_MAP: dict[str, type] = {
    "username": str,
    "password": str,
    "port": int,
    "timeout": int,
    "max_channels": int,
    "concurrency": int,
}


def load_config(config_path: str | None = None) -> ScannerConfig:
    """Build a :class:`ScannerConfig` from env vars and an optional JSON file.

    Args:
        config_path: Path to a JSON config file, or *None* to skip.

    Returns:
        Fully-resolved :class:`ScannerConfig`.
    """
    # Start with built-in defaults
    values: dict = {}

    # Layer 1: JSON file (lowest precedence of the two sources we read)
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            file_cfg = json.load(f)
        for key, cast in _FIELD_MAP.items():
            if key in file_cfg:
                values[key] = cast(file_cfg[key])

    # Layer 2: Environment variables (override file values)
    _env = {
        "username": os.getenv("TVT_USERNAME"),
        "password": os.getenv("TVT_PASSWORD"),
        "port": os.getenv("TVT_PORT"),
        "timeout": os.getenv("TVT_TIMEOUT"),
        "max_channels": os.getenv("TVT_MAX_CHANNELS"),
        "concurrency": os.getenv("TVT_CONCURRENCY"),
    }
    for key, raw in _env.items():
        if raw is not None:
            values[key] = _FIELD_MAP[key](raw)

    # API URL — env only (no config-file key)
    values["api_url"] = DEFAULT_API_URL

    return ScannerConfig(**values)
