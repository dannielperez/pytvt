"""Progress reporting primitives for long-running workflows.

Consumers (e.g. downstream application GUI) implement :class:`ProgressSink` to receive
real-time step updates. The library never calls ``print`` directly — all
user-facing output flows through a sink.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProgressEvent:
    """A single progress notification emitted by a workflow.

    Attributes:
        level: One of ``"info"``, ``"success"``, ``"warning"``, ``"error"``.
        code: Short stable identifier (e.g. ``"channel.rotated"``) suitable
            for i18n lookup or UI icon mapping.
        message: Human-readable English summary (fallback when no i18n).
        context: Arbitrary structured metadata (IPs, channel numbers,
            step counts) that the UI can render.
    """

    level: str
    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


class ProgressSink(Protocol):
    """Interface that downstream application (or any caller) implements to observe progress."""

    def emit(self, event: ProgressEvent) -> None:
        """Handle a progress event. Must not raise."""
        ...


class NullProgressSink:
    """Discards all events. Default for library consumers that don't care."""

    def emit(self, event: ProgressEvent) -> None:  # noqa: D401 — protocol impl
        return None


class ConsoleProgressSink:
    """Prints to stderr. Intended for CLI use, never for library defaults."""

    _LEVEL_PREFIX = {
        "info": "  ",
        "success": "  ✓ ",
        "warning": "  ⚠ ",
        "error": "  ✗ ",
    }

    def __init__(self, stream: Any = None, verbose: bool = True) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._verbose = verbose

    def emit(self, event: ProgressEvent) -> None:
        if not self._verbose and event.level == "info":
            return
        prefix = self._LEVEL_PREFIX.get(event.level, "  ")
        self._stream.write(f"{prefix}{event.message}\n")
        self._stream.flush()
