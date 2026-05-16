"""Workflow-specific exceptions."""

from __future__ import annotations

from pytvt.management.exceptions import ManagementError


class WorkflowError(ManagementError):
    """Base class for workflow-level failures.

    Distinct from the per-device :class:`pytvt.management.ManagementError`
    hierarchy in that workflow errors represent the orchestration failing
    to complete, not an individual backend call.
    """


class WorkflowPrecheckError(WorkflowError):
    """Raised before any side-effect when inputs are invalid or unsafe.

    Example: requested ``new_password`` equals ``old_password``; target
    subnet overlaps source; required credentials missing from environment.
    """
