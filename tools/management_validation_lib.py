#!/usr/bin/env python3
"""Shared helpers for disposable pytvt management live-validation tools.

Purpose:
- Run structured live validation against a real TVT management server.
- Emit machine-readable reports without adding app-specific behavior to pytvt.

Maintenance note:
- This module is intentionally retained as shared infrastructure for
    management validation tooling.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
import traceback
from typing import Any, Callable

import dotenv

from manifest_resolver import (
    ManifestNotFoundError,
    InvalidManifestError,
    SDKBinaryNotFoundError,
    extract_artifact_metadata,
    resolve_manifest,
)

from pytvt.platform_sdk import AlarmSubscription, CapabilityNotAvailable, ManagementClient
from pytvt.device_sdk.loader import LEGACY_SDK_PATH_ENV_VAR, SDK_PATH_ENV_VAR

ERROR_CODE_RE = re.compile(r"error_code=(\d+)")

FUNCTIONS_BY_OPERATION = {
    "login": ["NET_SDK_Init", "NET_SDK_Login", "NET_SDK_LoginEx", "NET_SDK_Logout"],
    "get_server_info": ["NET_SDK_GetDeviceInfo"],
    "list_devices": ["NET_SDK_GetDeviceIPCInfo"],
    "get_device_statuses": ["NET_SDK_GetDeviceCHStatus"],
    "subscribe_alarms": ["NET_SDK_SetupAlarmChan", "NET_SDK_CloseAlarmChan"],
    "close": ["NET_SDK_CloseAlarmChan", "NET_SDK_Logout"],
}


def load_env(env_file: str | None) -> None:
    dotenv.load_dotenv(env_file or None)


def resolve_sdk_path(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Resolve SDK path and return (sdk_path, artifact_metadata).
    
    If --sdk-manifest-id is provided, use manifest resolver.
    Otherwise, fall back to --sdk-path argument.
    
    Returns:
        (sdk_path, artifact_metadata) where artifact_metadata is empty dict if no manifest.
    
    Raises:
        ManifestNotFoundError, InvalidManifestError, SDKBinaryNotFoundError
    """
    artifact_metadata: dict[str, Any] = {}

    if getattr(args, "compare_sdk_manifests", None):
        raise ValueError(
            "resolve_sdk_path cannot be used with --compare-sdk-manifests"
        )
    
    if args.sdk_manifest_id:
        # Resolve via manifest
        inventory_root = args.sdk_inventory_root or None
        resolution = resolve_manifest(args.sdk_manifest_id, inventory_root)
        artifact_metadata = extract_artifact_metadata(resolution["manifest"])
        return resolution["sdk_path"], artifact_metadata
    
    # Fall back to direct path
    if not args.sdk_path:
        raise ValueError(
            "Must provide either --sdk-manifest-id or --sdk-path"
        )
    
    return args.sdk_path, artifact_metadata


def default_port() -> int:
    value = os.environ.get("TVT_MGMT_PORT") or os.environ.get("TVT_PORT") or "6003"
    return int(value)


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--env-file", default=None, help="Optional .env file to load before reading arguments")
    parser.add_argument("--host", default=os.environ.get("TVT_HOST", "192.0.2.10"))
    parser.add_argument("--port", type=int, default=default_port())
    parser.add_argument("--username", default=os.environ.get("TVT_USERNAME", "admin"))
    parser.add_argument("--password", default=os.environ.get("TVT_PASSWORD", ""))
    sdk_source_group = parser.add_mutually_exclusive_group()
    sdk_source_group.add_argument(
        "--sdk-path",
        default=os.environ.get(SDK_PATH_ENV_VAR) or os.environ.get(LEGACY_SDK_PATH_ENV_VAR) or "",
    )
    sdk_source_group.add_argument(
        "--sdk-manifest-id",
        default=None,
        help="Resolve SDK via tvt-sdk inventory manifest ID (e.g., tvt-windows-mgmt-20260401-v2.1.0)",
    )
    sdk_source_group.add_argument(
        "--compare-sdk-manifests",
        nargs="+",
        default=None,
        metavar="MANIFEST_ID",
        help=(
            "Resolve and compare multiple SDK manifests in one run "
            "(mutually exclusive with --sdk-path and --sdk-manifest-id)"
        ),
    )
    parser.add_argument(
        "--sdk-inventory-root",
        default=os.environ.get("TVT_SDK_INVENTORY_ROOT") or os.environ.get("TVT_SDK_REPO_ROOT") or "",
        help="Root directory of tvt-sdk inventory (default: env TVT_SDK_INVENTORY_ROOT or TVT_SDK_REPO_ROOT)",
    )
    parser.add_argument(
        "--sdk-login-mode",
        choices=("login", "login_ex"),
        default=os.environ.get("TVT_SDK_LOGIN_MODE", "login"),
        help="Deterministic SDK login function selection for validation runs",
    )
    parser.add_argument(
        "--sdk-connect-type",
        choices=("tcp", "nat", "nat20"),
        default=os.environ.get("TVT_SDK_CONNECT_TYPE", "tcp"),
        help="Deterministic NET_SDK_LoginEx connect type for login-path experiments",
    )
    parser.add_argument(
        "--backend-mode",
        choices=("auto", "native_linux_sdk", "native_protocol", "sidecar"),
        default=os.environ.get("PYTVT_MGMT_BACKEND_MODE", "auto"),
        help="Deterministic backend-family selection for validation runs",
    )
    parser.add_argument(
        "--sidecar-command",
        default=os.environ.get("PYTVT_MGMT_SIDECAR_CMD", ""),
        help="Shell command prefix for sidecar bridge backend (sidecar mode)",
    )
    parser.add_argument(
        "--dump-sdk-symbols",
        action="store_true",
        help=(
            "Emit SDK symbol inventory, parity report, capability evidence, and "
            "normalized context, then exit."
        ),
    )
    parser.add_argument(
        "--sdk-device-id",
        default=None,
        help=(
            "Optional NET_SDK_LoginEx deviceSN/UID argument. "
            "Use an empty string to send empty bytes explicitly."
        ),
    )
    parser.add_argument("--prefer-sdk", action="store_true", default=True)
    parser.add_argument("--no-prefer-sdk", dest="prefer_sdk", action="store_false")
    parser.add_argument(
        "--output-dir",
        default="tools/out/management-validation",
        help="Directory where JSON validation reports will be written",
    )
    return parser


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        "keys": sorted(mapping.keys())[:25],
        "key_count": len(mapping),
        "preview": {key: summarize_scalar(mapping[key]) for key in list(mapping)[:8]},
    }


