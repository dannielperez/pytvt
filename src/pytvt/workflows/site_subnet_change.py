"""Site subnet migration workflow — TVT cameras via NVR HTTP API.

Formalises the field-validated logic from
``data/migrate_tvt_cameras_via_nvr_api.py`` and
``data/migrate_tvt_site_subnet.py`` into a cross-platform, GUI-friendly
entry point. Only the NVR-API path is exposed here — the native SDK
camera-modify path has a proprietary runtime dependency and stays in
the legacy scripts.

The workflow performs, per camera, the following sequence against a
single authenticated NVR:

    1. **Discover** cameras registered as free LAN devices *and* as
       configured channels whose IP falls in ``old_subnet``. The union
       is the readdress target set.
    2. **Readdress** each camera via ``editDevNetworkList``
       (NVR-proxied UI call), mapping host octets 1:1 into
       ``new_subnet``.
    3. **Update channel IPs** stored by the NVR (``editDevList``) so
       the NVR reconnects on the new address.
    4. **(Optional) Rotate IPC password** in lockstep by delegating to
       :func:`pytvt.workflows.rotate_nvr_channel_passwords`.

All write operations require ``apply=True``; default is a pure probe
that returns the planned actions.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from pytvt.models import NvrApiError
from pytvt.xml_api import Channel, NvrClient, NvrLanFreeDevice

from .exceptions import WorkflowError, WorkflowPrecheckError
from .password_rotate import (
    PasswordRotateResult,
    rotate_nvr_channel_passwords,
)
from .progress import NullProgressSink, ProgressEvent, ProgressSink

_DEFAULT_SETTLE_SECONDS = 8
"""Delay after ``editDevNetworkList`` before polling for channel online.

