"""Parser for the TVT/NVMS **Status Log** export (FT-1).

The NVMS *Status Log* is a tab-separated export with the columns
``No.  Type  Record Time  Node Name  Details``. Every row is a *connectivity
transition* of one of four entity layers in the containment tree
``platform_server → recorder → channel`` (plus stand-alone decoders). The feed
is dominated by camera-channel churn (~98% of rows) and the ``Details`` column
is empty in practice — there is **no vendor severity**.

This module owns only the **vendor-string parsing** half of the SDK boundary
(CLAUDE.md §4):

* the fixed 8-term vocabulary ``{Monitor | Encoding device | Decoder | Server}``
  by ``{online | offline}`` -> a structural ``(entity_layer, transition)`` pair;
* the Node-Name grammar — splitting ``<recorder>_<channel>`` on the **last**
  underscore (NR-3), where the recorder may itself contain underscores, and
  detecting the ``<ip>_<port>`` endpoint-keyed recorder form.

Everything downstream — the canonical severity/health taxonomy, debounce/flap
suppression (NR-1), parent-child correlation (NR-2), retention and operator
surfacing — is the **consumer's** job and deliberately lives outside pytvt. The
parser assigns *no* severity and reads none from the row (NR-5); it is a lossy,
best-effort signal source (NR-4: the text Node Name is not a stable identity
key — the stable key is sourced from the NVMS API/DB by the consumer).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime

# ── Vocabulary ───────────────────────────────────────────────────────
#
# The export carries exactly these 8 event types. Each maps to a structural
# (entity_layer, transition) — the ONLY classification pytvt performs. Severity,
# health and action are assigned by the consumer's ontology, never here (NR-5).

CHANNEL = "channel"
RECORDER = "recorder"
DECODER = "decoder"
PLATFORM_SERVER = "platform_server"

ONLINE = "online"
OFFLINE = "offline"

#: vendor ``Type`` string → ``(entity_layer, transition)``.
EVENT_VOCAB: dict[str, tuple[str, str]] = {
    "Monitor online": (CHANNEL, ONLINE),
    "Monitor offline": (CHANNEL, OFFLINE),
    "Encoding device online": (RECORDER, ONLINE),
    "Encoding device offline": (RECORDER, OFFLINE),
    "Decoder online": (DECODER, ONLINE),
    "Decoder offline": (DECODER, OFFLINE),
    "Server online": (PLATFORM_SERVER, ONLINE),
    "Server offline": (PLATFORM_SERVER, OFFLINE),
}

# ``Record Time`` format observed in every export row, e.g. ``2020-01-01 00:00:00``.
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# An ``<ipv4>_<port>`` recorder identity (NR-3), e.g. ``192.168.1.50_8000``.
# Octets/port are range-checked in :func:`_extract_endpoint` to avoid matching a
# friendly name that merely looks numeric.
_IP_PORT_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})_(\d{1,5})$")

# Sentinel Node Name emitted by a retired channel that is still reporting; it is
# not a real identity (README: fired 3,631 times in the source export).
DELETED_NODE_SENTINEL = "The node has been deleted"


def classify_event_type(type_str: str) -> tuple[str, str] | None:
    """Return ``(entity_layer, transition)`` for a vendor ``Type`` string.

    Returns ``None`` for an unrecognized type (firmware drift / a new vendor
    string) so the caller can log it rather than silently coercing it.
    """
    return EVENT_VOCAB.get(type_str.strip())


def _extract_endpoint(token: str) -> str | None:
    """Return ``"ip:port"`` if ``token`` is an ``<ipv4>_<port>`` identity, else None."""
    match = _IP_PORT_RE.match(token)
    if not match:
        return None
    *octets, port = match.groups()
    if any(int(o) > 255 for o in octets):
        return None
    if not 0 < int(port) <= 65535:
        return None
    return f"{'.'.join(octets)}:{port}"


@dataclass(frozen=True)
class NodeIdentity:
    """Structural decomposition of a Node Name (NR-3/NR-4).

    Not a stable key — the consumer must resolve the real
    ``(integration_account, recorder_guid, channel_guid)`` from the NVMS API.
    """

    raw: str
    #: Recorder identity: for a channel this is the part before the last ``_``;
    #: for a recorder/decoder/server it is the whole Node Name. ``None`` only for
    #: a channel Node Name that carries no underscore at all.
    parent: str | None
    #: The channel segment (after the last ``_``); ``None`` for non-channel rows.
    channel_label: str | None
    #: ``"ip:port"`` when the recorder is endpoint-keyed rather than friendly-named.
    endpoint: str | None
    #: True when the Node Name is the retired-channel sentinel.
    is_deleted_sentinel: bool


def parse_node_name(raw: str, entity_layer: str | None) -> NodeIdentity:
    """Decompose a Node Name given the row's entity layer.

    * ``channel`` rows are ``<recorder>_<channel>`` — split on the **last**
      underscore (the recorder may contain underscores); the recorder half is
      probed for the ``<ip>_<port>`` endpoint form.
    * recorder / decoder / platform_server rows name the entity directly — no
      split; the whole name is probed for the endpoint form.
    """
    name = raw.strip()
    is_sentinel = name == DELETED_NODE_SENTINEL

    if entity_layer == CHANNEL and not is_sentinel:
        parent, sep, channel = name.rpartition("_")
        if not sep:  # channel Node Name with no underscore — nothing to split
            return NodeIdentity(raw, None, name or None, None, is_sentinel)
        return NodeIdentity(raw, parent, channel, _extract_endpoint(parent), is_sentinel)

    # Non-channel rows (recorder/decoder/server) or the sentinel: the name IS the
    # entity — no channel split.
    endpoint = None if is_sentinel else _extract_endpoint(name)
    return NodeIdentity(raw, name or None, None, endpoint, is_sentinel)


@dataclass(frozen=True)
class StatusLogEvent:
    """One parsed Status Log row.

    ``entity_layer``/``transition`` are ``None`` for an unrecognized ``Type``;
    ``record_time`` is ``None`` when the timestamp could not be parsed (the raw
    string is preserved in ``raw_time``). Severity/health are intentionally
    absent — those are assigned by the consumer's ontology (NR-5).
    """

    seq: int | None
    event_type: str
    entity_layer: str | None
    transition: str | None
    record_time: datetime | None
    raw_time: str
    node: NodeIdentity
    details: str

    @property
    def is_recognized(self) -> bool:
        """True when ``Type`` matched the known vocabulary."""
        return self.entity_layer is not None


_HEADER_FIRST_CELL = "no."


def _iter_lines(source: str | Iterable[str]) -> Iterator[str]:
    if isinstance(source, str):
        yield from source.splitlines()
    else:
        # Tolerate a streamed file object whose lines retain their terminators,
        # so the trailing newline never leaks into the Details cell.
        for line in source:
            yield line.rstrip("\r\n")


def _parse_time(raw_time: str) -> datetime | None:
    try:
        return datetime.strptime(raw_time.strip(), _TIME_FORMAT)
    except ValueError:
        return None


def parse_status_log(source: str | Iterable[str]) -> Iterator[StatusLogEvent]:
    """Parse a Status Log export into :class:`StatusLogEvent` records.

    ``source`` is the raw TSV text or an iterable of lines. Parsing is lenient:
    the header row and blank lines are skipped, rows are split on tabs, a missing
    trailing ``Details`` cell is tolerated, an unparseable timestamp yields
    ``record_time=None`` (raw kept in ``raw_time``), and an unknown ``Type``
    yields ``entity_layer=None`` rather than being dropped — so a firmware-drift
    string surfaces to the caller instead of vanishing.

    Rows with fewer than the ``Type``/``Node Name`` cells needed to be meaningful
    are skipped.
    """
    for line in _iter_lines(source):
        if not line.strip():
            continue
        cells = line.split("\t")
        # Header row: first cell is "No." (case-insensitive).
        if cells[0].strip().lower() == _HEADER_FIRST_CELL:
            continue
        if len(cells) < 4:  # need at least No./Type/Time/Node
            continue

        raw_seq, event_type, raw_time, node_name = (c.strip() for c in cells[:4])
        details = cells[4] if len(cells) > 4 else ""

        try:
            seq: int | None = int(raw_seq)
        except ValueError:
            seq = None

        classified = classify_event_type(event_type)
        layer, transition = classified if classified else (None, None)

        yield StatusLogEvent(
            seq=seq,
            event_type=event_type,
            entity_layer=layer,
            transition=transition,
            record_time=_parse_time(raw_time),
            raw_time=raw_time,
            node=parse_node_name(node_name, layer),
            details=details,
        )


__all__ = [
    "CHANNEL",
    "DECODER",
    "DELETED_NODE_SENTINEL",
    "EVENT_VOCAB",
    "OFFLINE",
    "ONLINE",
    "PLATFORM_SERVER",
    "RECORDER",
    "NodeIdentity",
    "StatusLogEvent",
    "classify_event_type",
    "parse_node_name",
    "parse_status_log",
]