def summarize_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        return {"type": "dict", "key_count": len(value), "keys": sorted(value.keys())[:8]}
    if isinstance(value, list):
        return {"type": "list", "length": len(value), "preview": [summarize_scalar(item) for item in value[:3]]}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def to_plain_data(value: Any) -> Any:
    if isinstance(value, AlarmSubscription):
        return {
            "handle": value.handle,
            "transport": value.transport,
            "raw_data": value.raw_data,
            "is_active": getattr(value, "is_active", None),
        }
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {key: to_plain_data(item) for key, item in value.items()}
    return value


def summarize_result(value: Any) -> dict[str, Any]:
    plain = to_plain_data(value)
    if isinstance(plain, dict):
        raw_data = plain.get("raw_data")
        return {
            "kind": "mapping",
            "fields": sorted(plain.keys()),
            "raw_data_summary": summarize_mapping(raw_data) if isinstance(raw_data, dict) else None,
            "preview": {key: summarize_scalar(plain[key]) for key in list(plain)[:8]},
        }
    if isinstance(plain, list):
        first = plain[0] if plain else None
        raw_data_summary = None
        if isinstance(first, dict) and isinstance(first.get("raw_data"), dict):
            raw_data_summary = summarize_mapping(first["raw_data"])
        return {
            "kind": "list",
            "length": len(plain),
            "first_item_preview": summarize_scalar(first),
            "first_item_raw_data_summary": raw_data_summary,
        }
    return {"kind": type(plain).__name__, "value": summarize_scalar(plain)}


def extract_error_code(message: str) -> int | None:
    match = ERROR_CODE_RE.search(message)
    if match is None:
        return None
    return int(match.group(1))


def run_operation(name: str, func: Callable[[], Any], semantic_note: str) -> tuple[dict[str, Any], Any]:
    started = time.perf_counter()
    op_report: dict[str, Any] = {
        "operation": name,
        "started_at": iso_now(),
        "sdk_functions": FUNCTIONS_BY_OPERATION.get(name, []),
        "semantic_note": semantic_note,
    }
    try:
        result = func()
    except Exception as exc:
        op_report.update(
            {
                "success": False,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "error_code": extract_error_code(str(exc)),
                "traceback": traceback.format_exc(),
            }
        )
        return op_report, None

    op_report.update(
        {
            "success": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "result_summary": summarize_result(result),
        }
    )
    return op_report, result


def write_report(output_dir: str, prefix: str, payload: dict[str, Any]) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = out_dir / f"{prefix}_{timestamp}.json"
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return report_path


def base_report(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "captured_at": iso_now(),
        "target": {
            "host": args.host,
            "port": args.port,
            "sdk_path": args.sdk_path,
            "prefer_sdk": args.prefer_sdk,
            "sdk_login_mode": args.sdk_login_mode,
            "sdk_connect_type": args.sdk_connect_type,
            "sdk_device_id": args.sdk_device_id,
            "backend_mode": args.backend_mode,
            "sidecar_command_configured": bool(args.sidecar_command),
        },
        "tooling": {
            "env_file": args.env_file,
        },
    }


def make_client(args: argparse.Namespace) -> ManagementClient:
    return ManagementClient(
        args.host,
        port=args.port,
        sdk_path=args.sdk_path or None,
        prefer_sdk=args.prefer_sdk,
        sdk_login_mode=args.sdk_login_mode,
        sdk_connect_type=args.sdk_connect_type,
        backend_mode=args.backend_mode,
        sidecar_command=args.sidecar_command or None,
    )


def attach_backend_diagnostics(
    report: dict[str, Any],
    client: ManagementClient,
    *,
    key: str = "backend_diagnostics_prelogin",
    artifact_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach backend diagnostics and normalized SDK context to a report.
    
    optionally includes artifact metadata from manifest resolution.
    """
    diagnostics = client.get_backend_diagnostics()
    report[key] = diagnostics
    
    # Attach artifact metadata if provided
    if artifact_metadata:
        report["artifact"] = artifact_metadata
    
    report["backend_context"] = diagnostics.get(
        "context",
        {
            "platform": diagnostics.get("platform", {}),
            "sdk": diagnostics.get("sdk", {}),
            "product_scope": diagnostics.get("product_scope", []),
            "capabilities": diagnostics.get("capabilities", {}),
            "notes": diagnostics.get("notes", []),
        },
    )
    return diagnostics


def require_password(args: argparse.Namespace) -> None:
    if not args.password:
        raise SystemExit("TVT_PASSWORD or --password is required for live validation")