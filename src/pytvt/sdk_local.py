"""SDK local subprocess backend — scans NVRs via ``scan_nvr.mjs`` under Node.js.

Requires Node.js 18+ and the native TVT SDK shared library on Linux x86-64.
The subprocess writes JSON between sentinel markers so that any SDK noise on
stdout is safely ignored.
"""

from __future__ import annotations

import json
import os
import subprocess

from .config import resolve_scan_script
from .models import CameraInfo, DeviceEntry, ScannerConfig, ScanResult

# Sentinel markers emitted by scan_nvr.mjs around the JSON payload.
_JSON_START = "___JSON_START___"
_JSON_END = "___JSON_END___"


def sdk_scan_local(device: DeviceEntry, config: ScannerConfig) -> ScanResult:
    """Scan one NVR by running ``scan_nvr.mjs`` as a subprocess.

    Args:
        device: Inventory entry for the NVR.
        config: Scanner configuration.

    Returns:
        A :class:`ScanResult` — ``success`` is *False* on any subprocess or
        parse error.
    """
    result = ScanResult.for_device(device, config, backend="sdk-local")
    timeout = config.timeout + 30
    scan_script = resolve_scan_script(config.scan_script)

    if scan_script is None:
        result.error = (
            "sdk-local backend requires a bridge script. Set TVT_SCAN_SCRIPT or "
            "ScannerConfig(scan_script=...) to your local scan_nvr.mjs path."
        )
        return result

    if not scan_script.exists():
        result.error = (
            f"sdk-local bridge script not found at {scan_script}. Set TVT_SCAN_SCRIPT or "
            "ScannerConfig(scan_script=...) to a valid path."
        )
        return result

    env = os.environ.copy()
    if config.sdk_path and not env.get("TVT_SDK_PATH") and not env.get("PYTVT_NETSDK_LIB"):
        env["TVT_SDK_PATH"] = config.sdk_path

    try:
        proc = subprocess.run(
            [
                "node",
                str(scan_script),
                device.ip,
                str(device.effective_port(config)),
                config.username,
                config.password,
            ],
            capture_output=True,
            env=env,
            text=True,
            timeout=timeout,
        )

        json_str = _extract_json(proc.stdout)
        if json_str is None:
            result.error = f"No JSON markers in output. stderr: {proc.stderr[:500]}"
            return result

        data = json.loads(json_str)

        result.success = data.get("success", False)
        result.device_name = data.get("device_name", "")
        result.device_model = data.get("device_model", "")
        result.serial_number = data.get("serial_number", "")
        result.firmware = data.get("firmware", "")
        result.total_channels = data.get("total_channels", 0)
        result.cameras = [CameraInfo.from_dict(c) for c in data.get("cameras", [])]
        result.error = data.get("error")

    except subprocess.TimeoutExpired:
        result.error = f"Subprocess timeout after {timeout}s"
    except json.JSONDecodeError as e:
        result.error = f"Invalid JSON from {scan_script.name}: {e}"
    except FileNotFoundError:
        result.error = "node not found in PATH"

    return result


def _extract_json(stdout: str) -> str | None:
    """Return the JSON string between sentinel markers, or *None*."""
    start = stdout.find(_JSON_START)
    end = stdout.find(_JSON_END)
    if start == -1 or end == -1:
        return None
    return stdout[start + len(_JSON_START) : end].strip()
