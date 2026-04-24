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

from .models import ScannerConfig

# Default API URL for an SDK HTTP bridge service.
DEFAULT_API_URL: str = os.getenv("TVT_API_URL", "http://localhost:3000")


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


# Mapping from config-file / env-var keys → (ScannerConfig field, type cast)
_FIELD_MAP: dict[str, type] = {
    "username": str,
    "password": str,
    "port": int,
    "timeout": int,
    "max_channels": int,
    "concurrency": int,
    "sdk_path": str,
    "scan_script": str,
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
        "sdk_path": _first_env("TVT_SDK_PATH", "PYTVT_NETSDK_LIB"),
    }
    for key, raw in _env.items():
        if raw is not None:
            values[key] = _FIELD_MAP[key](raw)

    # API URL — env only (no config-file key)
    values["api_url"] = DEFAULT_API_URL

    return ScannerConfig(**values)
