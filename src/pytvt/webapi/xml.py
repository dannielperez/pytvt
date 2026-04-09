"""XML building and parsing helpers for the TVT HTTP API.

The TVT Web API uses XML request and response bodies.  This module
provides lightweight helpers that avoid any dependency on ``lxml`` or
``xml.etree`` for construction (plain string templates) while using the
stdlib ``xml.etree.ElementTree`` for safe parsing of responses.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

# ── Request building ─────────────────────────────────────────────────


def wrap_request(inner_xml: str = "") -> str:
    """Wrap content in the standard TVT API request envelope.

    Args:
        inner_xml: XML fragment to include inside the request body.
            Pass empty string for GET-style requests that need no body.

    Returns:
        Complete XML string ready for POST.
    """
    if inner_xml:
        return f'<?xml version="1.0" encoding="utf-8"?>{inner_xml}'
    return ""


def build_set_request(tag: str, fields: dict[str, Any]) -> str:
    """Build a simple XML request with a root element and child fields.

    Example::

        build_set_request("DateAndTime", {"dateTimeMode": "NTP"})
        # → '<?xml version="1.0" encoding="utf-8"?><DateAndTime><dateTimeMode>NTP</dateTimeMode></DateAndTime>'

    Args:
        tag: Root element name.
        fields: Dict of child element name → value.  Values are converted
            to strings.  ``None`` values are skipped.

    Returns:
        Complete XML string with declaration.
    """
    children = "".join(
        f"<{k}>{v}</{k}>" for k, v in fields.items() if v is not None
    )
    return wrap_request(f"<{tag}>{children}</{tag}>")


# ── Response parsing ─────────────────────────────────────────────────


def parse_response(xml_bytes: bytes) -> ET.Element:
    """Parse an XML response body into an ElementTree root.

    Args:
        xml_bytes: Raw response bytes.

    Returns:
        The root :class:`xml.etree.ElementTree.Element`.

    Raises:
        ET.ParseError: If the response is not valid XML.
    """
    return ET.fromstring(xml_bytes)


def find_text(element: ET.Element, path: str, default: str = "") -> str:
    """Find a descendant element and return its text content.

    Supports both direct child lookup and dot-separated paths
    (e.g. ``"DeviceInfo/deviceName"``).

    Args:
        element: Parent element to search within.
        path: Element tag name or '/'-separated path.
        default: Value to return if the element is not found.

    Returns:
        Text content of the found element, or *default*.
    """
    node = element.find(path)
    if node is not None and node.text is not None:
        return node.text.strip()
    return default


def find_int(element: ET.Element, path: str, default: int = 0) -> int:
    """Find a descendant element and return its text as an integer."""
    text = find_text(element, path)
    if text:
        try:
            return int(text)
        except ValueError:
            return default
    return default


def find_bool(element: ET.Element, path: str, default: bool = False) -> bool:
    """Find a descendant element and return its text as a boolean.

    Recognises ``"true"`` / ``"false"`` (case-insensitive).
    """
    text = find_text(element, path).lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return default


def find_all_text(element: ET.Element, path: str) -> list[str]:
    """Find all matching descendant elements and return their text content."""
    return [
        (node.text or "").strip()
        for node in element.findall(path)
        if node.text
    ]


def extract_status(root: ET.Element) -> tuple[int, int, str]:
    """Extract statusCode, subStatusCode, and statusString from a response.

    The TVT API wraps every response in a ``<ResponseStatus>`` element
    (or includes status fields at the root level).

    Returns:
        (status_code, sub_code, status_string) tuple.
    """
    # Try <ResponseStatus> wrapper first
    rs = root.find("ResponseStatus")
    if rs is None:
        rs = root  # status fields may be at root level

    status_code = find_int(rs, "statusCode", 200)
    sub_code = find_int(rs, "subStatusCode", 0)
    status_string = find_text(rs, "statusString", "")

    return status_code, sub_code, status_string
