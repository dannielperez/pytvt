"""Typed models for TVT management-server operations.

This module defines data shapes for management-server workflows. Field names are
intentionally generic until protocol captures and SDK validation provide stable
vendor mappings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ServerInfo:
    """Basic management-server identity and runtime metadata."""

    host: str
    port: int
    model: str = ""
    firmware: str = ""
    serial_number: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    # TODO: extend with SDK-validated fields after symbol mapping is confirmed
    # (e.g. device_count, uptime, license_info)


# Spec-aligned alias
ManagementServerInfo = ServerInfo


@dataclass
class ManagedDevice:
    """A device enrolled in the management server."""

    device_id: str
    name: str = ""
    ip_address: str = ""
    status: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    # TODO: add model, firmware, channel_count once SDK enumeration is validated


@dataclass
class ManagedChannel:
    """A camera/media channel mapped under a managed device."""

    channel_id: str
    device_id: str
    name: str = ""
    status: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceStatus:
    """Operational status snapshot for a managed device."""

    device_id: str
    online: bool | None = None
    last_seen_at: datetime | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    # TODO: add recording_state, alarm_state once status payload is validated


@dataclass
class AlarmSubscription:
    """Handle for an active alarm subscription stream/session."""

    handle: str
    transport: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AlarmEvent:
    """A normalized alarm event record from a management-server stream."""

    event_type: str
    event_id: str = ""
    device_id: str = ""
    channel_id: str = ""
    occurred_at: datetime | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)
