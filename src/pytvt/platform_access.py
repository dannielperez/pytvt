"""Platform Access (auto-report) XML parsing shared by the HTTP and NetSDK paths.

The NVR web CGI ``queryPlatformCfg`` answers with::

    <content type="list" current="NVMS5000">
      <item id="NVMS5000"><switch>true</switch><serverAddr>…</serverAddr>
        <port>2009</port><reportId>…</reportId></item>
    </content>

The same command can be issued over HTTP (``NvrClient``) or tunnelled through
``NET_SDK_ApiInterface`` on an authenticated NetSDK handle (LAN or NAT).  Keep
one lenient regex parser here so both transports return the same
:class:`~pytvt.models.PlatformAccessConfig`.
"""

from __future__ import annotations

import re

from .models import PlatformAccessConfig

_DEFAULT_PORT = 2009


def _field(xml: str, name: str) -> str | None:
    match = re.search(rf"<{name}(?:\s[^>]*)?>(.+?)</{name}>", xml, re.DOTALL)
    return match.group(1).strip() if match else None


def response_status(xml: str) -> tuple[str | None, str | None]:
    """Return ``(status, errorCode)`` from a device CGI response, if present."""
    status = re.search(r"<status>(.*?)</status>", xml)
    code = re.search(r"<errorCode>(.*?)</errorCode>", xml)
    return (status.group(1) if status else None, code.group(1) if code else None)


def parse_platform_access_config(xml: str) -> PlatformAccessConfig:
    """Parse a ``queryPlatformCfg`` body into :class:`PlatformAccessConfig`.

    Prefers the ``NVMS5000`` item when the list carries several platforms.
    Missing fields degrade to their defaults; callers check response status.
    """
    item = re.search(r'<item\s+id="NVMS5000">(.*?)</item>', xml, re.DOTALL)
    block = item.group(1) if item else xml
    port_text = _field(block, "port")
    try:
        port = int(port_text) if port_text else _DEFAULT_PORT
    except ValueError:
        port = _DEFAULT_PORT
    return PlatformAccessConfig(
        enabled=(_field(block, "switch") == "true"),
        server_address=_field(block, "serverAddr") or "",
        port=port,
        report_id=_field(block, "reportId") or "",
    )