Cameras take several seconds to apply a new address and reboot; the
NVR's online-status poller is itself asynchronous. 8 s matches field
observations from production deployments.
"""


@dataclass(frozen=True)
class CameraReaddressPlan:
    """Planned IP change for a single camera, independent of apply/dry-run."""

    old_ip: str
    new_ip: str
    mac: str = ""
    netmask: str = ""
    gateway: str = ""
    source: str = ""  # "channel", "free", or "channel+free"


@dataclass(frozen=True)
class CameraReaddressResult:
    """Outcome of one ``editDevNetworkList`` + channel-update attempt."""

    old_ip: str
    new_ip: str
    mac: str
    status: str  # "planned" | "readdressed" | "channel-updated" | "failed" | "skipped"
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SiteSubnetChangeResult:
    """Aggregate outcome of a site-subnet migration run against one NVR.

    Attributes:
        nvr_host: NVR IP/hostname the workflow ran against.
        old_subnet: CIDR of the source subnet.
        new_subnet: CIDR of the target subnet.
        dry_run: ``True`` if no side effects were performed.
        cameras_total: Cameras in scope (union of free + channel targets).
        cameras_readdressed: Cameras whose IP this call changed.
        cameras_failed: Cameras whose readdress failed.
        channels_ip_updated: Configured channels whose NVR-stored IP
            was rewritten in step 3.
        password_rotation: Nested result when the caller requested a
            lockstep password change; ``None`` otherwise.
        results: Per-camera detail.
        error: Orchestration-level failure; empty on success.
    """

    nvr_host: str
    old_subnet: str
    new_subnet: str
    dry_run: bool
    cameras_total: int = 0
    cameras_readdressed: int = 0
    cameras_failed: int = 0
    channels_ip_updated: int = 0
    password_rotation: PasswordRotateResult | None = None
    results: list[CameraReaddressResult] = field(default_factory=list)
    error: str = ""

    @property
    def success(self) -> bool:
        if self.error:
            return False
        if self.cameras_failed:
            return False
        return not (self.password_rotation is not None and not self.password_rotation.success)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["success"] = self.success
        if self.password_rotation is not None:
            d["password_rotation"] = self.password_rotation.to_dict()
        return d


def _mapped_ip(
    old_ip: str,
    old_net: ipaddress.IPv4Network,
    new_net: ipaddress.IPv4Network,
) -> str:
    """Map *old_ip* into *new_net* preserving host octets.

    Raises :class:`ValueError` if *old_ip* is outside *old_net* or the
    host would fall outside *new_net*.
    """
    if old_net.prefixlen != new_net.prefixlen:
        raise ValueError(
            f"old and new subnets must have matching prefix length ({old_net.prefixlen} vs {new_net.prefixlen})"
        )
    old = ipaddress.ip_address(old_ip)
    if old not in old_net:
        raise ValueError(f"{old_ip} not in {old_net}")
    host = int(old) - int(old_net.network_address)
    return str(ipaddress.ip_address(int(new_net.network_address) + host))


def _collect_targets(
    client: NvrClient,
    old_net: ipaddress.IPv4Network,
    new_net: ipaddress.IPv4Network,
) -> tuple[list[CameraReaddressPlan], dict[str, str]]:
    """Discover cameras in *old_net* and return the readdress plan.

    Returns a tuple ``(plans, channel_new_ip_by_dev_id)`` where the
    mapping is used in step 3 to rewrite the NVR's stored channel IP
    once the camera itself answers on its new address.
    """
    # Configured channels (already added cameras).
    try:
        channels: list[Channel] = client.query_channels()
    except Exception as exc:
        raise WorkflowError(f"queryDevList failed: {exc}") from exc

    # Free LAN devices (discovered but not configured).
    try:
        free_devices: list[NvrLanFreeDevice] = client.query_nvr_lan_free_devices()
    except Exception as exc:
        raise WorkflowError(f"queryNvrLanDevList failed: {exc}") from exc

    # Index free devices by IP for MAC/mask/gateway lookup.
    free_by_ip: dict[str, NvrLanFreeDevice] = {}
    for dev in free_devices:
        ip = (dev.ip or "").strip()
        if ip:
            free_by_ip[ip] = dev

    plans: dict[str, CameraReaddressPlan] = {}
    channel_new_ip: dict[str, str] = {}

    for ch in channels:
        ip = (ch.ip or "").strip()
        if not ip:
            continue
        try:
            if ipaddress.ip_address(ip) not in old_net:
                continue
        except ValueError:
            continue
        new_ip = _mapped_ip(ip, old_net, new_net)
        src_free = free_by_ip.get(ip)
        plans[ip] = CameraReaddressPlan(
            old_ip=ip,
            new_ip=new_ip,
            mac=src_free.mac if src_free else "",
            netmask=src_free.mask if src_free else "",
            gateway=src_free.gateway if src_free else "",
            source="channel+free" if src_free else "channel",
        )
        channel_new_ip[ch.dev_id] = new_ip

    for ip, dev in free_by_ip.items():
        if ip in plans:
            continue
        try:
            if ipaddress.ip_address(ip) not in old_net:
                continue
        except ValueError:
            continue
        plans[ip] = CameraReaddressPlan(
            old_ip=ip,
            new_ip=_mapped_ip(ip, old_net, new_net),
            mac=dev.mac,
            netmask=dev.mask,
            gateway=dev.gateway,
            source="free",
        )

    ordered = sorted(
        plans.values(),
        key=lambda p: (ipaddress.ip_address(p.old_ip), p.source),
    )
    return ordered, channel_new_ip


def _edit_channel_ip(client: NvrClient, *, dev_id: str, new_ip: str) -> None:
    """Rewrite the NVR-stored IP for one channel via ``editDevList``.

    Uses pytvt's internal ``_post``/``_build_request_with_content``
    helpers; there is no public wrapper for this exact operation yet.
    """
    content = f'<content type="list"><item id="{dev_id}"><ip>{new_ip}</ip></item></content>'
    data = client._post("editDevList", client._build_request_with_content(content))
    client._check_response(data, "editDevList")


def change_site_subnet_via_nvr(
    client: NvrClient,
    *,
    old_subnet: str,
    new_subnet: str,
    camera_username: str = "admin",
    camera_password: str,
    new_camera_password: str | None = None,
    target_ips: Sequence[str] | None = None,
    apply: bool = False,
    rotate_passwords: bool = False,
    settle_seconds: float = _DEFAULT_SETTLE_SECONDS,
    progress: ProgressSink | None = None,
) -> SiteSubnetChangeResult:
    """Migrate one NVR's registered IPCs from *old_subnet* to *new_subnet*.

    The *client* must already be logged in; callers reuse their own
    :class:`~pytvt.NvrClient` session (including a downstream GUI).

    Args:
        client: An authenticated :class:`~pytvt.NvrClient`.
        old_subnet: CIDR of the current camera subnet (e.g.
            ``"192.168.110.0/24"``).
        new_subnet: CIDR of the target camera subnet. Must share a
            prefix length with *old_subnet* — host octets are preserved
            1:1, since that mapping has been the convention at every
            site migrated so far.
        camera_username: Admin user on the IPCs (default ``"admin"``).
        camera_password: Current IPC admin password — required for the
            NVR to authenticate to each camera during readdress.
        new_camera_password: Target password if ``rotate_passwords`` is
            set. Ignored otherwise.
        target_ips: Optional allow-list of old-subnet IPs. Cameras not
            on the list are ignored. ``None`` means all discovered
            cameras in *old_subnet*.
        apply: If ``False`` (default), probe and report; no writes.
        rotate_passwords: If ``True``, run
            :func:`rotate_nvr_channel_passwords` after readdress using
            ``camera_password`` as the old and ``new_camera_password``
            as the new. Scoped to *new_subnet* so only cameras we just
            moved are touched.
        settle_seconds: Time to wait after ``editDevNetworkList``
            before rewriting channel IPs. Don't lower below ~5 s
            outside tests.
        progress: Optional :class:`ProgressSink` for streaming events.

    Returns:
        :class:`SiteSubnetChangeResult` — never raises for per-camera
        failures. Raises :class:`WorkflowPrecheckError` for invalid
        input and :class:`WorkflowError` for orchestration failures.
    """
    sink: ProgressSink = progress or NullProgressSink()

    # --- prechecks ---
    if not camera_password:
        raise WorkflowPrecheckError("camera_password is required")
    if rotate_passwords and not new_camera_password:
        raise WorkflowPrecheckError("new_camera_password is required when rotate_passwords=True")
    if rotate_passwords and new_camera_password == camera_password:
        raise WorkflowPrecheckError("new_camera_password equals camera_password (no-op rotation)")

    try:
        old_net = ipaddress.ip_network(old_subnet, strict=False)
        new_net = ipaddress.ip_network(new_subnet, strict=False)
    except ValueError as exc:
        raise WorkflowPrecheckError(f"invalid subnet: {exc}") from exc

    if old_net.prefixlen != new_net.prefixlen:
        raise WorkflowPrecheckError("old_subnet and new_subnet must have the same prefix length")
    if old_net.overlaps(new_net):
        raise WorkflowPrecheckError(f"old_subnet {old_net} overlaps new_subnet {new_net}")

    host = getattr(client, "host", "?")

    # --- discovery ---
    try:
        plans, channel_new_ip_by_dev = _collect_targets(client, old_net, new_net)
    except WorkflowError as exc:
        return SiteSubnetChangeResult(
            nvr_host=host,
            old_subnet=str(old_net),
            new_subnet=str(new_net),
            dry_run=not apply,
            error=str(exc),
        )

    if target_ips is not None:
        wanted = {ip.strip() for ip in target_ips}
        plans = [p for p in plans if p.old_ip in wanted]
        channel_new_ip_by_dev = {
            dev_id: new_ip
            for dev_id, new_ip in channel_new_ip_by_dev.items()
            # Keep only channels whose new_ip corresponds to a plan we kept.
            if new_ip in {p.new_ip for p in plans}
        }

    sink.emit(
        ProgressEvent(
            level="info",
            code="workflow.start",
            message=(
                f"Subnet migration on {host}: "
                f"{old_net} -> {new_net} "
                f"({len(plans)} camera(s), "
                f"{'DRY-RUN' if not apply else 'APPLY'})"
            ),
            context={
                "nvr_host": host,
                "old_subnet": str(old_net),
                "new_subnet": str(new_net),
                "cameras_in_scope": len(plans),
                "apply": apply,
            },
        )
    )

    results: list[CameraReaddressResult] = []

    if not plans:
        sink.emit(
            ProgressEvent(
                level="success",
                code="workflow.noop",
                message=f"No cameras found in {old_net}; nothing to do.",
            )
        )
        return SiteSubnetChangeResult(
            nvr_host=host,
            old_subnet=str(old_net),
            new_subnet=str(new_net),
            dry_run=not apply,
            cameras_total=0,
            results=results,
        )

    # --- dry-run ---
    if not apply:
        for p in plans:
            sink.emit(
                ProgressEvent(
                    level="info",
                    code="camera.plan",
                    message=f"[DRY-RUN] {p.old_ip} -> {p.new_ip} ({p.source})",
                    context=asdict(p),
                )
            )
            results.append(
                CameraReaddressResult(
                    old_ip=p.old_ip,
                    new_ip=p.new_ip,
                    mac=p.mac,
                    status="planned",
                )
            )
        return SiteSubnetChangeResult(
            nvr_host=host,
            old_subnet=str(old_net),
            new_subnet=str(new_net),
            dry_run=True,
            cameras_total=len(plans),
            results=results,
        )

    # --- Step 2: editDevNetworkList for each camera ---
    readdressed_new_ips: list[str] = []
    sink.emit(
        ProgressEvent(
            level="info",
            code="step.readdress.start",
            message=f"Readdressing {len(plans)} camera(s) via editDevNetworkList",
        )
    )
    for p in plans:
        # Default gateway/mask if we didn't learn them from a free-device entry.
        netmask = p.netmask or str(old_net.netmask)
        gateway = p.gateway or str(next(new_net.hosts()))
        try:
            client.edit_nvr_lan_device_network(
                old_ip=p.old_ip,
                new_ip=p.new_ip,
                netmask=netmask,
                gateway=gateway,
                username=camera_username,
                password=camera_password,
            )
            results.append(
                CameraReaddressResult(
                    old_ip=p.old_ip,
                    new_ip=p.new_ip,
                    mac=p.mac,
                    status="readdressed",
                )
            )
            readdressed_new_ips.append(p.new_ip)
            sink.emit(
                ProgressEvent(
                    "success",
                    "camera.readdressed",
                    f"{p.old_ip} -> {p.new_ip}",
                    context={"old_ip": p.old_ip, "new_ip": p.new_ip, "mac": p.mac},
                )
            )
        except NvrApiError as exc:
            results.append(
                CameraReaddressResult(
                    old_ip=p.old_ip,
                    new_ip=p.new_ip,
                    mac=p.mac,
                    status="failed",
                    error=f"editDevNetworkList failed: {exc}",
                )
            )
            sink.emit(
                ProgressEvent(
                    "error",
                    "camera.readdress_failed",
                    f"{p.old_ip}: {exc}",
                    context={"old_ip": p.old_ip},
                )
            )
        except Exception as exc:
            results.append(
                CameraReaddressResult(
                    old_ip=p.old_ip,
                    new_ip=p.new_ip,
                    mac=p.mac,
                    status="failed",
                    error=f"editDevNetworkList raised: {exc}",
                )
            )
            sink.emit(
                ProgressEvent(
                    "error",
                    "camera.readdress_error",
                    f"{p.old_ip}: {exc}",
                )
            )

    # --- Step 3: settle, then rewrite NVR channel IPs ---
    if readdressed_new_ips:
        sink.emit(
            ProgressEvent(
                level="info",
                code="step.settle",
                message=f"Waiting {settle_seconds}s for cameras to apply new addresses",
            )
        )
        time.sleep(settle_seconds)

    channels_ip_updated = 0
    # Only update channels whose camera's readdress step reported success.
    successful_new_ips = {r.new_ip for r in results if r.status == "readdressed"}
    for dev_id, new_ip in channel_new_ip_by_dev.items():
        if new_ip not in successful_new_ips:
            continue
        try:
            _edit_channel_ip(client, dev_id=dev_id, new_ip=new_ip)
            channels_ip_updated += 1
            sink.emit(
                ProgressEvent(
                    "success",
                    "channel.ip_updated",
                    f"NVR channel {dev_id} -> {new_ip}",
                    context={"dev_id": dev_id, "new_ip": new_ip},
                )
            )
        except Exception as exc:
            sink.emit(
                ProgressEvent(
                    "warning",
                    "channel.ip_update_failed",
                    f"editDevList for {dev_id}: {exc}",
                    context={"dev_id": dev_id, "new_ip": new_ip},
                )
            )
            # Find the associated camera result and annotate it.
            for i, r in enumerate(results):
                if r.new_ip == new_ip and r.status == "readdressed":
                    results[i] = CameraReaddressResult(
                        old_ip=r.old_ip,
                        new_ip=r.new_ip,
                        mac=r.mac,
                        status="failed",
                        error=f"editDevList (channel IP) failed: {exc}",
                    )
                    break

    # --- Step 4 (optional): password rotation scoped to new subnet ---
    password_result: PasswordRotateResult | None = None
    if rotate_passwords:
        sink.emit(
            ProgressEvent(
                level="info",
                code="step.password_rotate",
                message="Rotating IPC passwords across migrated channels",
            )
        )
        try:
            password_result = rotate_nvr_channel_passwords(
                client,
                username=camera_username,
                old_password=camera_password,
                new_password=new_camera_password,  # type: ignore[arg-type]
                subnet=str(new_net),
                apply=True,
                progress=sink,
            )
        except WorkflowError as exc:
            sink.emit(
                ProgressEvent(
                    "error",
                    "password_rotate.failed",
                    f"password rotation failed: {exc}",
                )
            )

    failed = sum(1 for r in results if r.status == "failed")
    readdressed = sum(1 for r in results if r.status in {"readdressed", "channel-updated"})

    sink.emit(
        ProgressEvent(
            "success" if failed == 0 else "warning",
            "workflow.done",
            (f"Done: readdressed={readdressed} channel_ip_updated={channels_ip_updated} failed={failed}"),
            context={
                "readdressed": readdressed,
                "channel_ip_updated": channels_ip_updated,
                "failed": failed,
            },
        )
    )

    return SiteSubnetChangeResult(
        nvr_host=host,
        old_subnet=str(old_net),
        new_subnet=str(new_net),
        dry_run=False,
        cameras_total=len(plans),
        cameras_readdressed=readdressed,
        cameras_failed=failed,
        channels_ip_updated=channels_ip_updated,
        password_rotation=password_result,
        results=results,
    )
