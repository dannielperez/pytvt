"""Live PlatformSDK inventory validator.

Logs into a TVT management server using the PlatformSDK backend and prints
counts + small samples for every read-only capability pytvt exposes.  Useful
for smoke-testing new backend methods against a real server without touching
anything destructive.

Configuration is loaded from environment variables (optionally sourced from a
``.env`` file in the working directory):

    TVT_MGMT_IP          management-server host (required)
    TVT_MGMT_PORT        management-server port (default 6003)
    TVT_MGMT_USERNAME    login user (required)
    TVT_MGMT_PASSWORD    login password (required; never echoed)
    TVT_PLATFORM_SDK     absolute path to libPlatClientSDK.so / .dll (required)

Any of the above may be overridden on the command line.  Passwords are only
accepted via environment / .env — never as a CLI flag — to avoid shell history
leakage.

Example
-------
    # With a .env file in the current directory
    python tools/validate_platform_inventory.py --json --sample 3

    # With explicit overrides
    TVT_MGMT_PASSWORD=... python tools/validate_platform_inventory.py \\
        --host 10.0.0.1 --sdk /opt/tvtsdk/libPlatClientSDK.so

Sensitive keys in raw payloads are redacted via ``redact_sensitive``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a declared dep
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

from pytvt.platform_sdk import ManagementClient
from pytvt.platform_sdk.exceptions import CapabilityNotAvailable
from pytvt.platform_sdk.platform_constants import redact_sensitive
from pytvt.platform_sdk.inventory import get_platform_inventory_snapshot


def _asdict(obj: Any) -> Any:
    """Recursively convert dataclasses/objects to plain dicts for JSON."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _asdict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _asdict(v) for k, v in redact_sensitive(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_asdict(v) for v in obj]
    return obj


