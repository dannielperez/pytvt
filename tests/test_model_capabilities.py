"""Tests for pytvt.device_sdk.model_capabilities — the TVT camera capability catalog."""

from __future__ import annotations

import pytest

from pytvt.device_sdk.constants import SmartEventType
from pytvt.device_sdk.model_capabilities import (
    _TVT_TO_ENS,
    FEATURE_SMART_EVENTS,
    MODEL_CAPABILITIES,
    CameraFeature,
    features_for_model,
    is_known_model,
    model_supports,
    models_supporting,
    normalize_model,
    smart_events_for_model,
)

# ── Catalog integrity ───────────────────────────────────────────────


def test_catalog_covers_the_matrix_models() -> None:
    # 15 camera models are listed in the "Functionality list overview" matrix.
    assert len(MODEL_CAPABILITIES) == 15


def test_every_model_has_the_basic_events() -> None:
    # Every model in the matrix supports at least Exception + Motion Detection.
    for model, feats in MODEL_CAPABILITIES.items():
        assert CameraFeature.EXCEPTION in feats, model
        assert CameraFeature.MOTION_DETECTION in feats, model


def test_feature_smart_events_covers_every_feature() -> None:
    # The taxonomy must map every CameraFeature (empty tuple for basic ones).
    for feature in CameraFeature:
        assert feature in FEATURE_SMART_EVENTS, feature


def test_feature_smart_events_reference_real_codes() -> None:
    for events in FEATURE_SMART_EVENTS.values():
        for event in events:
            assert isinstance(event, SmartEventType)


def test_tvt_alias_targets_are_valid_ens_keys() -> None:
    for ens in _TVT_TO_ENS.values():
        assert ens in MODEL_CAPABILITIES


# ── Model normalization ─────────────────────────────────────────────


def test_normalize_accepts_ens_model() -> None:
    assert normalize_model("IP-5IRPC4A30") == "IP-5IRPC4A30"


def test_normalize_accepts_tvt_model() -> None:
    assert normalize_model("TD-9742A3-PC") == "IP-5IRPC4A30"


def test_normalize_strips_variant_suffix() -> None:
    # SDK Channel.model often carries a hardware-variant suffix.
    assert normalize_model("TD-9544S4-C(D/PE/AW2)") == "IP-5IRD4S4C4-28"


def test_normalize_is_case_insensitive() -> None:
    assert normalize_model("td-9742a3-pc") == "IP-5IRPC4A30"


def test_normalize_unknown_returns_none() -> None:
    assert normalize_model("TD-0000-XX") is None
    assert normalize_model("") is None


def test_is_known_model() -> None:
    assert is_known_model("IP-5IRPC4A30")
    assert is_known_model("TD-9742A3-PC")
    assert not is_known_model("bogus")


# ── Feature lookups ─────────────────────────────────────────────────


def test_people_counting_camera_supports_people_counting_only() -> None:
    # The IP-5IRPC4A30 is the dedicated people-counting camera in the fleet.
    feats = features_for_model("IP-5IRPC4A30")
    assert CameraFeature.PEOPLE_COUNTING in feats
    assert CameraFeature.HEAT_MAP not in feats
    assert CameraFeature.PLATE_RECOGNITION not in feats


def test_lpr_camera_supports_plate_recognition() -> None:
    feats = features_for_model("IP-5IR4A3H4-MZ-LR")
    assert CameraFeature.PLATE_DETECTION in feats
    assert CameraFeature.PLATE_RECOGNITION in feats
    assert CameraFeature.PEOPLE_COUNTING not in feats


def test_full_ai_camera_supports_perimeter_and_face() -> None:
    feats = features_for_model("IP-5VP5E3B1-28")
    for expected in (
        CameraFeature.LINE_CROSSING,
        CameraFeature.INTRUSION,
        CameraFeature.HEAT_MAP,
        CameraFeature.FACE_DETECTION,
        CameraFeature.METADATA,
    ):
        assert expected in feats


def test_sd_camera_supports_secondary_development() -> None:
    feats = features_for_model("IP-5IRD4A3BH4-28-SD")
    assert CameraFeature.SECONDARY_DEVELOPMENT in feats


def test_features_for_unknown_model_is_empty() -> None:
    assert features_for_model("nope") == frozenset()


def test_model_supports_accepts_both_vocabularies() -> None:
    assert model_supports("IP-5IRPC4A30", CameraFeature.PEOPLE_COUNTING)
    assert model_supports("TD-9742A3-PC", CameraFeature.PEOPLE_COUNTING)
    assert not model_supports("IP-5IRPC4A30", CameraFeature.HEAT_MAP)


# ── Smart-event derivation ──────────────────────────────────────────


def test_smart_events_for_people_counting_camera() -> None:
    events = smart_events_for_model("IP-5IRPC4A30")
    assert SmartEventType.CPC in events
    assert SmartEventType.TRAFFIC in events
    # It has no perimeter analytics.
    assert SmartEventType.LOITER not in events


def test_smart_events_for_unknown_model_is_empty() -> None:
    assert smart_events_for_model("nope") == frozenset()


# ── Reverse lookup ──────────────────────────────────────────────────


def test_models_supporting_people_counting() -> None:
    models = models_supporting(CameraFeature.PEOPLE_COUNTING)
    assert models == frozenset({"IP-5IRPC4A30"})


def test_models_supporting_heat_map_is_the_full_ai_set() -> None:
    models = models_supporting(CameraFeature.HEAT_MAP)
    # Heat map rides with the full-AI perimeter cameras, not the basic/LPR ones.
    assert "IP-5VP5E3B1-28" in models
    assert "IP-5IRPC4A30" not in models
    assert "IP-5IR4A3H4-MZ-LR" not in models
