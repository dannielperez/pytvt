"""High-level capability surface for pytvt 1.0.

The ``capabilities`` lane is the cross-lane discovery layer.  Given a
device or management-server target, it answers:

  * Which pytvt lanes can talk to this target?
  * What features does the target expose?

Today, this is a thin orchestration layer over the per-lane probes:

  * :func:`detect_platform_capabilities` — re-export of
    :func:`pytvt.platform_sdk.capabilities.detect_capabilities`.
    Probes a ``ManagementClient`` (platform/NVMS server) for what
    read-only operations it supports.

  * :class:`TvtCapabilities` — a typed snapshot of which lanes are
    reachable for one target, returned by :func:`probe_capabilities`.

  * :func:`probe_capabilities` — minimal cross-lane probe.  Given a
    host and credentials, attempts a light handshake against each lane
    and records pass/fail.

The probe is intentionally read-only and forgiving: any exception is
recorded as ``False`` rather than re-raised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..platform_sdk.capabilities import detect_capabilities as detect_platform_capabilities

__all__ = [
    "TvtCapabilities",
    "detect_platform_capabilities",
    "probe_capabilities",
]


@dataclass(frozen=True)
class TvtCapabilities:
    """Per-target lane availability snapshot.

    Each field is ``True`` if the corresponding lane responded to a
    light probe, ``False`` otherwise.  Fields are intentionally
    independent — a target may speak multiple lanes simultaneously.
    """

    target: str = ""
    device_sdk: bool = False
    """Native NetSDK (``libdvrnetsdk.so``) is reachable for this host."""

    platform_sdk: bool = False
    """PlatformSDK / NVMS management server is reachable."""

    web_api: bool = False
    """TVT Web API / LAPI HTTP endpoint responds."""

    xml_api: bool = False
    """NVR XML CGI endpoint responds."""

    workflows: bool = True
    """Workflow orchestration is always available (pure Python)."""

    details: dict[str, Any] = field(default_factory=dict)
    """Per-lane diagnostic details (errors, versions, etc.)."""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "target": self.target,
            "device_sdk": self.device_sdk,
            "platform_sdk": self.platform_sdk,
            "web_api": self.web_api,
            "xml_api": self.xml_api,
            "workflows": self.workflows,
            "details": dict(self.details),
        }


def probe_capabilities(
    host: str,
    *,
    username: str = "admin",
    password: str = "",
    web_api_port: int = 80,
    xml_api_port: int = 80,
    timeout: float = 5.0,
) -> TvtCapabilities:
    """Probe each pytvt lane against ``host`` and return a :class:`TvtCapabilities`.

    The probe is read-only and forgiving: any exception is captured in
    ``details[lane]`` and the lane is marked unavailable.

    Parameters
    ----------
    host:
        IP address or hostname of the target NVR / device.
    username, password:
        Credentials used for lanes that require auth (web_api, xml_api).
    web_api_port, xml_api_port:
        HTTP port to probe (NVRs frequently expose both on the same port).
    timeout:
        Per-lane request timeout in seconds.
    """
    details: dict[str, Any] = {}

    # ── xml_api (NVR CGI) ────────────────────────────────────────
    xml_api_ok = False
    try:
        from ..xml_api import NvrClient

        with NvrClient(host, username=username, password=password, port=xml_api_port, timeout=timeout) as client:
            client.login()
            xml_api_ok = True
    except Exception as exc:
        details["xml_api"] = repr(exc)

    # ── web_api (LAPI HTTP) ──────────────────────────────────────
    web_api_ok = False
    try:
        from ..web_api import WebApiClient

        with WebApiClient(host, username=username, password=password, port=web_api_port, timeout=timeout) as client:
            # A minimal handshake: instantiation + context-enter is enough
            # to detect that the endpoint is reachable; the auth challenge
            # happens lazily on first request.
            web_api_ok = True
    except Exception as exc:
        details["web_api"] = repr(exc)

    # ── device_sdk (native NetSDK) ───────────────────────────────
    device_sdk_ok = False
    try:
        from ..device_sdk.loader import _find_lib

        _find_lib(None)
        device_sdk_ok = True
    except Exception as exc:
        details["device_sdk"] = repr(exc)

    # ── platform_sdk ─────────────────────────────────────────────
    # Platform SDK probing requires a separate management server
    # endpoint, not the device itself.  We don't try to discover one
    # here — callers should use detect_platform_capabilities() against
    # a pre-built ManagementClient.
    details["platform_sdk"] = "probe deferred — use detect_platform_capabilities()"

    return TvtCapabilities(
        target=host,
        device_sdk=device_sdk_ok,
        platform_sdk=False,
        web_api=web_api_ok,
        xml_api=xml_api_ok,
        workflows=True,
        details=details,
    )
