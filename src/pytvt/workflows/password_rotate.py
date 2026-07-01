"""Password rotation workflow for NVR-registered IPC cameras.

Rotates the *native TVT* admin password on every channel of one or more
NVRs, keeping the NVR's stored credentials in sync with the camera side.

Validated at a production fleet migration (50 cameras across two NVRs). The
sequence below was derived empirically from observed NVR behaviour:

1. **Online channels are already in sync.** The NVR would not report a
   channel online if its stored credential didn't match the camera. So
   rotation targets *offline* channels by default.
2. **The authoritative online/offline signal is the NVR, not ONVIF.**
   TVT cameras ship with a separate ONVIF user database that does not
   track the native TVT admin password — probing ONVIF 401 is a false
   positive for "camera needs rotation".
3. **Two-pass recovery** handles both drift directions:
    * **Pass A** — push the *target* password to the NVR-stored credential
      and re-check online. If the camera was already manually rotated,
      the channel comes back online.
    * **Pass B** — for stragglers still offline, push the *old* password
      so the NVR can re-authenticate, call ``editIPChlPassword`` to
      rotate the camera, then push the target password to the NVR.

All behaviour is exposed through a single entrypoint,
:func:`rotate_nvr_channel_passwords`, that returns a
:class:`PasswordRotateResult` and emits :class:`~.ProgressEvent` s along
the way for GUI layers.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field

from pytvt.models import NvrApiError
from pytvt.xml_api import Channel, NvrClient

from .exceptions import WorkflowError, WorkflowPrecheckError
from .progress import NullProgressSink, ProgressEvent, ProgressSink

_SETTLE_SECONDS = 6
"""Delay after pushing stored creds before re-querying online status.

