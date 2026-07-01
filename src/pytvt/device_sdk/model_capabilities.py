"""TVT camera model → supported analytics feature catalog.

Source of truth: the TVT "Functionality list overview" capability matrix
shipped in the vendor training package (2026-06). It records, per camera
model, which analytics features the model supports, and this module maps each
of those product-facing features to the :class:`SmartEventType` event code(s)
the device emits for it.

This is vendor product knowledge, so it lives here in the SDK next to
``SmartEventType`` rather than in Django app code — the app consumes the
catalog through this boundary instead of hard-coding a model→feature table.

Two vocabularies are involved:

* **ENS model** — the Unique Security part number, e.g. ``IP-5IRPC4A30``.
* **TVT model** — the manufacturer part number that flows through the SDK
  (see :class:`pytvt.models.Channel.model`), e.g. ``TD-9742A3-PC``, sometimes
  with a hardware-variant suffix like ``TD-9544S4-C(D/PE/AW2)``.

:func:`normalize_model` accepts either form (with or without the suffix) and
resolves it to the canonical ENS key; the lookup helpers accept either form.

Note on the feature→event mapping: the SDK's ``SmartEventType`` codes do not
cleanly separate every product feature (e.g. "line crossing" and "intrusion"
both surface through the shared perimeter/tripwire codes), so
:data:`FEATURE_SMART_EVENTS` records the closest code(s) for correlation and
may be empty for basic (non-analytics) features.
"""

from __future__ import annotations

from enum import Enum

from .constants import SmartEventType


class CameraFeature(str, Enum):
    """Analytics features from the TVT capability matrix (product-facing names)."""

    EXCEPTION = "exception"  # basic exception events (video loss, etc.)
    MOTION_DETECTION = "motion_detection"
    LINE_CROSSING = "line_crossing"
    INTRUSION = "intrusion"
    REGION_ENTRANCE = "region_entrance"
    REGION_EXITING = "region_exiting"
    LOITERING = "loitering"
    ILLEGAL_PARKING = "illegal_parking"
    ABANDONED_OBJECT = "abandoned_object"
    MISSING_OBJECT = "missing_object"
    TARGET_COUNTING = "target_counting"  # cross-line target count
    HEAT_MAP = "heat_map"
    FACE_DETECTION = "face_detection"
    FACE_CAPTURE = "face_capture"
    METADATA = "metadata"  # human / vehicle / non-vehicle structured data
    PLATE_DETECTION = "plate_detection"
    PLATE_RECOGNITION = "plate_recognition"
    PEOPLE_COUNTING = "people_counting"
    SECONDARY_DEVELOPMENT = "secondary_development"  # AIOTP custom AI models


# Best-effort mapping from a product feature to the SmartEventType code(s) a
# device emits for it. Empty tuple = basic (non-smart-analytics) feature.
FEATURE_SMART_EVENTS: dict[CameraFeature, tuple[SmartEventType, ...]] = {
    CameraFeature.EXCEPTION: (),
    CameraFeature.MOTION_DETECTION: (),
    CameraFeature.LINE_CROSSING: (SmartEventType.TRIPWIRE, SmartEventType.PEA_FOR_IPC),
    CameraFeature.INTRUSION: (SmartEventType.PEA_FOR_IPC, SmartEventType.PEA_TARGET),
    CameraFeature.REGION_ENTRANCE: (SmartEventType.AOI_ENTRY, SmartEventType.NVR_AOI_ENTRY),
    CameraFeature.REGION_EXITING: (SmartEventType.AOI_LEAVE, SmartEventType.NVR_AOI_LEAVE),
    CameraFeature.LOITERING: (SmartEventType.LOITER,),
    CameraFeature.ILLEGAL_PARKING: (SmartEventType.PVD,),
    CameraFeature.ABANDONED_OBJECT: (SmartEventType.OSC,),
    CameraFeature.MISSING_OBJECT: (SmartEventType.OSC,),
    CameraFeature.TARGET_COUNTING: (SmartEventType.PASSLINE, SmartEventType.TRAFFIC),
    CameraFeature.HEAT_MAP: (SmartEventType.HEATMAP,),
    CameraFeature.FACE_DETECTION: (SmartEventType.VFD,),
    CameraFeature.FACE_CAPTURE: (SmartEventType.VFD,),
    CameraFeature.METADATA: (SmartEventType.VSD,),
    CameraFeature.PLATE_DETECTION: (SmartEventType.VEHICLE, SmartEventType.NVR_VEHICLE),
    CameraFeature.PLATE_RECOGNITION: (SmartEventType.VEHICLE, SmartEventType.NVR_VEHICLE),
    CameraFeature.PEOPLE_COUNTING: (SmartEventType.CPC, SmartEventType.TRAFFIC),
    CameraFeature.SECONDARY_DEVELOPMENT: (),
}


