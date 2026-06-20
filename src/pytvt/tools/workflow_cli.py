"""CLI dispatcher for ``pytvt workflow <name>`` subcommands.

Kept separate from :mod:`pytvt.cli` so that importing the main CLI does
not force the workflow stack to load. Each workflow is its own argparse
subcommand so downstream application can mirror the same invocation for testing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from ..workflows import (
    ConsoleProgressSink,
    WorkflowError,
    WorkflowPrecheckError,
    change_site_subnet_via_nvr,
    compare_sites,
    rotate_nvr_channel_passwords,
    validate_site,
)
from ..xml_api import NvrClient


def workflow_cli(argv: Sequence[str] | None = None) -> None:
    """Dispatch ``pytvt workflow <name> ...`` subcommands."""
    parser = argparse.ArgumentParser(
        prog="pytvt workflow",
        description="Technician-facing multi-step operations against TVT NVRs.",
    )
    sub = parser.add_subparsers(dest="name", required=True)

    pw = sub.add_parser(
        "password-rotate",
        help="Rotate the native IPC admin password on one or more NVRs.",
        description=(
            "Rotate the native TVT admin password on every registered channel "
            "of one or more NVRs. Handles both directions of drift (NVR stored "
            "cred ahead of camera, or camera ahead of NVR) automatically."
        ),
    )
    pw.add_argument("--nvr", action="append", required=True, help="NVR host/IP (repeatable).")
    pw.add_argument("--nvr-username", default=os.environ.get("TVT_USERNAME", "admin"))
    pw.add_argument(
        "--nvr-password", default=os.environ.get("TVT_PASSWORD"), help="NVR admin password (default: $TVT_PASSWORD)."
    )
    pw.add_argument("--camera-username", default="admin", help="Camera-side admin username (default: admin).")
    pw.add_argument("--old-password", required=True, help="Camera's current admin password.")
    pw.add_argument(
        "--new-password",
        default=os.environ.get("TVT_CAM_PASSWORD"),
        help="Target camera password (default: $TVT_CAM_PASSWORD).",
    )
    pw.add_argument("--subnet", default=None, help="Optional CIDR filter, e.g. 10.0.0.0/24.")
    pw.add_argument("--apply", action="store_true", help="Actually perform rotations. Default is dry-run.")
    pw.add_argument("--json", action="store_true", help="Emit structured JSON to stdout instead of human text.")
    pw.add_argument("--quiet", action="store_true", help="Suppress per-step progress on stderr.")

    sc = sub.add_parser(
        "site-subnet-change",
        help="Migrate cameras on an NVR from one subnet to another.",
        description=(
            "Readdress every IPC registered on an NVR from --old-subnet into "
            "--new-subnet (host octets preserved 1:1) via the NVR HTTP API. "
            "Optionally rotate the IPC admin password in lockstep."
        ),
    )
    sc.add_argument("--nvr", action="append", required=True, help="NVR host/IP (repeatable).")
    sc.add_argument("--nvr-username", default=os.environ.get("TVT_USERNAME", "admin"))
    sc.add_argument(
        "--nvr-password", default=os.environ.get("TVT_PASSWORD"), help="NVR admin password (default: $TVT_PASSWORD)."
    )
    sc.add_argument("--old-subnet", required=True, help="Current camera subnet (e.g. 192.168.110.0/24).")
    sc.add_argument("--new-subnet", required=True, help="Target camera subnet with same prefix length.")
    sc.add_argument("--camera-username", default="admin", help="IPC admin username (default: admin).")
    sc.add_argument(
        "--camera-password",
        default=os.environ.get("TVT_CAM_PASSWORD"),
        help="Current IPC admin password (default: $TVT_CAM_PASSWORD).",
    )
    sc.add_argument(
        "--new-camera-password", default=None, help="If set with --rotate-passwords, rotate IPCs to this password."
    )
    sc.add_argument(
        "--rotate-passwords",
        action="store_true",
        help="After readdress, rotate IPC admin password to --new-camera-password.",
    )
    sc.add_argument(
        "--target-ip", action="append", default=None, help="Restrict migration to these old-subnet IPs (repeatable)."
    )
    sc.add_argument("--apply", action="store_true", help="Actually perform the migration. Default is dry-run.")
    sc.add_argument("--json", action="store_true")
    sc.add_argument("--quiet", action="store_true")

    vd = sub.add_parser(
        "validate",
        help="Read-only health check of one or more NVR sites.",
        description=(
            "Report per-channel online status, subnet conformance, and "
            "channel-count expectations. Uses NVR native-protocol polling "
            "(not ONVIF) as the authoritative credential-sync signal. "
            "No write operations; --apply has no effect."
        ),
    )
    vd.add_argument("--nvr", action="append", required=True, help="NVR host/IP (repeatable).")
    vd.add_argument("--nvr-username", default=os.environ.get("TVT_USERNAME", "admin"))
    vd.add_argument("--nvr-password", default=os.environ.get("TVT_PASSWORD"))
    vd.add_argument("--expected-subnet", default=None, help="Camera subnet every channel must be in (CIDR).")
    vd.add_argument("--expected-nvr-subnet", default=None, help="Subnet the NVR host itself must be in (CIDR).")
    vd.add_argument("--expected-channel-count", type=int, default=None, help="Required registered-channel count.")
    vd.add_argument(
        "--compare-baseline",
        default=None,
        help="Path to a JSON file produced by a previous --json "
        "run against a reference NVR; compare current results "
        "to it structurally.",
    )
    vd.add_argument("--json", action="store_true")
    vd.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    if args.name == "password-rotate":
        _run_password_rotate(args)
        return
    if args.name == "site-subnet-change":
        _run_site_subnet_change(args)
        return
    if args.name == "validate":
        _run_validate(args)
        return

    parser.error(f"unknown workflow: {args.name}")


def _run_password_rotate(args: argparse.Namespace) -> None:
    if not args.nvr_password:
        print("ERROR: --nvr-password or $TVT_PASSWORD required", file=sys.stderr)
        sys.exit(2)
    if not args.new_password:
        print("ERROR: --new-password or $TVT_CAM_PASSWORD required", file=sys.stderr)
        sys.exit(2)

    sink = ConsoleProgressSink(verbose=not args.quiet) if not args.json else None
    results = []
    worst_exit = 0

    for host in args.nvr:
        client = NvrClient(
            host,
            username=args.nvr_username,
            password=args.nvr_password,
        )
        try:
            client.login()
        except Exception as exc:
            print(f"ERROR: login to {host} failed: {exc}", file=sys.stderr)
            worst_exit = max(worst_exit, 2)
            continue

        try:
            res = rotate_nvr_channel_passwords(
                client,
                username=args.camera_username,
                old_password=args.old_password,
                new_password=args.new_password,
                subnet=args.subnet,
                apply=args.apply,
                progress=sink,
            )
        except WorkflowPrecheckError as exc:
            print(f"ERROR: precheck failed for {host}: {exc}", file=sys.stderr)
            sys.exit(2)
        except WorkflowError as exc:
            print(f"ERROR: workflow failed on {host}: {exc}", file=sys.stderr)
            worst_exit = max(worst_exit, 1)
            continue

        results.append(res.to_dict())
        if not res.success:
            worst_exit = max(worst_exit, 1)

    if args.json:
        json.dump({"runs": results}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        # Concise text summary.
        for r in results:
            print(
                f"{r['nvr_host']}: "
                f"already={r['channels_already_ok']} "
                f"synced={r['channels_synced']} "
                f"rotated={r['channels_rotated']} "
                f"failed={r['channels_failed']} "
                f"({'DRY-RUN' if r['dry_run'] else 'APPLIED'})"
            )

    sys.exit(worst_exit)


def _login_or_exit(
    host: str,
    username: str,
    password: str | None,
) -> NvrClient | None:
    """Best-effort login. Prints to stderr and returns None on failure."""
    if not password:
        print("ERROR: --nvr-password or $TVT_PASSWORD required", file=sys.stderr)
        sys.exit(2)
    client = NvrClient(host, username=username, password=password)
    try:
        client.login()
    except Exception as exc:
        print(f"ERROR: login to {host} failed: {exc}", file=sys.stderr)
        return None
    return client


def _run_site_subnet_change(args: argparse.Namespace) -> None:
    if not args.camera_password:
        print("ERROR: --camera-password or $TVT_CAM_PASSWORD required", file=sys.stderr)
        sys.exit(2)
    if args.rotate_passwords and not args.new_camera_password:
        print("ERROR: --rotate-passwords requires --new-camera-password", file=sys.stderr)
        sys.exit(2)

    sink = ConsoleProgressSink(verbose=not args.quiet) if not args.json else None
    results = []
    worst_exit = 0

    for host in args.nvr:
        client = _login_or_exit(host, args.nvr_username, args.nvr_password)
        if client is None:
            worst_exit = max(worst_exit, 2)
            continue
        try:
            res = change_site_subnet_via_nvr(
                client,
                old_subnet=args.old_subnet,
                new_subnet=args.new_subnet,
                camera_username=args.camera_username,
                camera_password=args.camera_password,
                new_camera_password=args.new_camera_password,
                target_ips=args.target_ip,
                rotate_passwords=args.rotate_passwords,
                apply=args.apply,
                progress=sink,
            )
        except WorkflowPrecheckError as exc:
            print(f"ERROR: precheck failed for {host}: {exc}", file=sys.stderr)
            sys.exit(2)
        except WorkflowError as exc:
            print(f"ERROR: workflow failed on {host}: {exc}", file=sys.stderr)
            worst_exit = max(worst_exit, 1)
            continue

        results.append(res.to_dict())
        if not res.success:
            worst_exit = max(worst_exit, 1)

    if args.json:
        json.dump({"runs": results}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for r in results:
            print(
                f"{r['nvr_host']}: "
                f"{r['old_subnet']} -> {r['new_subnet']} | "
                f"readdressed={r['cameras_readdressed']}/{r['cameras_total']} "
                f"channel_ip={r['channels_ip_updated']} "
                f"failed={r['cameras_failed']} "
                f"({'DRY-RUN' if r['dry_run'] else 'APPLIED'})"
            )

    sys.exit(worst_exit)


def _run_validate(args: argparse.Namespace) -> None:
    sink = ConsoleProgressSink(verbose=not args.quiet) if not args.json else None
    results = []
    worst_exit = 0

    for host in args.nvr:
        client = _login_or_exit(host, args.nvr_username, args.nvr_password)
        if client is None:
            worst_exit = max(worst_exit, 2)
            continue
        res = validate_site(
            client,
            expected_subnet=args.expected_subnet,
            expected_channel_count=args.expected_channel_count,
            expected_nvr_subnet=args.expected_nvr_subnet,
            progress=sink,
        )
        results.append(res)
        if not res.ok:
            worst_exit = max(worst_exit, 1)

    comparisons = []
    if args.compare_baseline:
        try:
            with open(args.compare_baseline, encoding="utf-8") as f:
                baseline_doc = json.load(f)
        except OSError as exc:
            print(f"ERROR: cannot read baseline: {exc}", file=sys.stderr)
            sys.exit(2)
        baseline_runs = baseline_doc.get("runs", [])
        if not baseline_runs:
            print("ERROR: baseline has no 'runs' array", file=sys.stderr)
            sys.exit(2)
        # Reconstruct a minimal baseline SiteValidationResult to feed compare_sites.
        from ..workflows import SiteValidationResult  # avoid top-level import cycle

        first = baseline_runs[0]
        baseline_result = SiteValidationResult(
            nvr_host=first.get("nvr_host", "baseline"),
            channels_total=first.get("channels_total", 0),
            channels_online=first.get("channels_online", 0),
            channels_in_subnet=first.get("channels_in_subnet", 0),
            issues=list(first.get("issues", [])),
        )
        for candidate in results:
            cmp = compare_sites(baseline_result, candidate)
            comparisons.append(cmp.to_dict())
            if not cmp.ok:
                worst_exit = max(worst_exit, 1)

    if args.json:
        json.dump(
            {
                "runs": [r.to_dict() for r in results],
                "comparisons": comparisons,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        for r in results:
            label = "OK" if r.ok else f"{len(r.issues)} issue(s)"
            print(
                f"{r.nvr_host}: {label} | "
                f"online={r.channels_online}/{r.channels_total} "
                f"in_subnet={r.channels_in_subnet}/{r.channels_total}"
            )
            for issue in r.issues:
                print(f"  - {issue}")
        for c in comparisons:
            status = "OK" if c["ok"] else "DIFFERS"
            print(
                f"compare {c['baseline_host']} vs {c['candidate_host']}: {status} "
                f"(delta channels={c['channel_count_delta']}, "
                f"offline={c['offline_delta']})"
            )
            for d in c.get("differences", []):
                print(f"  - {d}")

    sys.exit(worst_exit)