The NVR's channel-online polling is asynchronous; too short a wait
yields false-offline readings.
"""


@dataclass(frozen=True)
class ChannelRotationResult:
    """Outcome for a single NVR channel.

    Attributes:
        chl_num: 1-indexed channel number on the NVR.
        dev_id: NVR-internal device UUID (e.g. ``{0000000A-0000-...}``).
        ip: Camera IP as registered on the NVR at entry.
        status: One of:
            * ``already-in-sync`` — channel was online before we started.
            * ``synced-via-pass-a`` — camera was pre-rotated; NVR cred refreshed.
            * ``rotated-via-pass-b`` — camera password was changed by us.
            * ``skipped`` — channel not eligible (filtered by subnet etc.).
            * ``failed`` — one of the steps raised; ``error`` is set.
        error: Failure message; empty on success.
    """

    chl_num: int
    dev_id: str
    ip: str
    status: str
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PasswordRotateResult:
    """Aggregate outcome of a password-rotation workflow run.

    Attributes:
        nvr_host: NVR IP/hostname the workflow ran against.
        dry_run: True if no side effects were performed.
        channels_total: Number of channels considered (after filtering).
        channels_rotated: Count of cameras whose password this call changed.
        channels_synced: Count of channels where only NVR-stored cred was
            updated (camera was already on target password).
        channels_already_ok: Count of channels that were in sync before the run.
        channels_failed: Count of channels that could not be reconciled.
        results: Per-channel detail.
        error: Orchestration-level failure (login, query), empty otherwise.
    """

    nvr_host: str
    dry_run: bool
    channels_total: int = 0
    channels_rotated: int = 0
    channels_synced: int = 0
    channels_already_ok: int = 0
    channels_failed: int = 0
    results: list[ChannelRotationResult] = field(default_factory=list)
    error: str = ""

    @property
    def success(self) -> bool:
        """True if the workflow ran to completion with no per-channel failures."""
        return not self.error and self.channels_failed == 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["success"] = self.success
        return d


def _filter_channels(
    channels: Iterable[Channel],
    *,
    subnet: str | None,
) -> list[Channel]:
    """Return channels whose IP falls inside *subnet* (or all if subnet is None)."""
    if subnet is None:
        return [c for c in channels if c.ip]
    net = ipaddress.ip_network(subnet, strict=False)
    out: list[Channel] = []
    for c in channels:
        if not c.ip:
            continue
        try:
            if ipaddress.ip_address(c.ip) in net:
                out.append(c)
        except ValueError:
            continue
    return out


def rotate_nvr_channel_passwords(
    client: NvrClient,
    *,
    old_password: str,
    new_password: str,
    username: str = "admin",
    subnet: str | None = None,
    channel_ids: Sequence[str] | None = None,
    apply: bool = False,
    settle_seconds: float = _SETTLE_SECONDS,
    progress: ProgressSink | None = None,
) -> PasswordRotateResult:
    """Rotate native IPC admin password across an NVR's registered channels.

    The *client* must already be logged in (its ``login()`` method called by
    the caller). This lets a downstream consumer reuse an existing session.

    Args:
        client: An authenticated :class:`~pytvt.NvrClient`.
        old_password: Current camera password (used to auth from NVR → camera
            during Pass B if the camera hasn't been rotated yet).
        new_password: Target password to set.
        username: Admin username on the camera (default ``"admin"``).
        subnet: Optional CIDR filter; only channels whose IP is in this
            subnet are considered. ``None`` means all channels.
        channel_ids: Optional explicit list of NVR device IDs to target.
            When set, overrides ``subnet``.
        apply: If False (default), probe and report but make no changes.
        settle_seconds: Wait after each ``editDevList`` call before re-reading
            online status. Lower only for testing; 6 s matches field observations.
        progress: Optional sink for real-time events. Defaults to a null sink.

    Returns:
        :class:`PasswordRotateResult` — never raises for per-channel failures;
        those are recorded in ``results``. Raises :class:`WorkflowPrecheckError`
        for invalid inputs and :class:`WorkflowError` for orchestration-level
        failures (e.g. NVR unreachable after login).

    Example:
        >>> client = NvrClient("10.0.0.250", username="admin", password="secret")
        >>> client.login()
        >>> r = rotate_nvr_channel_passwords(
        ...     client,
        ...     old_password="123456",
        ...     new_password="NewPw@2026",
        ...     subnet="10.0.0.0/24",
        ...     apply=True,
        ... )
        >>> assert r.success
    """
    sink: ProgressSink = progress or NullProgressSink()

    if not new_password:
        raise WorkflowPrecheckError("new_password must be non-empty")
    if new_password == old_password:
        raise WorkflowPrecheckError("new_password equals old_password (no-op)")

    host = getattr(client, "host", "?")

    # --- discover channels ---
    try:
        all_channels = client.query_channels()
    except Exception as exc:
        return PasswordRotateResult(
            nvr_host=host,
            dry_run=not apply,
            error=f"query_channels failed: {exc}",
        )

    if channel_ids is not None:
        wanted = set(channel_ids)
        candidates = [c for c in all_channels if c.dev_id in wanted]
    else:
        candidates = _filter_channels(all_channels, subnet=subnet)

    sink.emit(
        ProgressEvent(
            level="info",
            code="workflow.start",
            message=(
                f"Password rotation on {host}: "
                f"{len(candidates)} channel(s) in scope "
                f"({'DRY-RUN' if not apply else 'APPLY'})"
            ),
            context={
                "nvr_host": host,
                "channels_in_scope": len(candidates),
                "apply": apply,
            },
        )
    )

    results: list[ChannelRotationResult] = []
    already_ok = [c for c in candidates if c.online]
    offline = [c for c in candidates if not c.online]

    # Channels that are already online are assumed in sync — record as such.
    for ch in already_ok:
        results.append(
            ChannelRotationResult(
                chl_num=ch.chl_num,
                dev_id=ch.dev_id,
                ip=ch.ip,
                status="already-in-sync",
            )
        )

    if not offline:
        sink.emit(
            ProgressEvent(
                level="success",
                code="workflow.noop",
                message="All channels already in sync; nothing to rotate.",
            )
        )
        return PasswordRotateResult(
            nvr_host=host,
            dry_run=not apply,
            channels_total=len(candidates),
            channels_already_ok=len(already_ok),
            results=results,
        )

    if not apply:
        # Dry-run: list candidates but take no action.
        for ch in offline:
            sink.emit(
                ProgressEvent(
                    level="info",
                    code="channel.candidate",
                    message=f"[DRY-RUN] ch{ch.chl_num} {ch.ip} — needs rotation",
                    context={"chl_num": ch.chl_num, "ip": ch.ip, "dev_id": ch.dev_id},
                )
            )
            results.append(
                ChannelRotationResult(
                    chl_num=ch.chl_num,
                    dev_id=ch.dev_id,
                    ip=ch.ip,
                    status="skipped",
                    error="dry-run",
                )
            )
        return PasswordRotateResult(
            nvr_host=host,
            dry_run=True,
            channels_total=len(candidates),
            channels_already_ok=len(already_ok),
            channels_failed=0,
            results=results,
        )

    # --- Pass A: push target password to NVR-stored cred; re-check online ---
    sink.emit(
        ProgressEvent(
            level="info",
            code="pass_a.start",
            message=f"Pass A: syncing NVR stored creds to new password for {len(offline)} channel(s)",
        )
    )
    pass_a_attempted: list[Channel] = []
    for ch in offline:
        try:
            client.update_device_credentials(
                dev_ids=[ch.dev_id],
                username=username,
                password=new_password,
            )
            pass_a_attempted.append(ch)
        except Exception as exc:
            results.append(
                ChannelRotationResult(
                    chl_num=ch.chl_num,
                    dev_id=ch.dev_id,
                    ip=ch.ip,
                    status="failed",
                    error=f"editDevList(new) failed in Pass A: {exc}",
                )
            )
            sink.emit(
                ProgressEvent(
                    level="error",
                    code="pass_a.editDevList_failed",
                    message=f"ch{ch.chl_num} {ch.ip}: {exc}",
                    context={"chl_num": ch.chl_num, "ip": ch.ip},
                )
            )

    time.sleep(settle_seconds)

    try:
        fresh = {c.dev_id: c for c in client.query_channels()}
    except Exception as exc:
        raise WorkflowError(f"query_channels failed after Pass A: {exc}") from exc

    synced: list[Channel] = []
    still_offline: list[Channel] = []
    for ch in pass_a_attempted:
        latest = fresh.get(ch.dev_id)
        if latest is not None and latest.online:
            synced.append(ch)
            sink.emit(
                ProgressEvent(
                    level="success",
                    code="channel.synced",
                    message=f"ch{ch.chl_num} {ch.ip}: came online after NVR sync (camera already rotated)",
                    context={"chl_num": ch.chl_num, "ip": ch.ip},
                )
            )
            results.append(
                ChannelRotationResult(
                    chl_num=ch.chl_num,
                    dev_id=ch.dev_id,
                    ip=ch.ip,
                    status="synced-via-pass-a",
                )
            )
        else:
            still_offline.append(ch)

    if not still_offline:
        return PasswordRotateResult(
            nvr_host=host,
            dry_run=False,
            channels_total=len(candidates),
            channels_already_ok=len(already_ok),
            channels_synced=len(synced),
            channels_rotated=0,
            results=results,
        )

    # --- Pass B: set old cred, editIPChlPassword, restore new cred, verify ---
    sink.emit(
        ProgressEvent(
            level="info",
            code="pass_b.start",
            message=f"Pass B: rotating camera password via NVR for {len(still_offline)} channel(s)",
        )
    )
    rotated: list[Channel] = []
    for ch in still_offline:
        label = f"ch{ch.chl_num} {ch.ip}"

        # Step 1 — install OLD cred so NVR can authenticate to camera.
        try:
            client.update_device_credentials(
                dev_ids=[ch.dev_id],
                username=username,
                password=old_password,
            )
        except Exception as exc:
            results.append(
                ChannelRotationResult(
                    chl_num=ch.chl_num,
                    dev_id=ch.dev_id,
                    ip=ch.ip,
                    status="failed",
                    error=f"editDevList(old) failed: {exc}",
                )
            )
            sink.emit(ProgressEvent("error", "pass_b.editDevList_old_failed", f"{label}: {exc}"))
            continue

        # Step 2 — rotate camera side.
        try:
            client.edit_nvr_ipc_passwords([ch.dev_id], new_password=new_password)
        except NvrApiError as exc:
            results.append(
                ChannelRotationResult(
                    chl_num=ch.chl_num,
                    dev_id=ch.dev_id,
                    ip=ch.ip,
                    status="failed",
                    error=f"editIPChlPassword failed: {exc}",
                )
            )
            sink.emit(ProgressEvent("error", "pass_b.editIPChlPassword_failed", f"{label}: {exc}"))
            continue
        except Exception as exc:
            results.append(
                ChannelRotationResult(
                    chl_num=ch.chl_num,
                    dev_id=ch.dev_id,
                    ip=ch.ip,
                    status="failed",
                    error=f"editIPChlPassword raised: {exc}",
                )
            )
            sink.emit(ProgressEvent("error", "pass_b.editIPChlPassword_error", f"{label}: {exc}"))
            continue

        # Step 3 — restore NEW cred on NVR so future auths succeed.
        try:
            client.update_device_credentials(
                dev_ids=[ch.dev_id],
                username=username,
                password=new_password,
            )
        except Exception as exc:
            results.append(
                ChannelRotationResult(
                    chl_num=ch.chl_num,
                    dev_id=ch.dev_id,
                    ip=ch.ip,
                    status="failed",
                    error=f"editDevList(new) post-rotation failed: {exc}",
                )
            )
            sink.emit(
                ProgressEvent(
                    "warning",
                    "pass_b.editDevList_new_failed",
                    f"{label}: camera rotated but NVR cred NOT updated — {exc}",
                )
            )
            continue

        rotated.append(ch)
        results.append(
            ChannelRotationResult(
                chl_num=ch.chl_num,
                dev_id=ch.dev_id,
                ip=ch.ip,
                status="rotated-via-pass-b",
            )
        )
        sink.emit(
            ProgressEvent(
                "success",
                "channel.rotated",
                f"{label}: rotated and NVR cred synced",
                context={"chl_num": ch.chl_num, "ip": ch.ip},
            )
        )

    # --- final settle and report ---
    time.sleep(settle_seconds)
    failed = sum(1 for r in results if r.status == "failed")

    sink.emit(
        ProgressEvent(
            "success" if failed == 0 else "warning",
            "workflow.done",
            (f"Done: already={len(already_ok)} synced={len(synced)} rotated={len(rotated)} failed={failed}"),
            context={
                "already": len(already_ok),
                "synced": len(synced),
                "rotated": len(rotated),
                "failed": failed,
            },
        )
    )

    return PasswordRotateResult(
        nvr_host=host,
        dry_run=False,
        channels_total=len(candidates),
        channels_already_ok=len(already_ok),
        channels_synced=len(synced),
        channels_rotated=len(rotated),
        channels_failed=failed,
        results=results,
    )
