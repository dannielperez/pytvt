"""Read-only parser for an IPC's HTTP POST (alarm/smart-event push) config.

``GetHttpPostConfig`` and its XML shape are DOC-derived from an OEM reference
(IPC v1.9, firmware 5.1.4.0, API 1.7), not captured from an installed camera.
Pure functions only: no network I/O, and no write/set counterpart on purpose.

An unrecognised payload raises :class:`~pytvt.models.NvrApiResponseShapeError`
rather than looking like an empty or disabled config. Credentials are never
stored; only their presence is reported. Error messages never echo values.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum

from ..models import NvrApiResponseShapeError
from .models import IpcHttpPostConfig, IpcHttpPostTarget

MAX_CONFIG_BYTES = 256 * 1024
MAX_ELEMENTS = 2048
MAX_TARGETS = 8
MAX_LIST_ITEMS = 64
MAX_UNKNOWN_FIELDS = 32
MAX_UNKNOWN_TEXT = 256

_IPC_NS = "{http://www.ipc.com/ver10}"
_SECRET_RE = re.compile(r"pass|pwd|secret|token|key|credential|cert|private", re.IGNORECASE)
_USER_RE = re.compile(r"user|account|login", re.IGNORECASE)

_ROOT_OWNED = frozenset({"httpPost", "httpPostV2"})
_V1_KNOWN = frozenset({"bSwitch", "protocolType", "serverIp", "serverPort", "keepaliveTimeval", "onlineStatus", "URL"})
_V2_KNOWN = frozenset({"postUrlConf"})
_V2_OWNED = frozenset({"httpPostV2/postUrlConf/urlList"})
_DATA_TYPE_TAGS = ("subscribeDateType", "subDataType")  # 5.1.x vs 5.3.1 / v2.1 spelling
_ITEM_KNOWN = frozenset(
    {
        "urlId",
        "switch",
        "url",
        "url/protocol",
        "url/domain",
        "url/port",
        "url/path",
        "url/authentication",
        "heatBeatSwitch",
        "keepaliveTimeval",
        "subscriptionEvents",
        "subscriptionEvents/item",
        *_DATA_TYPE_TAGS,
        *(f"{tag}/item" for tag in _DATA_TYPE_TAGS),
    }
)


def _shape(message: str) -> NvrApiResponseShapeError:
    return NvrApiResponseShapeError(f"GetHttpPostConfig: {message} (unrecognized response shape)")


def _one(parent: ET.Element, path: str) -> ET.Element | None:
    matches = parent.findall(path)
    if len(matches) > 1:
        raise _shape(f"ambiguous <{path}>")
    return matches[0] if matches else None


def _text(parent: ET.Element, path: str) -> str:
    node = _one(parent, path)
    return (node.text or "").strip() if node is not None else ""


def _bool(parent: ET.Element, path: str) -> bool | None:
    node = _one(parent, path)
    if node is None:
        return None
    value = (node.text or "").strip().lower()
    if value not in {"true", "false"}:
        raise _shape(f"non-boolean <{path}>")
    return value == "true"


def _uint(parent: ET.Element, path: str, *, maximum: int) -> int | None:
    value = _text(parent, path)
    if not value:
        return None
    if not (value.isascii() and value.isdigit() and len(value) <= 6 and int(value) <= maximum):
        raise _shape(f"invalid <{path}>")
    return int(value)


def _list(parent: ET.Element, path: str) -> tuple[str, ...]:
    node = _one(parent, path)
    if node is None:
        return ()
    items = tuple((item.text or "").strip() for item in node.findall("item"))
    if len(items) > MAX_LIST_ITEMS:
        raise _shape(f"<{path}> exceeds item limit")
    return tuple(item for item in items if item)


def _split_host(raw: str) -> tuple[str, bool, bool]:
    """Drop any ``user:pass@`` userinfo, reporting only that it was present."""
    if "@" not in raw:
        return raw, False, False
    userinfo, host = raw.rsplit("@", 1)
    user, sep, password = userinfo.partition(":")
    return host, bool(user), bool(sep and password)


def _split_path(raw: str) -> tuple[str, bool]:
    path = raw.split("#", 1)[0]
    path, sep, _query = path.partition("?")
    return path, bool(sep)


class _Unknown:
    """Collects unmodelled leaves (bounded) and flags secret-looking elements."""

    def __init__(self) -> None:
        self.fields: list[tuple[str, str]] = []
        self.has_username = False
        self.has_password = False

    def scan(
        self, element: ET.Element, known: frozenset[str], prefix: str = "", *, owned: frozenset[str] = frozenset()
    ) -> None:
        """Walk *element*; ``known`` paths are modelled, ``owned`` paths are parsed elsewhere."""
        for child in element:
            path = f"{prefix}{child.tag}"
            if path in owned:
                continue
            if _SECRET_RE.search(child.tag) or _USER_RE.search(child.tag):
                # Never retained. Only a non-empty value counts as configured.
                if "".join(child.itertext()).strip():
                    if _SECRET_RE.search(child.tag):
                        self.has_password = True
                    else:
                        self.has_username = True
                continue
            if path in known or len(child):
                self.scan(child, known, f"{path}/", owned=owned)
            elif len(self.fields) < MAX_UNKNOWN_FIELDS:
                self.fields.append((path, (child.text or "").strip()[:MAX_UNKNOWN_TEXT]))


def _parse_v1(block: ET.Element) -> IpcHttpPostTarget:
    if _one(block, "bSwitch") is None and _one(block, "serverIp") is None:
        raise _shape("<httpPost> has neither bSwitch nor serverIp")
    unknown = _Unknown()
    unknown.scan(block, _V1_KNOWN)
    host, has_user, has_pass = _split_host(_text(block, "serverIp"))
    path, has_query = _split_path(_text(block, "URL"))
    return IpcHttpPostTarget(
        version="v1",
        url_id=None,
        enabled=_bool(block, "bSwitch"),
        protocol="",
        host=host,
        port=_uint(block, "serverPort", maximum=65535),
        path=path,
        keepalive_seconds=_uint(block, "keepaliveTimeval", maximum=86400),
        online=_bool(block, "onlineStatus"),
        protocol_type=_text(block, "protocolType"),
        has_username=has_user or unknown.has_username,
        has_password=has_pass or unknown.has_password,
        has_query=has_query,
        unknown_fields=tuple(unknown.fields),
    )


def _parse_v2_item(item: ET.Element) -> IpcHttpPostTarget:
    url_id = _uint(item, "urlId", maximum=MAX_TARGETS)
    url = _one(item, "url")
    if url_id is None or url is None:
        raise _shape("<urlList> item without urlId/url")
    present = [tag for tag in _DATA_TYPE_TAGS if _one(item, tag) is not None]
    if len(present) > 1:
        raise _shape("both data-type list spellings present")
    unknown = _Unknown()
    unknown.scan(item, _ITEM_KNOWN)
    host, has_user, has_pass = _split_host(_text(url, "domain"))
    path, has_query = _split_path(_text(url, "path"))
    return IpcHttpPostTarget(
        version="v2",
        url_id=url_id,
        enabled=_bool(item, "switch"),
        protocol=_text(url, "protocol").lower(),
        host=host,
        port=_uint(url, "port", maximum=65535),
        path=path,
        authentication=_text(url, "authentication"),
        keepalive_seconds=_uint(item, "keepaliveTimeval", maximum=86400),
        heartbeat_enabled=_bool(item, "heatBeatSwitch"),
        data_types=_list(item, present[0]) if present else (),
        events=_list(item, "subscriptionEvents"),
        has_username=has_user or unknown.has_username,
        has_password=has_pass or unknown.has_password,
        has_query=has_query,
        unknown_fields=tuple(unknown.fields),
    )


def _parse_v2(block: ET.Element) -> tuple[tuple[IpcHttpPostTarget, ...], int | None]:
    conf = _one(block, "postUrlConf")
    url_list = _one(conf, "urlList") if conf is not None else None
    if url_list is None:
        raise _shape("<httpPostV2> without postUrlConf/urlList")
    items = url_list.findall("item")
    count, max_count = url_list.get("count"), url_list.get("maxCount")
    if count is not None and count != str(len(items)):
        raise _shape("<urlList> count does not match its items")
    limit = int(max_count) if max_count is not None and max_count.isdigit() else MAX_TARGETS
    if len(items) > min(limit, MAX_TARGETS):
        raise _shape("<urlList> exceeds target limit")
    targets = tuple(_parse_v2_item(item) for item in items)
    if len({t.url_id for t in targets}) != len(targets):
        raise _shape("duplicate urlId")
    return targets, int(max_count) if max_count is not None and max_count.isdigit() else None


def parse_http_post_config(body: bytes) -> IpcHttpPostConfig:
    """Parse a ``GetHttpPostConfig`` response into an :class:`IpcHttpPostConfig`.

    Raises:
        NvrApiResponseShapeError: oversize, non-XML, DOCTYPE/ENTITY, foreign
            namespace, or any shape the OEM reference does not describe.
    """
    if not body or len(body) > MAX_CONFIG_BYTES:
        raise _shape("size outside limits")
    try:
        text = body.decode("utf-8")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise _shape("XML declarations prohibited")
        root = ET.fromstring(text)
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise _shape("invalid XML") from exc
    nodes = list(root.iter())
    if len(nodes) > MAX_ELEMENTS:
        raise _shape("XML element limit exceeded")
    for node in nodes:
        if not isinstance(node.tag, str):
            continue  # comments / processing instructions
        if node.tag.startswith("{") and not node.tag.startswith(_IPC_NS):
            raise _shape("unsupported namespace")
        node.tag = node.tag.split("}")[-1]
    if root.tag != "config" or root.get("status", "success") != "success":
        raise _shape("not a successful <config> document")
    v1_block, v2_block = _one(root, "httpPost"), _one(root, "httpPostV2")
    if v1_block is None and v2_block is None:
        raise _shape("neither <httpPost> nor <httpPostV2> present")
    v2_targets, v2_max = _parse_v2(v2_block) if v2_block is not None else ((), None)
    unknown = _Unknown()
    unknown.scan(root, frozenset(), owned=_ROOT_OWNED)
    if v2_block is not None:
        unknown.scan(v2_block, frozenset(f"httpPostV2/{k}" for k in _V2_KNOWN), "httpPostV2/", owned=_V2_OWNED)
    return IpcHttpPostConfig(
        v1=_parse_v1(v1_block) if v1_block is not None else None,
        v2_targets=v2_targets,
        v2_max_targets=v2_max,
        unknown_fields=tuple(unknown.fields),
    )


# ── Conflict detection ───────────────────────────────────────────────


class HttpPostMatchStatus(str, Enum):
    MATCH = "match"  # a slot points at us with every compared field equal
    DIFFERS = "differs"  # a slot points at our host but other fields differ
    NOT_CONFIGURED = "not_configured"  # no slot for our host and no enabled foreign slot
    THIRD_PARTY = "third_party"  # only enabled slots for other receivers


@dataclass(frozen=True)
class HttpPostTargetMatch:
    status: HttpPostMatchStatus
    target: IpcHttpPostTarget | None = None
    differing_fields: tuple[str, ...] = ()
    # Enabled slots pushing to another receiver, reported whatever the status.
    third_party_targets: tuple[IpcHttpPostTarget, ...] = ()

    @property
    def has_third_party(self) -> bool:
        return bool(self.third_party_targets)


def _differences(target: IpcHttpPostTarget, *, port: int, path: str, protocol: str) -> tuple[str, ...]:
    checks = (
        ("enabled", target.enabled is True),
        # v1 has no scheme field: an unknown protocol is never a proven match.
        ("protocol", target.protocol == protocol),
        ("port", target.port == port),
        ("path", (target.path or "/") == (path or "/")),
    )
    return tuple(name for name, ok in checks if not ok)


def http_post_target_matches(
    config: IpcHttpPostConfig,
    *,
    host: str,
    port: int,
    path: str,
    protocol: str,
) -> HttpPostTargetMatch:
    """Compare a parsed config against the receiver we expect. Pure; no network.

    Host and protocol compare case-insensitively; an empty path means ``/``.
    The slot for our host with the fewest differences is reported.
    """
    host_n, protocol_n = host.strip().lower(), protocol.strip().lower()
    if not host_n:
        raise ValueError("host is required")
    if protocol_n not in {"http", "https"}:
        raise ValueError("protocol must be 'http' or 'https'")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be 1-65535")
    third_party = tuple(t for t in config.enabled_targets if t.host.strip() and t.host.strip().lower() != host_n)
    ranked = sorted(
        (
            (_differences(t, port=port, path=path, protocol=protocol_n), index, t)
            for index, t in enumerate(config.targets)
            if t.host.strip().lower() == host_n
        ),
        key=lambda entry: (len(entry[0]), entry[1]),
    )
    if ranked:
        diffs, _index, target = ranked[0]
        status = HttpPostMatchStatus.DIFFERS if diffs else HttpPostMatchStatus.MATCH
        return HttpPostTargetMatch(status, target, diffs, third_party)
    status = HttpPostMatchStatus.THIRD_PARTY if third_party else HttpPostMatchStatus.NOT_CONFIGURED
    return HttpPostTargetMatch(status, None, (), third_party)
