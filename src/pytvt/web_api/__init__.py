"""TVT HTTP Web API client (LAPI).

This sub-package implements the TVT HTTP API protocol (``/LAPI/V1.0/...``)
as documented in the *HTTP API Protocol User Guide for IP Media Device v2.0.0*.

It is distinct from :class:`pytvt.xml_api.NvrClient` which targets the
NVMS-9000 NVR CGI interface.  The Web API uses HTTP Basic auth per request
and XML request/response bodies.

Typical usage::

    from pytvt.web_api import WebApiClient

    client = WebApiClient("192.168.1.100", "admin", "password")
    caps = client.get_supported_apis()
    info = client.get_device_info()
    snap = client.get_snapshot(channel_id=1)
"""

from .client import WebApiClient

__all__ = ["WebApiClient"]
