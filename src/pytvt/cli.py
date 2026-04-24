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


def _load_dotenv() -> None:
    """Load local .env for CLI execution paths only.

    Keeping this out of module import avoids global env contamination in tests
    that import parser helpers from this module.
    """
    dotenv.load_dotenv()


# ── Public entry points ──────────────────────────────────────────────


def main() -> None:
    """Main scanner CLI — the ``pytvt`` command."""
    _load_dotenv()
    if len(sys.argv) > 1 and sys.argv[1] == "scan-nvr":
        scan_nvr_cli(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        doctor_cli(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "connect" and (len(sys.argv) == 2 or sys.argv[2].startswith("-")):
        _connect_main(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "connect-many" and (
        len(sys.argv) == 2 or sys.argv[2].startswith("-")
    ):
        _connect_many_main(sys.argv[2:])
        return

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
    _load_dotenv()
    from .discovery import main as _main

    _main()


def api() -> None:
    """NVR API CLI — the ``pytvt-api`` command."""
    _load_dotenv()
    from .nvr_api import main as _main

    _main()


def snapshot() -> None:
    """Snapshot CLI — the ``pytvt-snapshot`` command."""
    _load_dotenv()
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


def _build_scan_nvr_parser() -> argparse.ArgumentParser:
    """Construct the ``pytvt scan-nvr`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="pytvt scan-nvr",
        description="Scan a single TVT NVR via the local native SDK",
    )
    parser.add_argument("ip", help="NVR IP address or hostname")
    parser.add_argument("port", nargs="?", type=int, default=6036, help="SDK port (default: 6036)")
    parser.add_argument("username", nargs="?", default="admin", help="Login username (default: admin)")
    parser.add_argument("password", nargs="?", default="", help="Login password")
    parser.add_argument("--sdk-path", help="Path to libdvrnetsdk.so or the vendor SDK root")
    parser.add_argument("--max-channels", type=int, default=64, help="Maximum IPC channels to enumerate")
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Connect/receive timeout in seconds (default: 10.0)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON only instead of the legacy sentinel markers",
    )
    parser.add_argument(
        "--no-sentinels",
        action="store_true",
        dest="json_output",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--indent", type=int, default=None, help="Indent JSON output for readability")
    return parser


def scan_nvr_cli(argv: list[str] | None = None) -> None:
    """Handle ``pytvt scan-nvr`` and the ``pytvt-scan`` console script."""
    from .sdk_local import scan_nvr_payload

    parser = _build_scan_nvr_parser()
    args = parser.parse_args(argv)

    payload = scan_nvr_payload(
        args.ip,
        port=args.port,
        username=args.username,
        password=args.password,
        sdk_path=args.sdk_path,
        max_channels=args.max_channels,
        timeout=args.timeout,
    )
    rendered = json.dumps(payload, indent=args.indent)

    if args.json or args.json_output:
        print(rendered)
    else:
        print("___JSON_START___")
        print(rendered)
        print("___JSON_END___")

    if not payload.get("success", False):
        sys.exit(1)


def _build_doctor_parser() -> argparse.ArgumentParser:
    """Construct the ``pytvt doctor`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="pytvt doctor",
        description="Inspect pytvt runtime and SDK availability",
    )
    parser.add_argument("--sdk-path", help="Path to libdvrnetsdk.so or the vendor SDK root")
    parser.add_argument("--json", action="store_true", help="Print machine-readable diagnostics JSON")
    return parser


def doctor_cli(argv: list[str] | None = None) -> None:
    """Handle ``pytvt doctor`` diagnostics."""
    from . import diagnostics

    parser = _build_doctor_parser()
    args = parser.parse_args(argv)

    report = diagnostics(sdk_path=args.sdk_path)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report)

    if not report.sdk_available:
        sys.exit(1)


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
        help="Scan backend: protocol (pure Python, default), sdk (HTTP API), sdk-local (direct Python ctypes), both (protocol then sdk)",
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


def _build_connect_parser() -> argparse.ArgumentParser:
    """Construct the ``pytvt connect`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="pytvt connect",
        description="Connect to a single TVT device via direct SDK or AutoNAT",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--nat", action="store_true", help="Use SDK AutoNAT / P2P login")
    mode.add_argument("--direct", action="store_true", help="Force direct host/port login")
    parser.add_argument("--host", help="Direct IP/hostname for SDK login or fallback")
    parser.add_argument("--port", type=int, default=6036, help="Direct device SDK port (default: 6036)")
    parser.add_argument("--id", dest="identifier", help="Device serial / UID / cloud identifier")
    parser.add_argument("--user", "-u", dest="username", required=True, help="Login username")
    parser.add_argument("--password", "-p", required=True, help="Login password")
    parser.add_argument("--sdk-path", help="Path to libdvrnetsdk.so or vendor SDK root")
    parser.add_argument("--timeout", type=float, default=10.0, help="NAT handshake timeout in seconds")
    parser.add_argument("--nat-server", help="Override NAT/P2P server address for NAT20")
    parser.add_argument("--nat-port", type=int, help="Override NAT/P2P server port for NAT20")
    parser.add_argument(
        "--nat-type",
        choices=("nat", "nat20"),
        default="nat20",
        help="SDK NAT transport to use (default: nat20)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser


def _connect_main(argv: list[str] | None = None) -> None:
    """Handle ``pytvt connect``."""
    _load_dotenv()
    from .device_manager import DeviceManager

    parser = _build_connect_parser()
    args = parser.parse_args(argv)

    method = "nat" if args.nat or (args.identifier and not args.direct) else "direct"
    if method == "nat" and not args.identifier:
        parser.error("NAT connections require --id")
    if method == "direct" and not args.host:
        parser.error("Direct connections require --host")

    with DeviceManager(
        args.host,
        args.username,
        args.password,
        port=args.port,
        identifier=args.identifier,
        connection_method=method,
        sdk_path=args.sdk_path,
        timeout=max(1, int(args.timeout)),
        nat_server=args.nat_server,
        nat_port=args.nat_port,
        nat_type=args.nat_type,
    ) as manager:
        result = manager.device_info()

        payload = {
            "success": result.success,
            "backend": str(manager.backend),
            "connection_method": manager.connection_method,
            "host": manager.ip or None,
            "identifier": manager.identifier or None,
            "device_name": result.device_name,
            "device_model": result.device_model,
            "serial_number": result.serial_number,
            "firmware": result.firmware,
            "error": result.error,
        }

        if args.json:
            print(json.dumps(payload, indent=2))
        elif result.success:
            print(f"Connected via {manager.connection_method}/{manager.backend} to {manager.target}")
            print(json.dumps(payload, indent=2))
        else:
            print(
                f"Connection failed via {manager.connection_method}/{manager.backend} to {manager.target}: {result.error}",
                file=sys.stderr,
            )

        if not result.success:
            sys.exit(1)


def _build_connect_many_parser() -> argparse.ArgumentParser:
    """Construct the ``pytvt connect-many`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="pytvt connect-many",
        description="Connect to many TVT devices concurrently via direct SDK or AutoNAT",
    )
    parser.add_argument(
        "--file",
        "-f",
        required=True,
        help="JSON file with device list (each object needs at least ip or identifier)",
    )
    parser.add_argument("--user", "-u", dest="username", required=True, help="Login username")
    parser.add_argument("--password", "-p", required=True, help="Login password")
    parser.add_argument("--sdk-path", help="Path to libdvrnetsdk.so or vendor SDK root")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-device connection timeout in seconds")
    parser.add_argument("--concurrency", "-c", type=int, default=10, help="Max concurrent connections (default: 10)")
    parser.add_argument("--nat", action="store_true", help="Prefer NAT connections")
    parser.add_argument("--json", action="store_true", help="Print JSON output instead of table")
    return parser


def _connect_many_main(argv: list[str] | None = None) -> None:
    """Handle ``pytvt connect-many``."""
    from .connection_pool import connect_many

    parser = _build_connect_many_parser()
    args = parser.parse_args(argv)

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    raw = json.loads(file_path.read_text())
    if isinstance(raw, dict):
        raw = raw.get("devices", raw.get("data", [raw]))
    if not isinstance(raw, list):
        print("Expected a JSON array of device objects", file=sys.stderr)
        sys.exit(1)

    devices = [DeviceEntry.from_dict(d) for d in raw]
    if not devices:
        print("No devices found in file", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to {len(devices)} devices (concurrency={args.concurrency})...", file=sys.stderr)

    results = connect_many(
        devices,
        args.username,
        args.password,
        sdk_path=args.sdk_path,
        max_workers=args.concurrency,
        timeout=args.timeout,
        prefer_nat=args.nat,
    )

    if args.json:
        payload = [
            {
                "target": r.target,
                "success": r.success,
                "connection_method": r.connection_method,
                "latency_ms": r.latency_ms,
                "device_name": r.device_name,
                "serial_number": r.serial_number,
                "firmware": r.firmware,
                "error": r.error,
            }
            for r in results
        ]
        print(json.dumps(payload, indent=2))
    else:
        _print_connect_many_table(results)

    failed = sum(1 for r in results if not r.success)
    if failed:
        print(f"\n{failed}/{len(results)} connections failed.", file=sys.stderr)
        sys.exit(1)


def _print_connect_many_table(results: list) -> None:
    """Print a text table of connect-many results."""
    header = f"{'TARGET':<30} {'STATUS':<10} {'LATENCY':<10} {'METHOD':<10} {'DEVICE':<20} {'ERROR'}"
    print(header)
    print("-" * len(header))
    for r in results:
        status = "OK" if r.success else "FAIL"
        latency = f"{r.latency_ms}ms"
        device = r.device_name or r.serial_number or ""
        error = r.error or ""
        print(f"{r.target:<30} {status:<10} {latency:<10} {r.connection_method:<10} {device:<20} {error}")


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
