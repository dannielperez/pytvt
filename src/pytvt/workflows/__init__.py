"""High-level workflows for technician-facing operations.

This subpackage packages multi-step orchestrations we've validated in the
field — subnet migrations, credential rotations, site-to-site validation —
into explicit, composable building blocks that consumers such as UniqueOS
can expose in a GUI or CLI without having to re-derive the sequencing and
recovery logic.

Design principles:
    * **Idempotent.** Running a workflow twice on a finished system is safe.
    * **Observable.** All state transitions flow through a :class:`ProgressSink`
      so UI layers can stream to technicians in real time.
    * **Structured results.** Every workflow returns a frozen dataclass with
      ``success``, ``steps`` (audit trail) and ``.to_dict()`` for JSON export.
    * **No prints from the library.** All user-facing output goes through the
      progress sink.

Stability: **Provisional** — subject to refinement as more sites are migrated.
See ``docs/PUBLIC_SURFACE.md`` for the promotion contract.
"""

from __future__ import annotations

from .exceptions import WorkflowError, WorkflowPrecheckError
from .password_rotate import (
    ChannelRotationResult,
    PasswordRotateResult,
    rotate_nvr_channel_passwords,
)
from .progress import ConsoleProgressSink, NullProgressSink, ProgressEvent, ProgressSink
from .site_subnet_change import (
    CameraReaddressPlan,
    CameraReaddressResult,
    SiteSubnetChangeResult,
    change_site_subnet_via_nvr,
)
from .validate import (
    ChannelHealth,
    SiteComparisonResult,
    SiteValidationResult,
    compare_sites,
    validate_site,
)

__all__ = [
    "CameraReaddressPlan",
    "CameraReaddressResult",
    "ChannelHealth",
    "ChannelRotationResult",
    "ConsoleProgressSink",
    "NullProgressSink",
    "PasswordRotateResult",
    "ProgressEvent",
    "ProgressSink",
    "SiteComparisonResult",
    "SiteSubnetChangeResult",
    "SiteValidationResult",
    "WorkflowError",
    "WorkflowPrecheckError",
    "change_site_subnet_via_nvr",
    "compare_sites",
    "rotate_nvr_channel_passwords",
    "validate_site",
]
