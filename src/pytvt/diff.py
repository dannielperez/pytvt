"""Scan diffing — compare two pytvt JSON scan result files.

Provides offline change detection between scan runs: which devices were
added/removed, which changed metadata or camera channels, and which
failures appeared or disappeared.

Identity strategy:
    Devices are matched by ``nvr_ip`` — always present, unique per scan run,
    and the natural key operators use to identify an NVR.  MAC address is
    displayed when available but not used for matching (it can be empty for
    head-variant or discovery-only results).

    Cameras within a device are matched by ``channel`` index (int or str).
    If channel is missing/empty, cameras are compared positionally.

Public API:
    :func:`load_scan_file` — read and validate a JSON scan result file
    :func:`diff_scans` — compare two scan result lists
    :class:`ScanDiff` — top-level diff result
    :class:`DeviceDiff` — per-device change record
    :class:`CameraDiff` — per-camera change record
    :func:`format_diff_text` — human-readable console output
    :func:`format_diff_json` — machine-readable JSON output
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import CameraInfo, ScanResult

# ── Diff models ──────────────────────────────────────────────────────


@dataclass
class FieldChange:
    """A single field that changed between old and new."""

    field: str
    old: str | int | bool | None
    new: str | int | bool | None

    def __str__(self) -> str:
        return f"{self.field}: {self.old!r} → {self.new!r}"


@dataclass
class CameraDiff:
    """Change record for a single camera channel."""

    channel: int | str
    name: str = ""
    status: str = "unchanged"  # "added", "removed", "changed", "unchanged"
    changes: list[FieldChange] = field(default_factory=list)


@dataclass
class DeviceDiff:
    """Change record for a single NVR between two scans."""

    nvr_ip: str
    site: str = ""
    hostname: str = ""
    status: str = "unchanged"  # "added", "removed", "changed", "unchanged"
    field_changes: list[FieldChange] = field(default_factory=list)
    cameras_added: list[CameraDiff] = field(default_factory=list)
    cameras_removed: list[CameraDiff] = field(default_factory=list)
    cameras_changed: list[CameraDiff] = field(default_factory=list)
    camera_count_old: int = 0
    camera_count_new: int = 0

    @property
    def has_camera_changes(self) -> bool:
        return bool(self.cameras_added or self.cameras_removed or self.cameras_changed)


@dataclass
class ScanDiff:
    """Top-level result of comparing two scan runs."""

    old_file: str = ""
    new_file: str = ""
    old_device_count: int = 0
    new_device_count: int = 0
    devices_added: list[DeviceDiff] = field(default_factory=list)
    devices_removed: list[DeviceDiff] = field(default_factory=list)
    devices_changed: list[DeviceDiff] = field(default_factory=list)
    unchanged_count: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.devices_added or self.devices_removed or self.devices_changed)

    def to_dict(self) -> dict:
        return asdict(self)


# ── File loading ─────────────────────────────────────────────────────


def load_scan_file(path: str | Path) -> list[ScanResult]:
    """Read a pytvt JSON scan result file and return typed models.

    Raises ``ValueError`` for missing files, invalid JSON, or unexpected
    structure.  Tolerates unknown keys in dicts (forward-compatible).
    """
    p = Path(path)

    if not p.exists():
        raise ValueError(f"File not found: {p}")

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {p}: {exc}") from exc

    if not isinstance(raw, list):
        raise ValueError(f"Expected a JSON array in {p}, got {type(raw).__name__}")

    results: list[ScanResult] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry {i} in {p} is not an object")
        results.append(_scan_result_from_dict(entry))

    return results


def _scan_result_from_dict(d: dict) -> ScanResult:
    """Construct a ScanResult from a raw JSON dict, tolerating extra keys."""
    cameras = []
    for cam_dict in d.get("cameras", []):
        if isinstance(cam_dict, dict):
            cameras.append(CameraInfo.from_dict(cam_dict))

    return ScanResult(
        site=d.get("site", ""),
        hostname=d.get("hostname", ""),
        nvr_ip=d.get("nvr_ip", ""),
        nvr_mac=d.get("nvr_mac", ""),
        nvr_port=int(d.get("nvr_port", 0)),
        success=bool(d.get("success", False)),
        device_name=d.get("device_name", ""),
        device_model=d.get("device_model", ""),
        serial_number=d.get("serial_number", ""),
        firmware=d.get("firmware", ""),
        total_channels=int(d.get("total_channels", 0)),
        cameras=cameras,
        error=d.get("error"),
        backend=d.get("backend", ""),
    )


# ── Core diffing logic ───────────────────────────────────────────────

# Device fields compared for changes (order = display order)
_DEVICE_COMPARE_FIELDS = [
    "success",
    "device_name",
    "device_model",
    "serial_number",
    "firmware",
    "total_channels",
    "backend",
    "error",
]

# Camera fields compared for changes
_CAMERA_COMPARE_FIELDS = ["name", "address", "port", "status", "protocol", "model"]


def diff_scans(
    old: list[ScanResult],
    new: list[ScanResult],
    *,
    old_file: str = "",
    new_file: str = "",
) -> ScanDiff:
    """Compare two scan result lists and return a structured diff.

    Devices are matched by ``nvr_ip``.  Cameras within a device are
    matched by ``channel`` index.
    """
    old_by_ip = {r.nvr_ip: r for r in old}
    new_by_ip = {r.nvr_ip: r for r in new}

    old_ips = set(old_by_ip)
    new_ips = set(new_by_ip)

    added_ips = sorted(new_ips - old_ips)
    removed_ips = sorted(old_ips - new_ips)
    common_ips = sorted(old_ips & new_ips)

    devices_added = [_make_added_device(new_by_ip[ip]) for ip in added_ips]
    devices_removed = [_make_removed_device(old_by_ip[ip]) for ip in removed_ips]

    devices_changed: list[DeviceDiff] = []
    unchanged_count = 0

    for ip in common_ips:
        dd = _diff_device(old_by_ip[ip], new_by_ip[ip])
        if dd.status == "changed":
            devices_changed.append(dd)
        else:
            unchanged_count += 1

    return ScanDiff(
        old_file=old_file,
        new_file=new_file,
        old_device_count=len(old),
        new_device_count=len(new),
        devices_added=devices_added,
        devices_removed=devices_removed,
        devices_changed=devices_changed,
        unchanged_count=unchanged_count,
    )


def _make_added_device(r: ScanResult) -> DeviceDiff:
    return DeviceDiff(
        nvr_ip=r.nvr_ip,
        site=r.site,
        hostname=r.hostname,
        status="added",
        camera_count_new=r.camera_count,
    )


def _make_removed_device(r: ScanResult) -> DeviceDiff:
    return DeviceDiff(
        nvr_ip=r.nvr_ip,
        site=r.site,
        hostname=r.hostname,
        status="removed",
        camera_count_old=r.camera_count,
    )


def _diff_device(old: ScanResult, new: ScanResult) -> DeviceDiff:
    """Compare two scans of the same device (matched by nvr_ip)."""
    field_changes: list[FieldChange] = []

    for fname in _DEVICE_COMPARE_FIELDS:
        old_val = getattr(old, fname)
        new_val = getattr(new, fname)
        if old_val != new_val:
            field_changes.append(FieldChange(field=fname, old=old_val, new=new_val))

    cams_added, cams_removed, cams_changed = _diff_cameras(old.cameras, new.cameras)

    has_changes = bool(field_changes or cams_added or cams_removed or cams_changed)

    return DeviceDiff(
        nvr_ip=old.nvr_ip,
        site=new.site or old.site,
        hostname=new.hostname or old.hostname,
        status="changed" if has_changes else "unchanged",
        field_changes=field_changes,
        cameras_added=cams_added,
        cameras_removed=cams_removed,
        cameras_changed=cams_changed,
        camera_count_old=old.camera_count,
        camera_count_new=new.camera_count,
    )


def _diff_cameras(
    old_cams: list[CameraInfo],
    new_cams: list[CameraInfo],
) -> tuple[list[CameraDiff], list[CameraDiff], list[CameraDiff]]:
    """Compare camera channel lists, matching by channel index."""
    old_by_ch = _cameras_by_channel(old_cams)
    new_by_ch = _cameras_by_channel(new_cams)

    old_keys = set(old_by_ch)
    new_keys = set(new_by_ch)

    added = [
        CameraDiff(channel=ch, name=new_by_ch[ch].name, status="added")
        for ch in sorted(new_keys - old_keys, key=_sort_channel)
    ]
    removed = [
        CameraDiff(channel=ch, name=old_by_ch[ch].name, status="removed")
        for ch in sorted(old_keys - new_keys, key=_sort_channel)
    ]

    changed: list[CameraDiff] = []
    for ch in sorted(old_keys & new_keys, key=_sort_channel):
        changes = _compare_camera_fields(old_by_ch[ch], new_by_ch[ch])
        if changes:
            changed.append(
                CameraDiff(
                    channel=ch,
                    name=new_by_ch[ch].name or old_by_ch[ch].name,
                    status="changed",
                    changes=changes,
                )
            )

    return added, removed, changed


def _cameras_by_channel(cameras: list[CameraInfo]) -> dict[int | str, CameraInfo]:
    """Index cameras by channel, filtering to those with addresses."""
    result: dict[int | str, CameraInfo] = {}
    for cam in cameras:
        if cam.has_address:
            key = cam.channel if cam.channel != "" else id(cam)
            result[key] = cam
    return result


def _compare_camera_fields(old: CameraInfo, new: CameraInfo) -> list[FieldChange]:
    changes: list[FieldChange] = []
    for fname in _CAMERA_COMPARE_FIELDS:
        old_val = getattr(old, fname)
        new_val = getattr(new, fname)
        if str(old_val) != str(new_val):
            changes.append(FieldChange(field=fname, old=old_val, new=new_val))
    return changes


def _sort_channel(ch: int | str) -> tuple[int, str]:
    """Sort key: ints first numerically, then strings alphabetically."""
    if isinstance(ch, int):
        return (0, str(ch).zfill(10))
    return (1, str(ch))


# ── Output formatting: human-readable ────────────────────────────────


def format_diff_text(diff: ScanDiff) -> str:
    """Format a ScanDiff as human-readable console text."""
    lines: list[str] = []
    w = 80

    lines.append("=" * w)
    lines.append("  SCAN DIFF REPORT")
    lines.append("=" * w)

    if diff.old_file or diff.new_file:
        lines.append(f"  Old: {diff.old_file}")
        lines.append(f"  New: {diff.new_file}")

    lines.append(f"  Devices: {diff.old_device_count} → {diff.new_device_count}")

    # Summary counts
    summary_parts = []
    if diff.devices_added:
        summary_parts.append(f"{len(diff.devices_added)} added")
    if diff.devices_removed:
        summary_parts.append(f"{len(diff.devices_removed)} removed")
    if diff.devices_changed:
        summary_parts.append(f"{len(diff.devices_changed)} changed")
    if diff.unchanged_count:
        summary_parts.append(f"{diff.unchanged_count} unchanged")

    lines.append(f"  Changes: {', '.join(summary_parts) if summary_parts else 'none'}")
    lines.append("=" * w)

    if not diff.has_changes:
        lines.append("  No changes detected.")
        return "\n".join(lines)

    # Added devices
    if diff.devices_added:
        lines.append("")
        lines.append(f"  ++ ADDED DEVICES ({len(diff.devices_added)})")
        lines.append(f"  {'-' * 76}")
        for dd in diff.devices_added:
            label = _device_label(dd)
            lines.append(f"  + {label} ({dd.camera_count_new} cameras)")

    # Removed devices
    if diff.devices_removed:
        lines.append("")
        lines.append(f"  -- REMOVED DEVICES ({len(diff.devices_removed)})")
        lines.append(f"  {'-' * 76}")
        for dd in diff.devices_removed:
            label = _device_label(dd)
            lines.append(f"  - {label} ({dd.camera_count_old} cameras)")

    # Changed devices
    if diff.devices_changed:
        lines.append("")
        lines.append(f"  ~~ CHANGED DEVICES ({len(diff.devices_changed)})")
        lines.append(f"  {'-' * 76}")
        for dd in diff.devices_changed:
            label = _device_label(dd)
            lines.append(f"  ~ {label}")

            for fc in dd.field_changes:
                lines.append(f"      {fc}")

            if dd.camera_count_old != dd.camera_count_new:
                lines.append(f"      cameras: {dd.camera_count_old} → {dd.camera_count_new}")

            for cam in dd.cameras_added:
                lines.append(f"      + ch {cam.channel}: {cam.name}")
            for cam in dd.cameras_removed:
                lines.append(f"      - ch {cam.channel}: {cam.name}")
            for cam in dd.cameras_changed:
                changes_str = "; ".join(str(c) for c in cam.changes)
                lines.append(f"      ~ ch {cam.channel} ({cam.name}): {changes_str}")

    return "\n".join(lines)


def _device_label(dd: DeviceDiff) -> str:
    """Build a human-readable label for a device in diff output."""
    parts = [dd.nvr_ip]
    if dd.hostname:
        parts.append(dd.hostname)
    if dd.site:
        parts.append(f"@ {dd.site}")
    return " / ".join(parts)


# ── Output formatting: JSON ──────────────────────────────────────────


def format_diff_json(diff: ScanDiff) -> str:
    """Format a ScanDiff as indented JSON."""
    return json.dumps(diff.to_dict(), indent=2, default=str)


# ── Convenience: summary-only ────────────────────────────────────────


def format_diff_summary(diff: ScanDiff) -> str:
    """One-line summary of changes."""
    parts = []
    parts.append(f"devices: {diff.old_device_count} → {diff.new_device_count}")
    if diff.devices_added:
        parts.append(f"+{len(diff.devices_added)} added")
    if diff.devices_removed:
        parts.append(f"-{len(diff.devices_removed)} removed")
    if diff.devices_changed:
        parts.append(f"~{len(diff.devices_changed)} changed")
    parts.append(f"{diff.unchanged_count} unchanged")
    return ", ".join(parts)
