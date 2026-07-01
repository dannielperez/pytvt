"""Post-migration site validation — NVR-authoritative health checks.

Consolidates the read-only diagnostics we run after a subnet change or
password rotation. Answers the single question a technician needs:
**"Is this site fully healthy and on the expected configuration?"**

Authoritative signal
--------------------
Channel online/offline state **as reported by the NVR itself** is the
ground truth for credential sync. The NVR polls each IPC over the
native TVT protocol on port 9008; if it reports online, the stored
credential matches the camera's native admin password. ONVIF probes
are *not* used here — TVT cameras maintain a separate ONVIF user
database that can drift independently from the native TVT admin user,
so an ONVIF 401 is noise for this workflow.

Checks performed
----------------
1. **Channel online status.** Every registered channel must be online.
2. **Subnet conformance.** If ``expected_subnet`` is given, every
   registered channel's IP must fall inside it.
3. **Channel count.** If ``expected_channel_count`` is given, the
   observed count must match exactly.
4. **NVR host conformance.** If ``expected_nvr_subnet`` is given, the
   NVR host itself must fall inside it.
"""

from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass, field

from pytvt.xml_api import NvrClient

from .exceptions import WorkflowError
from .progress import NullProgressSink, ProgressEvent, ProgressSink


@dataclass(frozen=True)
class ChannelHealth:
    """Per-channel snapshot for the validation report."""

    chl_num: int
    dev_id: str
    ip: str
    online: bool
    in_expected_subnet: bool  # True when no expected_subnet was supplied.

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SiteValidationResult:
    """Aggregate outcome of a single-NVR validation pass.

    Attributes:
        nvr_host: NVR IP/hostname that was checked.
        expected_subnet: Subnet the cameras should be on, or ``None``
            to skip that check.
        expected_channel_count: Required channel count, or ``None``.
        expected_nvr_subnet: Subnet the NVR itself should be on, or
            ``None``.
        channels_total: Registered channels on the NVR.
        channels_online: Subset that report online.
        channels_in_subnet: Subset whose IP falls in
            ``expected_subnet`` (equals ``channels_total`` when no
            subnet was supplied).
        channels: Per-channel detail.
        issues: Human-readable findings for any failed check. Empty
            when ``ok`` is true.
        error: Orchestration-level failure message, empty otherwise.
    """

    nvr_host: str
    expected_subnet: str | None = None
    expected_channel_count: int | None = None
    expected_nvr_subnet: str | None = None
    channels_total: int = 0
    channels_online: int = 0
    channels_in_subnet: int = 0
    channels: list[ChannelHealth] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when every declared expectation is satisfied."""
        return not self.error and not self.issues

    # Backwards-compat alias so CLI/consumer callers can use the same
    # name (``success``) as other workflow results.
    @property
    def success(self) -> bool:
        return self.ok

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d


@dataclass(frozen=True)
class SiteComparisonResult:
    """Compare a candidate site's validation against a reference baseline.

    Used by the ``pytvt workflow validate ... --compare <baseline.json>``
    mode to highlight configuration drift versus a known-good peer
    site.
    """

    baseline_host: str
    candidate_host: str
    channel_count_delta: int  # candidate - baseline
    offline_delta: int
    baseline_ok: bool
    candidate_ok: bool
    differences: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.baseline_ok
            and self.candidate_ok
            and self.channel_count_delta == 0
            and self.offline_delta == 0
            and not self.differences
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d


def _ip_in_net(ip: str, net: ipaddress.IPv4Network | None) -> bool:
    if net is None:
        return True
    try:
        return ipaddress.ip_address(ip) in net
    except ValueError:
        return False


def validate_site(
    client: NvrClient,
    *,
    expected_subnet: str | None = None,
    expected_channel_count: int | None = None,
    expected_nvr_subnet: str | None = None,
    progress: ProgressSink | None = None,
) -> SiteValidationResult:
    """Run the authoritative health checks against a logged-in NVR.

    *client* must already be logged in; this is a strictly read-only
    operation.

    Args:
        client: Authenticated :class:`~pytvt.NvrClient`.
        expected_subnet: Camera subnet in CIDR. When set, every
            channel whose IP is outside it is flagged.
        expected_channel_count: Required registered-channel count.
            When set, a mismatch is flagged.
        expected_nvr_subnet: CIDR the NVR host itself should be in.
            Flags a mismatch.
        progress: Optional :class:`ProgressSink` for streaming events.

    Returns:
        :class:`SiteValidationResult`. Never raises for per-channel
        findings; orchestration failures (login missing, query error)
        are returned via ``error``.
    """
    sink: ProgressSink = progress or NullProgressSink()
    host = getattr(client, "host", "?")

    # Parse expected subnets once.
    try:
        cam_net = ipaddress.ip_network(expected_subnet, strict=False) if expected_subnet else None
        nvr_net = ipaddress.ip_network(expected_nvr_subnet, strict=False) if expected_nvr_subnet else None
    except ValueError as exc:
        return SiteValidationResult(
            nvr_host=host,
            expected_subnet=expected_subnet,
            expected_channel_count=expected_channel_count,
            expected_nvr_subnet=expected_nvr_subnet,
            error=f"invalid expected subnet: {exc}",
        )

    sink.emit(
        ProgressEvent(
            level="info",
            code="validate.start",
            message=f"Validating {host}",
            context={
                "expected_subnet": expected_subnet,
                "expected_channel_count": expected_channel_count,
                "expected_nvr_subnet": expected_nvr_subnet,
            },
        )
    )

    try:
        raw_channels = client.query_channels()
    except Exception as exc:
        return SiteValidationResult(
            nvr_host=host,
            expected_subnet=expected_subnet,
            expected_channel_count=expected_channel_count,
            expected_nvr_subnet=expected_nvr_subnet,
            error=f"query_channels failed: {exc}",
        )

    channels: list[ChannelHealth] = []
    online_count = 0
    in_subnet_count = 0
    for ch in raw_channels:
        ip = (ch.ip or "").strip()
        in_subnet = _ip_in_net(ip, cam_net)
        if ch.online:
            online_count += 1
        if in_subnet:
            in_subnet_count += 1
        channels.append(
            ChannelHealth(
                chl_num=ch.chl_num,
                dev_id=ch.dev_id,
                ip=ip,
                online=bool(ch.online),
                in_expected_subnet=in_subnet,
            )
        )

    issues: list[str] = []

    # Check 1 — everyone online.
    offline = [c for c in channels if not c.online]
    if offline:
        ids = ", ".join(f"ch{c.chl_num}" for c in offline)
        issues.append(f"{len(offline)}/{len(channels)} channel(s) offline: {ids}")

    # Check 2 — subnet conformance.
    if cam_net is not None:
        stray = [c for c in channels if not c.in_expected_subnet]
        if stray:
            ids = ", ".join(f"ch{c.chl_num}({c.ip})" for c in stray)
            issues.append(f"{len(stray)} channel(s) outside expected {cam_net}: {ids}")

    # Check 3 — channel count.
    if expected_channel_count is not None and len(channels) != expected_channel_count:
        issues.append(f"channel count mismatch: got {len(channels)}, expected {expected_channel_count}")

    # Check 4 — NVR host subnet.
    if nvr_net is not None and not _ip_in_net(host, nvr_net):
        issues.append(f"NVR host {host} outside expected {nvr_net}")

    result = SiteValidationResult(
        nvr_host=host,
        expected_subnet=expected_subnet,
        expected_channel_count=expected_channel_count,
        expected_nvr_subnet=expected_nvr_subnet,
        channels_total=len(channels),
        channels_online=online_count,
        channels_in_subnet=in_subnet_count,
        channels=channels,
        issues=issues,
    )

    if result.ok:
        sink.emit(
            ProgressEvent(
                "success",
                "validate.done",
                f"{host}: OK ({len(channels)} channel(s), all online)",
            )
        )
    else:
        for issue in issues:
            sink.emit(ProgressEvent("warning", "validate.issue", issue))
        sink.emit(
            ProgressEvent(
                "warning",
                "validate.done",
                f"{host}: {len(issues)} issue(s)",
            )
        )
    return result


def compare_sites(
    baseline: SiteValidationResult,
    candidate: SiteValidationResult,
) -> SiteComparisonResult:
    """Compare two validation results side-by-side.

    Useful after rolling out a change to one site to confirm its
    health profile matches a reference peer. Does *not* require the
    sites to share subnets or hostnames — only structural parity
    (channel count, online count).

    Raises :class:`WorkflowError` if either input carries an
    orchestration error (both must have completed their queries).
    """
    if baseline.error:
        raise WorkflowError(f"baseline {baseline.nvr_host}: {baseline.error}")
    if candidate.error:
        raise WorkflowError(f"candidate {candidate.nvr_host}: {candidate.error}")

    differences: list[str] = []

    if baseline.channels_total != candidate.channels_total:
        differences.append(f"channel count: baseline={baseline.channels_total} candidate={candidate.channels_total}")

    baseline_offline = baseline.channels_total - baseline.channels_online
    candidate_offline = candidate.channels_total - candidate.channels_online
    if baseline_offline != candidate_offline:
        differences.append(f"offline channels: baseline={baseline_offline} candidate={candidate_offline}")

    return SiteComparisonResult(
        baseline_host=baseline.nvr_host,
        candidate_host=candidate.nvr_host,
        channel_count_delta=candidate.channels_total - baseline.channels_total,
        offline_delta=candidate_offline - baseline_offline,
        baseline_ok=baseline.ok,
        candidate_ok=candidate.ok,
        differences=differences,
    )
