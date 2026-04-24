"""Local SDK backend implemented directly in Python via ctypes.

The historical ``sdk-local`` backend used a Node.js bridge script to call the
vendor SDK. That bridge has been removed. The backend name is retained for
backwards compatibility, but execution now stays entirely inside Python and
uses :class:`pytvt.netsdk.client.NetSdkClient` directly.
"""

from __future__ import annotations

from .models import CameraInfo, DeviceEntry, ScannerConfig, ScanResult
from .netsdk.client import NetSdkClient, NetSdkError
from .netsdk.loader import NetSdkUnavailable


def scan_nvr_payload(
    ip: str,
    port: int = 6036,
    username: str = "admin",
    password: str = "",
    *,
    sdk_path: str | None = None,
    max_channels: int = 64,
) -> dict:
    """Scan one NVR through the native SDK and return a JSON-safe payload."""
    result = {
        "nvr_ip": ip,
        "nvr_port": port,
        "success": False,
        "device_name": "",
        "device_model": "",
        "serial_number": "",
        "firmware": "",
        "total_channels": 0,
        "cameras": [],
        "error": None,
    }

    try:
        with NetSdkClient(
            sdk_path=sdk_path,
            connect_timeout=10_000,
            recv_timeout=10_000,
        ) as client:
            with client.login(ip, username, password, port=port) as session:
                device_info = session.device_info()
                result["device_name"] = device_info.device_name
                result["device_model"] = device_info.product
                result["serial_number"] = device_info.serial_number
                result["firmware"] = device_info.firmware

                try:
                    cameras = session.ipc_info(max_channels=max_channels)
                except NetSdkError as exc:
                    result["success"] = True
                    result["total_channels"] = device_info.video_inputs
                    result["error"] = f"Could not retrieve IPC info, but device is reachable: {exc}"
                    return result

                result["cameras"] = [
                    {
                        "channel": camera.channel,
                        "name": camera.name,
                        "address": camera.ip,
                        "port": camera.port,
                        "status": "Online" if camera.online else "Offline",
                        "protocol": camera.manufacturer,
                        "model": camera.model,
                    }
                    for camera in cameras
                ]
                result["total_channels"] = len(cameras) or device_info.video_inputs
                result["success"] = True
    except (NetSdkError, NetSdkUnavailable, OSError, ValueError) as exc:
        result["error"] = str(exc)

    return result


def sdk_scan_local(device: DeviceEntry, config: ScannerConfig) -> ScanResult:
    """Scan one NVR using the direct Python SDK backend."""
    port = device.effective_port(config)
    payload = scan_nvr_payload(
        device.ip,
        port=port,
        username=config.username,
        password=config.password,
        sdk_path=config.sdk_path,
        max_channels=config.max_channels,
    )

    return ScanResult(
        site=device.site,
        hostname=device.hostname,
        nvr_ip=device.ip,
        nvr_mac=device.mac,
        nvr_port=port,
        success=payload.get("success", False),
        device_name=payload.get("device_name", ""),
        device_model=payload.get("device_model", ""),
        serial_number=payload.get("serial_number", ""),
        firmware=payload.get("firmware", ""),
        total_channels=payload.get("total_channels", 0),
        cameras=[CameraInfo.from_dict(camera) for camera in payload.get("cameras", [])],
        error=payload.get("error"),
        backend="sdk-local",
        device_info=payload.get("device_info", {}),
    )
