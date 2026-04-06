"""CLI entry points for pytvt.

Console scripts (defined in pyproject.toml):
    pytvt           — bulk NVR scanner
    pytvt-discover  — LAN / subnet device discovery
    pytvt-api       — NVR web CGI client
    pytvt-snapshot  — camera snapshot capture
    pytvt-diff      — compare two scan result files

The public surface is thin entry-point functions. Everything else is
prefixed with ``_`` and should not be imported.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import dotenv

dotenv.load_dotenv()

from .config import DEFAULT_API_URL, load_config
from .constants import CLI_BACKEND_CHOICES
from .discovery import (
    discover_devices,
    discover_subnet,
    discovery_to_scanner_format,
    print_discovery_report,
    save_discovery_xlsx,
)
from .models import DeviceEntry, ScannerConfig, ScanResult
from .output import (
    print_nvr_report,
    print_summary,
    save_csv,
    save_failed_devices,
    save_json,
    save_xlsx_per_site,
)
from .scanner import filter_tvt_devices, load_devices, scan_single_nvr


# ── Public entry points ──────────────────────────────────────────────


def main() -> None:
    """Main scanner CLI — the ``pytvt`` command."""
    args = _build_parser().parse_args()
    config = load_config(args.config)

    # Discovery-only mode (no scanning)
    if args.discover_only or (args.subnet and not args.input and not args.discover):
        _handle_discover_only(args, config)
        return

    if not args.input and not args.discover and not args.subnet:
        _build_parser().error("input file is required (or use --discover / --discover-only / --subnet)")

    config = _apply_cli_overrides(config, args)
    devices = _prepare_devices(args, config)

    if not devices:
        print("No TVT devices found matching the criteria.", file=sys.stderr)
        sys.exit(1)

    _print_scan_header(devices, args.backend, config)
    results = _run_scan(devices, config, args.backend)
    results.sort(key=lambda r: r.site)

    for r in results:
        print_nvr_report(r)
    print_summary(results)

    _emit_output(results, devices, args)


def discover() -> None:
    """Discovery CLI — the ``pytvt-discover`` command."""
    from .discovery import main as _main

    _main()


def api() -> None:
    """NVR API CLI — the ``pytvt-api`` command."""
    from .nvr_api import main as _main

    _main()


def snapshot() -> None:
    """Snapshot CLI — the ``pytvt-snapshot`` command."""
    from .snapshot import main as _main

    _main()


def diff_cli() -> None:
    """Diff CLI — the ``pytvt-diff`` command."""
    parser = argparse.ArgumentParser(
        prog="pytvt-diff",
        description="Compare two pytvt scan result files and report changes",
    )
    parser.add_argument("old", help="Path to the older scan result JSON file")
    parser.add_argument("new", help="Path to the newer scan result JSON file")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    output_group.add_argument("--summary", action="store_true", help="One-line summary only")

    args = parser.parse_args()

    from .diff import diff_scans, format_diff_json, format_diff_summary, format_diff_text, load_scan_file

    try:
        old_results = load_scan_file(args.old)
    except ValueError as exc:
        print(f"Error reading old file: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        new_results = load_scan_file(args.new)
    except ValueError as exc:
        print(f"Error reading new file: {exc}", file=sys.stderr)
        sys.exit(1)

    result = diff_scans(old_results, new_results, old_file=args.old, new_file=args.new)

    if args.json_output:
        print(format_diff_json(result))
    elif args.summary:
        print(format_diff_summary(result))
    else:
        print(format_diff_text(result))


# ── Argument parser ──────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """Construct the ``pytvt`` argument parser."""
    parser = argparse.ArgumentParser(
        description="TVT NVR Camera Scanner - Retrieve camera lists from TVT NVRs",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to JSON file with NVR device list (optional with --discover)",
    )
    parser.add_argument("--config", default="config.json", help="Path to config.json (default: config.json)")
    parser.add_argument("--output", "-o", help="Output file path (.csv or .json)")
    parser.add_argument("--site", "-s", help="Filter to a specific site name (partial match)")
    parser.add_argument("--concurrency", "-c", type=int, help="Max concurrent NVR scans (default: from config)")
    parser.add_argument("--username", "-u", help="NVR username (overrides config)")
    parser.add_argument("--password", "-p", help="NVR password (overrides config)")
    parser.add_argument(
        "--backend",
        "-b",
        choices=CLI_BACKEND_CHOICES,
        default="protocol",
        help="Scan backend: protocol (pure Python, default), sdk (HTTP API), sdk-local (subprocess), both (protocol then sdk)",
    )
    parser.add_argument("--xlsx", metavar="DIR", help="Output one .xlsx file per site into DIR (e.g. --xlsx files/)")
    parser.add_argument(
        "--failed",
        metavar="FILE",
        help="Write devices that failed to scan to a JSON file for retry",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"TVT SDK API URL for sdk backend (default: {DEFAULT_API_URL})",
    )

    # Discovery flags
    parser.add_argument("--discover", action="store_true", help="Run LAN discovery before scanning")
    parser.add_argument("--discover-timeout", type=float, default=5.0, help="Seconds per discovery probe (default: 5)")
    parser.add_argument("--discover-only", action="store_true", help="Only discover devices, don't scan")
    parser.add_argument("--subnet", action="append", metavar="CIDR", help="Scan a remote subnet (repeatable)")
    parser.add_argument("--discover-concurrency", type=int, default=50, help="Max parallel probes for subnet sweep")
    parser.add_argument("--no-tcp-fallback", action="store_true", help="Skip TCP fallback during subnet sweep")

    return parser


# ── Private orchestration helpers ────────────────────────────────────


def _apply_cli_overrides(config: ScannerConfig, args: argparse.Namespace) -> ScannerConfig:
    """Mutate *config* with CLI flag overrides and return it."""
    config.api_url = args.api_url
    if args.concurrency:
        config.concurrency = args.concurrency
    if args.username:
        config.username = args.username
    if args.password:
        config.password = args.password
    return config


def _run_discovery(args: argparse.Namespace) -> list[dict]:
    """Execute all discovery probes and return de-duplicated raw device dicts."""
    all_found: list[dict] = []

    if not args.subnet:
        print(f"Searching for TVT devices on LAN (timeout={args.discover_timeout}s)...")
        all_found.extend(discover_devices(timeout=args.discover_timeout))

    if args.subnet:
        for cidr in args.subnet:
            all_found.extend(
                discover_subnet(
                    cidr,
                    timeout=args.discover_timeout,
                    concurrency=args.discover_concurrency,
                    tcp_fallback=not args.no_tcp_fallback,
                )
            )

    return _dedupe_by_mac(all_found)


def _handle_discover_only(args: argparse.Namespace, config: ScannerConfig) -> None:
    """Handle ``--discover-only`` and bare ``--subnet`` modes."""
    discovered = _run_discovery(args)
    print_discovery_report(discovered)

    if args.output:
        scan_port = config.port if args.input or args.discover else 6036
        scanner_devs = discovery_to_scanner_format(
            discovered,
            site=args.site or "Discovered",
            scan_port=scan_port,
        )
        with open(args.output, "w") as f:
            json.dump(scanner_devs, f, indent=2)
        print(f"Scanner JSON ({len(scanner_devs)} devices) saved to: {args.output}")

    if args.xlsx:
        save_discovery_xlsx(discovered, str(Path(args.xlsx) / "discovery.xlsx"))


def _prepare_devices(args: argparse.Namespace, config: ScannerConfig) -> list[DeviceEntry]:
    """Load inventory, merge discovery, filter, and de-duplicate."""
    devices: list[DeviceEntry] = []

    if args.input:
        devices = filter_tvt_devices(load_devices(args.input))

    if args.discover or args.subnet:
        discovered = _run_discovery(args)
        print_discovery_report(discovered)
        discovered_devs = discovery_to_scanner_format(
            discovered,
            site=args.site or "Discovered",
        )
        existing_ips = {d.ip for d in devices}
        for dd in discovered_devs:
            if dd["ip"] not in existing_ips:
                devices.append(DeviceEntry.from_dict(dd))
                existing_ips.add(dd["ip"])

    if args.site:
        site_lower = args.site.lower()
        devices = [d for d in devices if site_lower in d.site.lower()]

    return _dedupe_devices(devices)


def _run_scan(
    devices: list[DeviceEntry],
    config: ScannerConfig,
    backend: str,
) -> list[ScanResult]:
    """Scan all devices concurrently and return results."""
    results: list[ScanResult] = []

    with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        future_to_dev = {executor.submit(scan_single_nvr, dev, config, backend): dev for dev in devices}

        for i, future in enumerate(as_completed(future_to_dev), 1):
            dev = future_to_dev[future]
            try:
                result = future.result()
                results.append(result)
                status = "OK" if result.success else "FAIL"
                print(
                    f"  [{i}/{len(devices)}] {dev.site} / "
                    f"{dev.hostname or dev.ip} - {status} ({result.camera_count} cameras)"
                )
            except Exception as e:
                print(
                    f"  [{i}/{len(devices)}] {dev.site} / {dev.ip} - ERROR: {e}",
                    file=sys.stderr,
                )

    return results


def _emit_output(
    results: list[ScanResult],
    devices: list[DeviceEntry],
    args: argparse.Namespace,
) -> None:
    """Write CSV, JSON, XLSX, and failed-device outputs as requested."""
    if args.output:
        if args.output.endswith(".csv"):
            save_csv(results, args.output)
        else:
            save_json(results, args.output)

    if args.xlsx:
        save_xlsx_per_site(results, args.xlsx)

    failed_path = args.failed or str(Path("files") / "failed_devices.json")
    Path(failed_path).parent.mkdir(parents=True, exist_ok=True)
    save_failed_devices(results, devices, failed_path)


def _print_scan_header(
    devices: list[DeviceEntry],
    backend: str,
    config: ScannerConfig,
) -> None:
    """Print a brief banner before scanning starts."""
    from .registry import resolve_execution_plan

    plan = resolve_execution_plan(backend)
    print(f"\nTVT NVR Camera Scanner")
    print(f"Found {len(devices)} unique TVT NVR(s) to scan")
    print(f"Backend: {plan.label}")
    print(f"Credentials: {config.username} / {'*' * len(config.password)}")
    print(f"Concurrency: {config.concurrency}")


# ── Pure helpers ─────────────────────────────────────────────────────


def _dedupe_by_mac(devices: list[dict]) -> list[dict]:
    """De-duplicate raw discovery dicts by MAC address."""
    seen: set[str] = set()
    unique: list[dict] = []
    for d in devices:
        mac = d.get("mac", "")
        if mac and mac in seen:
            continue
        if mac:
            seen.add(mac)
        unique.append(d)
    return unique


def _dedupe_devices(devices: list[DeviceEntry]) -> list[DeviceEntry]:
    """De-duplicate DeviceEntry list by IP, skipping link-local addresses."""
    seen: set[str] = set()
    unique: list[DeviceEntry] = []
    for d in devices:
        if d.ip.startswith("169.254."):
            continue
        if d.ip not in seen:
            seen.add(d.ip)
            unique.append(d)
    return unique