# Per-model supported features, keyed by ENS model. Transcribed from the TVT
# "Functionality list overview" matrix; the trailing comment is the base TVT
# model. Keep this table sorted the same way as the source matrix.
MODEL_CAPABILITIES: dict[str, frozenset[CameraFeature]] = {
    "IP-5IR4A3H4-MZ-LR": frozenset(
        {  # TD-9443A3BH-A-LR
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.PLATE_DETECTION,
            CameraFeature.PLATE_RECOGNITION,
        }
    ),
    "IP-5IR4A3B4-MZ-LR": frozenset(
        {  # TD-9443A3BH-LR
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.PLATE_DETECTION,
            CameraFeature.PLATE_RECOGNITION,
        }
    ),
    "IP-5IR8E3B3/MZ": frozenset(
        {  # TD-9483E3B
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
            CameraFeature.REGION_ENTRANCE,
            CameraFeature.REGION_EXITING,
            CameraFeature.LOITERING,
            CameraFeature.ILLEGAL_PARKING,
            CameraFeature.ABANDONED_OBJECT,
            CameraFeature.MISSING_OBJECT,
            CameraFeature.TARGET_COUNTING,
            CameraFeature.HEAT_MAP,
            CameraFeature.FACE_DETECTION,
            CameraFeature.FACE_CAPTURE,
            CameraFeature.METADATA,
        }
    ),
    "IP-5IRD4A3BH4-28-SD": frozenset(
        {  # TD-9544A3BH-SD
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.SECONDARY_DEVELOPMENT,
        }
    ),
    "IP-5IRD4E3BA4-28": frozenset(
        {  # TD-9544E3B-A
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
            CameraFeature.REGION_ENTRANCE,
            CameraFeature.REGION_EXITING,
            CameraFeature.LOITERING,
            CameraFeature.ILLEGAL_PARKING,
            CameraFeature.ABANDONED_OBJECT,
            CameraFeature.MISSING_OBJECT,
            CameraFeature.TARGET_COUNTING,
            CameraFeature.HEAT_MAP,
            CameraFeature.FACE_DETECTION,
            CameraFeature.FACE_CAPTURE,
            CameraFeature.METADATA,
        }
    ),
    "IP-5IRD4S34-28": frozenset(
        {  # TD-9544S3
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
        }
    ),
    "IP-5IRD4S44-28": frozenset(
        {  # TD-9544S4
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
            CameraFeature.ABANDONED_OBJECT,
            CameraFeature.MISSING_OBJECT,
        }
    ),
    "IP-5IRD4S4C4-28": frozenset(
        {  # TD-9544S4-C
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
            CameraFeature.ABANDONED_OBJECT,
            CameraFeature.MISSING_OBJECT,
        }
    ),
    "IP-5IRD4C25-MZ-PA": frozenset(
        {  # TD-9545C2-PA
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
            CameraFeature.REGION_ENTRANCE,
            CameraFeature.REGION_EXITING,
            CameraFeature.LOITERING,
            CameraFeature.ILLEGAL_PARKING,
            CameraFeature.ABANDONED_OBJECT,
            CameraFeature.MISSING_OBJECT,
            CameraFeature.TARGET_COUNTING,
            CameraFeature.HEAT_MAP,
            CameraFeature.FACE_DETECTION,
            CameraFeature.FACE_CAPTURE,
            CameraFeature.METADATA,
        }
    ),
    "IP-5IRD4C25-28-PA": frozenset(
        {  # TD-9545C2-PA
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
            CameraFeature.REGION_ENTRANCE,
            CameraFeature.REGION_EXITING,
            CameraFeature.LOITERING,
            CameraFeature.ILLEGAL_PARKING,
            CameraFeature.ABANDONED_OBJECT,
            CameraFeature.MISSING_OBJECT,
            CameraFeature.TARGET_COUNTING,
            CameraFeature.HEAT_MAP,
            CameraFeature.FACE_DETECTION,
            CameraFeature.FACE_CAPTURE,
            CameraFeature.METADATA,
        }
    ),
    "IP-5IRD4C4A5-28": frozenset(
        {  # TD-9545C4-A
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
            CameraFeature.REGION_ENTRANCE,
            CameraFeature.REGION_EXITING,
            CameraFeature.ABANDONED_OBJECT,
            CameraFeature.MISSING_OBJECT,
            CameraFeature.TARGET_COUNTING,
            CameraFeature.FACE_DETECTION,
            CameraFeature.FACE_CAPTURE,
        }
    ),
    "IP-5IRD4E3BA5-MZ": frozenset(
        {  # TD-9545E3B-A
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
            CameraFeature.REGION_ENTRANCE,
            CameraFeature.REGION_EXITING,
            CameraFeature.LOITERING,
            CameraFeature.ILLEGAL_PARKING,
            CameraFeature.ABANDONED_OBJECT,
            CameraFeature.MISSING_OBJECT,
            CameraFeature.TARGET_COUNTING,
            CameraFeature.HEAT_MAP,
            CameraFeature.FACE_DETECTION,
            CameraFeature.FACE_CAPTURE,
            CameraFeature.METADATA,
        }
    ),
    "IP-5VP5E3B1-28": frozenset(
        {  # TD-9551E3B
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
            CameraFeature.REGION_ENTRANCE,
            CameraFeature.REGION_EXITING,
            CameraFeature.LOITERING,
            CameraFeature.ILLEGAL_PARKING,
            CameraFeature.ABANDONED_OBJECT,
            CameraFeature.MISSING_OBJECT,
            CameraFeature.TARGET_COUNTING,
            CameraFeature.HEAT_MAP,
            CameraFeature.FACE_DETECTION,
            CameraFeature.FACE_CAPTURE,
            CameraFeature.METADATA,
        }
    ),
    "IP-5IRD8S4C4-28": frozenset(
        {  # TD-9584S4-C
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.LINE_CROSSING,
            CameraFeature.INTRUSION,
            CameraFeature.ABANDONED_OBJECT,
            CameraFeature.MISSING_OBJECT,
        }
    ),
    "IP-5IRPC4A30": frozenset(
        {  # TD-9742A3-PC
            CameraFeature.EXCEPTION,
            CameraFeature.MOTION_DETECTION,
            CameraFeature.PEOPLE_COUNTING,
        }
    ),
}


