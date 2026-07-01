"""Tests for the NVMS Status Log parser (:mod:`pytvt.status_log`)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from pytvt.status_log import (
    CHANNEL,
    DECODER,
    DELETED_NODE_SENTINEL,
    EVENT_VOCAB,
    OFFLINE,
    ONLINE,
    PLATFORM_SERVER,
    RECORDER,
    classify_event_type,
    parse_node_name,
    parse_status_log,
)

FIXTURE = Path(__file__).parent / "fixtures" / "status_log" / "small.tsv"


# ── Vocabulary ───────────────────────────────────────────────────────


def test_vocab_covers_exactly_eight_types():
    assert len(EVENT_VOCAB) == 8


@pytest.mark.parametrize(
    ("type_str", "layer", "transition"),
    [
        ("Monitor online", CHANNEL, ONLINE),
        ("Monitor offline", CHANNEL, OFFLINE),
        ("Encoding device online", RECORDER, ONLINE),
        ("Encoding device offline", RECORDER, OFFLINE),
        ("Decoder online", DECODER, ONLINE),
        ("Decoder offline", DECODER, OFFLINE),
        ("Server online", PLATFORM_SERVER, ONLINE),
        ("Server offline", PLATFORM_SERVER, OFFLINE),
    ],
)
def test_classify_known_types(type_str, layer, transition):
    assert classify_event_type(type_str) == (layer, transition)


def test_classify_tolerates_surrounding_whitespace():
    assert classify_event_type("  Monitor offline ") == (CHANNEL, OFFLINE)


def test_classify_unknown_type_returns_none():
    assert classify_event_type("Camera tamper") is None


# ── Node-name grammar (NR-3 / NR-4) ──────────────────────────────────


def test_channel_splits_on_last_underscore():
    node = parse_node_name("NVR-0015_IP Camera", CHANNEL)
    assert node.parent == "NVR-0015"
    assert node.channel_label == "IP Camera"
    assert node.endpoint is None
    assert not node.is_deleted_sentinel


def test_channel_parent_may_itself_contain_underscores():
    # Split must be on the LAST underscore, not the first.
    node = parse_node_name("Site_A_NVR 2_Cam 3", CHANNEL)
    assert node.parent == "Site_A_NVR 2"
    assert node.channel_label == "Cam 3"


def test_channel_ip_port_parent_is_detected():
    node = parse_node_name("192.168.1.50_8000_Cam 3", CHANNEL)
    assert node.parent == "192.168.1.50_8000"
    assert node.channel_label == "Cam 3"
    assert node.endpoint == "192.168.1.50:8000"


def test_channel_without_underscore_keeps_label_and_null_parent():
    node = parse_node_name("LobbyCam", CHANNEL)
    assert node.parent is None
    assert node.channel_label == "LobbyCam"


def test_recorder_name_is_not_split():
    node = parse_node_name("NVR-0001", RECORDER)
    assert node.parent == "NVR-0001"
    assert node.channel_label is None
    assert node.endpoint is None


def test_recorder_keyed_by_ip_port_exposes_endpoint():
    node = parse_node_name("10.0.0.9_8000", RECORDER)
    assert node.parent == "10.0.0.9_8000"
    assert node.channel_label is None
    assert node.endpoint == "10.0.0.9:8000"


def test_server_name_is_not_split_even_with_spaces():
    node = parse_node_name("Management Server", PLATFORM_SERVER)
    assert node.parent == "Management Server"
    assert node.channel_label is None


def test_deleted_sentinel_flagged_and_not_split():
    node = parse_node_name(DELETED_NODE_SENTINEL, CHANNEL)
    assert node.is_deleted_sentinel
    assert node.channel_label is None
    assert node.parent == DELETED_NODE_SENTINEL


def test_endpoint_detection_rejects_out_of_range_octet_and_port():
    # 999 is not a valid octet; 70000 is not a valid port — must NOT be read as an endpoint.
    assert parse_node_name("999.1.1.1_8000", RECORDER).endpoint is None
    assert parse_node_name("10.0.0.1_70000", RECORDER).endpoint is None
    # A friendly name that merely contains dots/digits is not an endpoint.
    assert parse_node_name("NVR-1.2_ok", RECORDER).endpoint is None


# ── Streaming parse ──────────────────────────────────────────────────


def test_parse_skips_header_and_blank_lines():
    text = "No.\tType\tRecord Time\tNode Name\tDetails\n\n"
    assert list(parse_status_log(text)) == []


def test_parse_row_populates_all_fields():
    text = "1\tMonitor offline\t2020-01-01 00:00:02\tNVR-0015_IP Camera\t"
    (event,) = list(parse_status_log(text))
    assert event.seq == 1
    assert event.event_type == "Monitor offline"
    assert event.entity_layer == CHANNEL
    assert event.transition == OFFLINE
    assert event.record_time == datetime(2020, 1, 1, 0, 0, 2)
    assert event.raw_time == "2020-01-01 00:00:02"
    assert event.node.parent == "NVR-0015"
    assert event.node.channel_label == "IP Camera"
    assert event.details == ""
    assert event.is_recognized


def test_parse_unknown_type_is_kept_unrecognized_not_dropped():
    text = "7\tCamera tamper\t2020-01-01 00:00:00\tNVR-1_Cam\t"
    (event,) = list(parse_status_log(text))
    assert event.entity_layer is None
    assert event.transition is None
    assert not event.is_recognized
    # node parsing falls back to the non-channel path when layer is unknown
    assert event.node.parent == "NVR-1_Cam"


def test_parse_tolerates_unparseable_timestamp():
    text = "1\tServer online\tnot-a-time\tManagement Server\t"
    (event,) = list(parse_status_log(text))
    assert event.record_time is None
    assert event.raw_time == "not-a-time"


def test_parse_tolerates_missing_details_cell():
    # No trailing tab / Details column at all.
    text = "1\tDecoder offline\t2020-01-01 22:41:13\tDEC-15"
    (event,) = list(parse_status_log(text))
    assert event.details == ""
    assert event.entity_layer == DECODER


def test_parse_skips_short_rows():
    text = "1\tMonitor online\n2\tMonitor online\t2020-01-01 00:00:00\tNVR-1_Cam\t"
    events = list(parse_status_log(text))
    assert len(events) == 1
    assert events[0].seq == 2


def test_parse_accepts_iterable_of_lines():
    lines = [
        "No.\tType\tRecord Time\tNode Name\tDetails",
        "1\tServer offline\t2020-01-01 13:33:21\tManagement Server\t",
    ]
    (event,) = list(parse_status_log(lines))
    assert event.entity_layer == PLATFORM_SERVER
    assert event.transition == OFFLINE


def test_parse_streamed_lines_do_not_leak_newline_into_details():
    # A file object yields lines WITH their terminators; Details must stay clean.
    lines = [
        "1\tMonitor offline\t2020-01-01 00:00:00\tNVR-1_Cam\t\n",
        "2\tMonitor online\t2020-01-01 00:00:10\tNVR-1_Cam\t\r\n",
    ]
    events = list(parse_status_log(iter(lines)))
    assert len(events) == 2
    assert all(e.details == "" for e in events)
    assert all(e.node.channel_label == "Cam" for e in events)


def test_non_int_sequence_is_none():
    text = "x\tMonitor online\t2020-01-01 00:00:00\tNVR-1_Cam\t"
    (event,) = list(parse_status_log(text))
    assert event.seq is None


# ── Real-data smoke over the committed (anonymized) fixture ───────────


def test_small_fixture_parses_cleanly():
    events = list(parse_status_log(FIXTURE.read_text()))
    # 25 data rows (header excluded).
    assert len(events) == 25
    # Every row in this anonymized export is a recognized connectivity transition.
    assert all(e.is_recognized for e in events)
    assert all(e.record_time is not None for e in events)
    # Details is empty in 100% of rows (NR-5: no vendor severity).
    assert all(e.details.strip() == "" for e in events)


def test_small_fixture_layer_and_transition_distribution():
    events = list(parse_status_log(FIXTURE.read_text()))
    layers = [e.entity_layer for e in events]
    # Every layer is represented (one of each event type appears in the fixture).
    assert set(layers) == {CHANNEL, RECORDER, DECODER, PLATFORM_SERVER}
    # Channel churn dominates even the tiny fixture.
    assert layers.count(CHANNEL) >= 15
    # Recorders/decoders/servers name the entity directly — never a channel split.
    for e in events:
        if e.entity_layer != CHANNEL:
            assert e.node.channel_label is None


def test_small_fixture_channel_nodes_split_to_recorder_parent():
    events = list(parse_status_log(FIXTURE.read_text()))
    channels = [e for e in events if e.entity_layer == CHANNEL]
    parents = {e.node.parent for e in channels}
    assert "NVR-0015" in parents
    assert "NVR-0219" in parents
    # The camera label is the trailing segment.
    labels = {e.node.channel_label for e in channels}
    assert "IP Camera" in labels
    assert "Camera 6" in labels
