"""Response-envelope parsing for the TVT NVMS management web API.

Every ``/service/*`` endpoint answers with one JSON envelope::

    {"retCode": "1", "retMsg": "...", "resultXml": "<response>...</response>"}

``retCode == "1"`` means the web tier reached the internal service (transport
OK); the *application* verdict lives inside ``resultXml``::

    <response>
      <status>success|fail</status>
      [<errorCode>NNN</errorCode>]
      <content>...scalar fields and repeated <item> elements...</content>
    </response>

This module turns that double wrapping into one immutable, secret-free value
object. It is pure parsing — no network, no crypto, no session state.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from pytvt.platform_sdk.exceptions import ProtocolError

from .alarm_configuration import configuration_items

_TRANSPORT_OK = "1"
# Confirmed attribute-shaped server status rows. Keep credentials/session
# attributes out of the envelope rather than copying arbitrary attributes.
_ITEM_ATTRIBUTES = frozenset(
    {
        "id",
        "pId",
        "name",
        "ip",
        "port",
        "type",
        "state",
        "stateType",
        "lastOnLineTime",
        "lastOffLineTime",
    }
)


@dataclass(frozen=True)
class WebEnvelope:
    """Parsed ``/service/*`` response envelope.

    Attributes:
        ret_code: The JSON ``retCode`` (``"1"`` = transport OK).
        ret_msg: The JSON ``retMsg`` diagnostic string.
        status: The XML ``response>status`` text (``success`` or ``fail``).
        error_code: The XML ``response>errorCode`` text on failure, else ``None``.
        content: Scalar children of ``response>content`` as tag → stripped text.
        items: Repeated ``response>content>item`` elements, each flattened to
            tag → stripped text.
    """

    ret_code: str
    ret_msg: str
    status: str
    error_code: str | None
    content: dict[str, str]
    items: list[dict[str, str]]

    alarm_configurations: list[dict] = field(default_factory=list)
    log_types: list[str] = field(default_factory=list)
    content_attributes: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when the application status is ``success``."""
        return self.status == "success"


def _flatten(element: ET.Element) -> dict[str, str]:
    """Map an element's children to ``tag -> stripped text`` (childless tags only)."""
    return {child.tag: (child.text or "").strip() for child in element if len(child) == 0}


def parse_envelope(payload: str | bytes) -> WebEnvelope:
    """Parse a raw ``/service/*`` response body into a :class:`WebEnvelope`.

    Args:
        payload: The HTTP response body (JSON text, UTF-8 if bytes).

    Returns:
        The parsed envelope. Application-level failure (``status == "fail"``)
        is *returned*, not raised — the caller decides how to map it.

    Raises:
        ProtocolError: If the body is not the documented JSON envelope, the
            transport ``retCode`` is not ``"1"``, or ``resultXml`` is missing
            or not parseable XML.
    """
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    try:
        outer = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"management web response is not JSON: {exc}") from exc
    if not isinstance(outer, dict):
        raise ProtocolError(f"management web response is not a JSON object: {type(outer).__name__}")

    ret_code = str(outer.get("retCode", ""))
    ret_msg = str(outer.get("retMsg", ""))
    if ret_code != _TRANSPORT_OK:
        raise ProtocolError(f"management web transport error: retCode={ret_code!r} retMsg={ret_msg!r}")

    result_xml = outer.get("resultXml")
    if not result_xml:
        raise ProtocolError("management web response has no resultXml")
    try:
        root = ET.fromstring(result_xml)
    except ET.ParseError as exc:
        raise ProtocolError(f"management web resultXml is not valid XML: {exc}") from exc

    status_node = root.find("status")
    status = (status_node.text or "").strip() if status_node is not None else ""
    error_node = root.find("errorCode")
    error_code = (error_node.text or "").strip() if error_node is not None else None

    content_node = root.find("content")
    content: dict[str, str] = {}
    items: list[dict[str, str]] = []
    if content_node is not None:
        content = {
            child.tag: (child.text or "").strip() for child in content_node if child.tag != "item" and len(child) == 0
        }
        items = [
            {**_flatten(item), **{k: v for k, v in item.attrib.items() if k in _ITEM_ATTRIBUTES}}
            for item in content_node.findall("item")
        ]

    try:
        alarm_configurations = configuration_items(content_node)
    except ValueError as exc:
        raise ProtocolError("Alarm configuration exceeds projection limits") from exc

    return WebEnvelope(
        ret_code=ret_code,
        ret_msg=ret_msg,
        status=status,
        error_code=error_code,
        content=content,
        items=items,
        alarm_configurations=alarm_configurations,
        content_attributes=dict(content_node.attrib) if content_node is not None else {},
        log_types=list(
            dict.fromkeys(
                item.attrib["type"]
                for section in ("alarmLog", "operateLog", "configLog", "exceptionLog")
                for item in root.findall(f"content/{section}/item")
                if item.attrib.get("type")
            )
        ),
    )