# Base TVT model → ENS model. Built from MODEL_CAPABILITIES comments; when two
# ENS variants share a base TVT model they carry identical feature sets, so a
# single representative ENS is fine here.
_TVT_TO_ENS: dict[str, str] = {
    "TD-9443A3BH-A-LR": "IP-5IR4A3H4-MZ-LR",
    "TD-9443A3BH-LR": "IP-5IR4A3B4-MZ-LR",
    "TD-9483E3B": "IP-5IR8E3B3/MZ",
    "TD-9544A3BH-SD": "IP-5IRD4A3BH4-28-SD",
    "TD-9544E3B-A": "IP-5IRD4E3BA4-28",
    "TD-9544S3": "IP-5IRD4S34-28",
    "TD-9544S4": "IP-5IRD4S44-28",
    "TD-9544S4-C": "IP-5IRD4S4C4-28",
    "TD-9545C2-PA": "IP-5IRD4C25-28-PA",
    "TD-9545C4-A": "IP-5IRD4C4A5-28",
    "TD-9545E3B-A": "IP-5IRD4E3BA5-MZ",
    "TD-9551E3B": "IP-5VP5E3B1-28",
    "TD-9584S4-C": "IP-5IRD8S4C4-28",
    "TD-9742A3-PC": "IP-5IRPC4A30",
}


def _strip_variant_suffix(model: str) -> str:
    """Drop a trailing hardware-variant suffix, e.g. ``TD-9544S4-C(D/PE/AW2)``."""
    head, _, _ = model.partition("(")
    return head.strip()


def normalize_model(model: str) -> str | None:
    """Resolve an ENS or TVT model string to its canonical ENS key.

    Accepts either vocabulary, with or without a variant suffix. Returns the
    canonical ENS key present in :data:`MODEL_CAPABILITIES`, or ``None`` if the
    model is not in the catalog.
    """
    if not model:
        return None
    candidate = _strip_variant_suffix(model).upper()
    if candidate in MODEL_CAPABILITIES:
        return candidate
    return _TVT_TO_ENS.get(candidate)


def is_known_model(model: str) -> bool:
    """Return ``True`` if the model (ENS or TVT) is present in the catalog."""
    return normalize_model(model) is not None


def features_for_model(model: str) -> frozenset[CameraFeature]:
    """Return the supported features for a model, or an empty set if unknown."""
    key = normalize_model(model)
    if key is None:
        return frozenset()
    return MODEL_CAPABILITIES[key]


def model_supports(model: str, feature: CameraFeature) -> bool:
    """Return ``True`` if the model supports ``feature``."""
    return feature in features_for_model(model)


def smart_events_for_model(model: str) -> frozenset[SmartEventType]:
    """Return the SmartEventType codes a model can emit, across its features."""
    events: set[SmartEventType] = set()
    for feature in features_for_model(model):
        events.update(FEATURE_SMART_EVENTS.get(feature, ()))
    return frozenset(events)


def models_supporting(feature: CameraFeature) -> frozenset[str]:
    """Return the ENS model keys that support ``feature``."""
    return frozenset(m for m, feats in MODEL_CAPABILITIES.items() if feature in feats)