def _safe_call(callable_: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return {"ok": bool, "value": ..., "error": str} for a single read call."""
    try:
        value = callable_(*args, **kwargs)
    except CapabilityNotAvailable as exc:
        return {"ok": False, "error": f"capability_not_available: {exc}"}
    except Exception as exc:  # pragma: no cover - runtime-only
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "value": value}


def build_report(client: ManagementClient, sample: int) -> dict[str, Any]:
    """Build the inventory report.  Call counts are capped at ``sample`` items."""

    def summarize(result: dict[str, Any]) -> dict[str, Any]:
        if not result["ok"]:
            return result
        value = result["value"]
        if isinstance(value, list):
            return {
                "ok": True,
                "count": len(value),
                "sample": _asdict(value[:sample]),
            }
        return {"ok": True, "value": _asdict(value)}

    report: dict[str, Any] = {
        "server": {
            "host": client.host,
            "port": client.port,
            "backend": client.backend_name,
        },
        "server_info": summarize(_safe_call(client.get_server_info)),
        "resources": {
            "areas": summarize(_safe_call(client.list_areas)),
            "devices": summarize(_safe_call(client.list_devices_normalized)),
            "channels": summarize(_safe_call(client.list_channels_normalized)),
            "raw_total": summarize(_safe_call(client.list_resources)),
        },
        "servers": {
            "all": summarize(_safe_call(client.list_servers)),
            "transfer": summarize(_safe_call(client.list_transfer_servers)),
            "storage": summarize(_safe_call(client.list_storage_servers)),
            "alarm_host": summarize(_safe_call(client.list_alarm_servers)),
            "intelligent_analysis": summarize(
                _safe_call(client.list_intelligent_analysis_servers)
            ),
            "access": summarize(_safe_call(client.list_access_servers)),
            "tv_wall": summarize(_safe_call(client.list_tv_wall_servers)),
            "connect_events": summarize(
                _safe_call(client.list_server_connection_events)
            ),
        },
        "alarms": {
            "zones": summarize(_safe_call(client.list_alarm_zones)),
            "events": summarize(_safe_call(client.list_alarm_events)),
            "active": summarize(_safe_call(client.list_active_alarms)),
        },
        "accounts": {
            "users": summarize(_safe_call(client.list_users)),
            "permission_groups": summarize(_safe_call(client.list_permission_groups)),
        },
        "logs": {
            "all": summarize(_safe_call(client.list_logs)),
            "alarm": summarize(_safe_call(client.list_alarm_logs)),
            "operation": summarize(_safe_call(client.list_operation_logs)),
            "exception": summarize(_safe_call(client.list_exception_logs)),
        },
        "tv_walls": summarize(_safe_call(client.list_tv_walls)),
    }
    return report


def print_human(report: dict[str, Any]) -> None:
    print(f"PlatformSDK inventory for {report['server']['host']}:{report['server']['port']}")
    print(f"  backend: {report['server']['backend']}")
    print()

    def line(label: str, result: dict[str, Any]) -> None:
        if not result.get("ok"):
            print(f"  {label:<36}  skipped  ({result.get('error', '?')})")
            return
        if "count" in result:
            print(f"  {label:<36}  count={result['count']}")
        else:
            print(f"  {label:<36}  ok")

    print("Resources:")
    for k, v in report["resources"].items():
        line(k, v)
    print()
    print("Servers:")
    for k, v in report["servers"].items():
        line(k, v)
    print()
    print("Alarms:")
    for k, v in report["alarms"].items():
        line(k, v)
    print()
    print("Accounts:")
    for k, v in report["accounts"].items():
        line(k, v)
    print()
    print("Logs:")
    for k, v in report["logs"].items():
        line(k, v)
    print()
    line("tv_walls", report["tv_walls"])


def _print_snapshot_human(snapshot: dict[str, Any], args: Any) -> None:
    print()
    print("=== Inventory Snapshot ===")
    if args.summary:
        s = snapshot["summary"]
        print(
            f"  sites={s['site_count']} devices={s['device_count']} "
            f"channels={s['channel_count']}"
        )
        print(
            f"  health: online={s['online_devices']} "
            f"degraded={s['degraded_devices']} offline={s['offline_devices']}"
        )
        caps = snapshot["capabilities"]
        cap_str = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in caps.items())
        print(f"  capabilities: {cap_str}")
    if args.topology:
        print("  topology:")
        for site in snapshot["sites"]:
            print(
                f"    - {site['name']} (id={site['id']}) "
                f"devices={len(site['devices'])} channels={len(site['channels'])} "
                f"zones={len(site['alarm_zones'])}"
            )
    if args.health:
        print("  health (first 10):")
        for h in snapshot["health"][:10]:
            issues = ",".join(h["issues"]) or "-"
            print(
                f"    - {h['device_name']} [{h['status']}] "
                f"chan {h['online_channels']}/{h['total_channels']} "
                f"site={h['site_id']} issues={issues}"
            )
    if args.alarms:
        print(f"  alarm_events: {len(snapshot['alarm_events'])}")
        for e in snapshot["alarm_events"][:10]:
            print(
                f"    - {e['type']} ({e['severity']}) "
                f"device={e['device_guid']} ts={e['timestamp']}"
            )


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # safe no-op if no .env is present

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default=os.getenv("TVT_MGMT_IP"),
        help="Management server host (env: TVT_MGMT_IP)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("TVT_MGMT_PORT", "6003")),
        help="Management server port (env: TVT_MGMT_PORT, default 6003)",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("TVT_MGMT_USERNAME"),
        help="Login username (env: TVT_MGMT_USERNAME)",
    )
    parser.add_argument(
        "--sdk",
        default=os.getenv("TVT_PLATFORM_SDK"),
        help="Absolute path to libPlatClientSDK.so / PlatClientSDK.dll "
        "(env: TVT_PLATFORM_SDK)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=3,
        help="Max number of rows to include per list in the sample output",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument(
        "--topology",
        action="store_true",
        help="Include per-site topology in output (uses platform_inventory snapshot)",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Include per-device health in output",
    )
    parser.add_argument(
        "--alarms",
        action="store_true",
        help="Include normalized alarm events in output",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Include high-level inventory summary",
    )
    args = parser.parse_args(argv)

    password = os.getenv("TVT_MGMT_PASSWORD")
    missing = [
        name
        for name, value in (
            ("host (--host / TVT_MGMT_IP)", args.host),
            ("username (--username / TVT_MGMT_USERNAME)", args.username),
            ("password (TVT_MGMT_PASSWORD)", password),
            ("sdk (--sdk / TVT_PLATFORM_SDK)", args.sdk),
        )
        if not value
    ]
    if missing:
        print(
            "missing required configuration: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    client = ManagementClient(
        args.host,
        port=args.port,
        backend_mode="platform_sdk",
        platform_sdk_path=args.sdk,
    )
    try:
        client.login(args.username, password)
    except Exception as exc:  # pragma: no cover
        print(f"login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    try:
        report = build_report(client, sample=max(0, args.sample))
        snapshot: dict[str, Any] | None = None
        if args.topology or args.health or args.alarms or args.summary:
            snapshot = get_platform_inventory_snapshot(client)
    finally:
        client.close()

    if snapshot is not None:
        extras: dict[str, Any] = {}
        if args.summary:
            extras["summary"] = snapshot["summary"]
            extras["capabilities"] = snapshot["capabilities"]
        if args.topology:
            extras["sites"] = snapshot["sites"]
        if args.health:
            extras["health"] = snapshot["health"]
        if args.alarms:
            extras["alarm_events"] = snapshot["alarm_events"]
            extras["alarm_zones"] = snapshot["alarm_zones"]
        report["snapshot"] = extras

    if args.json:
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print_human(report)
        if snapshot is not None:
            _print_snapshot_human(snapshot, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
