"""SDK HTTP API backend — scans NVRs via the tvt-api Fastify server.

The Fastify server (``tvt-api``) wraps the native TVT SDK and exposes a
``/scan`` endpoint. This module posts a scan request and converts the
response into a :class:`~pytvt.models.ScanResult`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .models import CameraInfo, DeviceEntry, ScannerConfig, ScanResult


def sdk_scan(device: DeviceEntry, config: ScannerConfig) -> ScanResult:
    """Scan one NVR by calling the TVT SDK Fastify API.

    Args:
        device: Inventory entry for the NVR.
        config: Scanner configuration (``api_url`` must be set).

    Returns:
        A :class:`ScanResult` — ``success`` is *False* on any HTTP / parse error.
    """
    result = ScanResult.for_device(device, config, backend="sdk")
    timeout = config.timeout + 15

    try:
        payload = json.dumps(
            {
                "ip": device.ip,
                "port": device.effective_port(config),
                "username": config.username,
                "password": config.password,
                "maxCameras": config.max_channels,
            }
        ).encode()

        req = urllib.request.Request(
            f"{config.api_url}/scan",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())

        result.success = data.get("success", False)
        result.device_name = data.get("device_name", "")
        result.device_model = data.get("device_model", "")
        result.serial_number = data.get("serial_number", "")
        result.firmware = data.get("firmware", "")
        result.total_channels = data.get("total_channels", 0)
        result.cameras = [CameraInfo.from_dict(c) for c in data.get("cameras", [])]
        result.error = data.get("error")

    except urllib.error.URLError as e:
        result.error = f"API connection error: {e.reason}"
    except TimeoutError:
        result.error = f"API timeout after {timeout}s"
    except json.JSONDecodeError as e:
        result.error = f"Invalid JSON from API: {e}"

    return result
