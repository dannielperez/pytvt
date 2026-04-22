#!/usr/bin/env python3
"""Disposable full-sequence live validator for pytvt.management.

Purpose:
- Exercise login, get_server_info, list_devices, get_device_statuses, and
  subscribe_alarms registration lifecycle against a real management server.

Outputs:
- JSON report in --output-dir with per-operation timings, summaries, errors,
  diagnostics, and semantic notes.

Reusable evidence captured:
- backend selection, SDK diagnostics, elapsed timings, success/failure modes,
  parsed result summaries, raw_data summaries, and ordering stability.

Maintenance note:
- This is the primary retained management validation entrypoint.
"""

from __future__ import annotations

import argparse
import json

from manifest_resolver import (
    ManifestNotFoundError,
    InvalidManifestError,
    SDKBinaryNotFoundError,
)
from sdk_comparator import compare_diagnostics_by_manifest, format_comparison_summary

from management_validation_lib import (
    attach_backend_diagnostics,
    base_report,
    build_parser,
    load_env,
    make_client,
    require_password,
    resolve_sdk_path,
    run_operation,
    to_plain_data,
    write_report,
)


def _active_sdk_session_handle(client: object) -> int | None:
    backend = getattr(client, "_backend", None)
    sdk_client = getattr(backend, "_client", None)
    handle = getattr(sdk_client, "_session_handle", None)
    return int(handle) if isinstance(handle, int) else None


def _format_device_id_for_log(value: str | None) -> str:
    if value is None:
        return "None"
    if value == "":
        return '""'
    return value


def _print_device_list(rows: list[dict[str, str]]) -> None:
    print("=== DEVICE LIST ===")
    for row in rows:
        print(row)


def classify_device_id(device_id: str, devices: list[dict]) -> dict:
    matches = [d for d in devices if str(d.get("device_sn") or "") == device_id]
    return {
        "device_id": device_id,
        "match_found": len(matches) > 0,
        "match_count": len(matches),
        "matched_devices": matches,
        "total_devices": len(devices),
    }


def _print_device_id_classification(classification: dict) -> None:
    print("=== DEVICE ID CLASSIFICATION ===")
    print(classification)


def _sdk_blockers_from_diagnostics(diag: dict) -> list[str]:
    blockers: list[str] = []
    backend = str(diag.get("backend") or "unknown")
    if backend != "native_linux_sdk":
        blockers.append(f"backend_not_native_linux_sdk: active backend is {backend}")

    if not diag.get("supports_login") and not diag.get("supports_login_ex"):
        blockers.append("backend_login_capability_missing: no confirmed login symbol path")

    structured = diag.get("sdk_not_ready_blockers") or []
    for item in structured:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        detail = item.get("detail")
        if code and detail:
            blockers.append(f"{code}: {detail}")

    # Backward-compatible fallback for older diagnostics payloads.
    if not blockers:
        if not diag.get("load_success"):
            blockers.append(f"sdk_load_failed: {diag.get('load_error')}")
        if "login_path_ready" in diag and not diag.get("login_path_ready"):
            blockers.append(f"sdk_login_not_ready: {diag.get('login_readiness_reason')}")
        if diag.get("architecture_compatible") is False:
            blockers.append(f"sdk_arch_mismatch: {diag.get('architecture_note')}")
    return blockers


def _ensure_sdk_ready_for_matrix(args, report: dict) -> bool:
    if not args.require_sdk:
        return True
    probe_client = make_client(args)
    diag = attach_backend_diagnostics(report, probe_client)
    blockers = _sdk_blockers_from_diagnostics(diag)
    if blockers:
        report["final_status"] = "sdk_not_ready"
        report["hard_blockers"] = blockers
        print(json.dumps({"final_status": report["final_status"], "hard_blockers": blockers}, indent=2))
        return False
    return True


def _run_single_login_test(args, device_id: str | None) -> tuple[dict, dict, list[dict[str, str]]]:
    client = make_client(args)
    op, _ = run_operation(
        "login",
        lambda did=device_id: client.login(args.username, args.password, device_id=did),
        "Controlled single-variable optional device_id injection experiment.",
    )
    handle = _active_sdk_session_handle(client)
    err = 0 if op.get("success") else op.get("error_code")
    print(f"[LOGIN TEST] device_id={_format_device_id_for_log(device_id)} -> handle={handle} err={err}")

    row = {
        "login_device_id": device_id,
        "handle": handle,
        "error_code": err,
        "success": op.get("success", False),
        "elapsed_ms": op.get("elapsed_ms"),
        "exception_type": op.get("exception_type"),
        "exception_message": op.get("exception_message"),
    }
    op["login_device_id"] = device_id
    op["handle"] = handle

    device_rows: list[dict[str, str]] = []
    if op.get("success"):
        full_devices = client.list_devices()
        op["full_devices"] = to_plain_data(full_devices)
        device_rows = client.list_devices_for_login_routing()
        _print_device_list(device_rows)
        op["device_list"] = device_rows
        row["device_list"] = device_rows

        if device_id is not None:
            classification = classify_device_id(device_id, device_rows)
            _print_device_id_classification(classification)
            op["device_id_classification"] = classification
            row["device_id_classification"] = classification

    close_op, _ = run_operation(
        "close",
        client.close,
        "Always validate session cleanup and logout behavior.",
    )
    return row, op, [close_op] if close_op else []


def _run_device_id_classification_matrix(args) -> int:
    report = base_report(args)
    report["sequence"] = ["device_id_classification_matrix", "client.close"]
    report["operations"] = []
    report["device_id_classification_matrix"] = []

    if not _ensure_sdk_ready_for_matrix(args, report):
        report_path = write_report(args.output_dir, "management_live_validation", report)
        print(report_path)
        return 2

    matrix_values: list[str] = ["123456"]

    first_row, first_op, first_close_ops = _run_single_login_test(args, "123456")
    report["device_id_classification_matrix"].append(first_row)
    report["operations"].append(first_op)
    report["operations"].extend(first_close_ops)

    baseline_devices = first_row.get("device_list") if isinstance(first_row.get("device_list"), list) else []
    first_device_sn = ""
    second_device_sn = ""
    if baseline_devices:
        first_device_sn = str(baseline_devices[0].get("device_sn") or "").strip()
    if len(baseline_devices) > 1:
        second_device_sn = str(baseline_devices[1].get("device_sn") or "").strip()

    if first_device_sn:
        matrix_values.append(first_device_sn)
    if second_device_sn:
        matrix_values.append(second_device_sn)

    for device_id in matrix_values[1:]:
        row, op, close_ops = _run_single_login_test(args, device_id)
        report["device_id_classification_matrix"].append(row)
        report["operations"].append(op)
        report["operations"].extend(close_ops)

    report["final_status"] = "success" if any(item.get("success") for item in report["device_id_classification_matrix"]) else "failed"
    report_path = write_report(args.output_dir, "management_live_validation", report)
    print(report_path)
    print(
        json.dumps(
            {
                "final_status": report["final_status"],
                "classification_matrix_count": len(report["device_id_classification_matrix"]),
            },
            indent=2,
        )
    )
    return 0


def _run_login_matrix(args) -> int:
    report = base_report(args)
    report["sequence"] = ["login_matrix", "client.close"]
    report["operations"] = []
    report["login_matrix"] = []
    matrix_values: list[str | None] = [None, "", "NVMS", "123456"]

    if not _ensure_sdk_ready_for_matrix(args, report):
        report_path = write_report(args.output_dir, "management_live_validation", report)
        print(report_path)
        return 2

    for device_id in matrix_values:
        client = make_client(args)
        op, _ = run_operation(
            "login",
            lambda did=device_id: client.login(args.username, args.password, device_id=did),
            "Controlled single-variable optional device_id injection experiment.",
        )
        handle = _active_sdk_session_handle(client)
        err = 0 if op.get("success") else op.get("error_code")
        print(f"[LOGIN TEST] device_id={_format_device_id_for_log(device_id)} -> handle={handle} err={err}")

        row = {
            "login_device_id": device_id,
            "handle": handle,
            "error_code": err,
            "success": op.get("success", False),
            "elapsed_ms": op.get("elapsed_ms"),
            "exception_type": op.get("exception_type"),
            "exception_message": op.get("exception_message"),
        }
        report["login_matrix"].append(row)
        op["login_device_id"] = device_id
        op["handle"] = handle
        if op.get("success"):
            device_rows = client.list_devices_for_login_routing()
            _print_device_list(device_rows)
            op["device_list"] = device_rows
            row["device_list"] = device_rows
            row["matching_device_sn"] = next(
                (
                    item.get("device_sn")
                    for item in device_rows
                    if device_id is not None and str(item.get("device_sn") or "").strip() == device_id
                ),
                None,
            )
        report["operations"].append(op)

        close_op, _ = run_operation(
            "close",
            client.close,
            "Always validate session cleanup and logout behavior.",
        )
        report["operations"].append(close_op)

    report["final_status"] = "success" if any(item.get("success") for item in report["login_matrix"]) else "failed"
    report_path = write_report(args.output_dir, "management_live_validation", report)
    print(report_path)
    print(json.dumps({"final_status": report["final_status"], "login_matrix_count": len(report["login_matrix"])}, indent=2))
    return 0


def _build_symbol_payload_for_sdk(
    args: argparse.Namespace,
    *,
    manifest_id: str,
    sdk_path: str,
    artifact_metadata: dict,
) -> dict:
    probe_args = argparse.Namespace(**vars(args))
    probe_args.sdk_path = sdk_path
    probe_args.sdk_manifest_id = manifest_id

    client = make_client(probe_args)
    diagnostics = client.get_sdk_diagnostics()
    symbol_inventory = diagnostics.get("symbol_inventory", [])

    payload = {
        "captured_at": base_report(probe_args)["captured_at"],
        "manifest_id": manifest_id,
        "sdk_path": sdk_path,
        "backend": diagnostics.get("backend"),
        "context": diagnostics.get(
            "context",
            {
                "platform": diagnostics.get("platform", {}),
                "sdk": diagnostics.get("sdk", {}),
                "product_scope": diagnostics.get("product_scope", []),
                "capabilities": diagnostics.get("capabilities", {}),
                "notes": diagnostics.get("notes", []),
            },
        ),
        "symbol_probe": diagnostics.get("symbol_probe", {}),
        "symbol_inventory": symbol_inventory if isinstance(symbol_inventory, list) else [],
        "symbol_inventory_summary": {
            "count": len(symbol_inventory) if isinstance(symbol_inventory, list) else 0,
            "preview": (symbol_inventory[:30] if isinstance(symbol_inventory, list) else []),
        },
        "symbol_presence_checks": diagnostics.get("symbol_presence_checks", []),
        "windows_symbol_parity": diagnostics.get("windows_symbol_parity", []),
        "capability_evidence": diagnostics.get("capability_evidence", {}),
    }
    if artifact_metadata:
        payload["artifact"] = artifact_metadata
    return payload


def _run_compare_sdk_manifests(args: argparse.Namespace) -> int:
    manifest_ids = list(args.compare_sdk_manifests or [])
    if len(manifest_ids) < 2:
        print("ERROR: --compare-sdk-manifests requires at least 2 manifest IDs")
        return 2

    results: dict[str, dict] = {}
    for manifest_id in manifest_ids:
        try:
            probe_args = argparse.Namespace(**vars(args))
            probe_args.compare_sdk_manifests = None
            probe_args.sdk_manifest_id = manifest_id
            probe_args.sdk_path = ""
            sdk_path, artifact_metadata = resolve_sdk_path(probe_args)
        except (ManifestNotFoundError, InvalidManifestError, SDKBinaryNotFoundError, ValueError) as exc:
            print(f"ERROR resolving manifest {manifest_id}: {type(exc).__name__}: {exc}")
            return 1

        results[manifest_id] = _build_symbol_payload_for_sdk(
            args,
            manifest_id=manifest_id,
            sdk_path=sdk_path,
            artifact_metadata=artifact_metadata,
        )

    comparison = compare_diagnostics_by_manifest(manifest_ids, results).payload
    comparison["results"] = {manifest_id: results[manifest_id] for manifest_id in manifest_ids}

    report_path = write_report(args.output_dir, "management_sdk_comparison", comparison)
    print(report_path)
    print(json.dumps(comparison, indent=2, sort_keys=True))
    print(format_comparison_summary(comparison))
    return 0


def main() -> int:
    parser = build_parser("Run a full live validation sequence against a TVT management server")
    parser.add_argument("--subscribe-alarms", action="store_true", default=True)
    parser.add_argument("--no-subscribe-alarms", dest="subscribe_alarms", action="store_false")
    parser.add_argument("--require-sdk", action="store_true", default=True)
    parser.add_argument("--allow-non-sdk", dest="require_sdk", action="store_false")
    parser.add_argument(
        "--sdk-device-id-matrix",
        action="store_true",
        help="Run a controlled login-only matrix over device_id values: None, '', NVMS, 123456.",
    )
    parser.add_argument(
        "--sdk-device-id-classification-matrix",
        action="store_true",
        help=(
            "Run device_id meaning classification matrix: 123456, then first and second enumerated device_sn values "
            "from the successful 123456 session."
        ),
    )
    args = parser.parse_args()

    load_env(args.env_file)
    args.host = args.host

    if args.compare_sdk_manifests:
        return _run_compare_sdk_manifests(args)

    require_password(args)

    # Resolve SDK path (manifest or direct)
    artifact_metadata = {}
    try:
        resolved_path, artifact_metadata = resolve_sdk_path(args)
        args.sdk_path = resolved_path
    except (ManifestNotFoundError, InvalidManifestError, SDKBinaryNotFoundError) as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        return 1

    if args.dump_sdk_symbols:
        client = make_client(args)
        diagnostics = client.get_backend_diagnostics()
        symbol_inventory = diagnostics.get("symbol_inventory", [])
        payload = {
            "captured_at": base_report(args)["captured_at"],
            "sdk_path": args.sdk_path,
            "backend": diagnostics.get("backend"),
            "context": diagnostics.get("context", {
                "platform": diagnostics.get("platform", {}),
                "sdk": diagnostics.get("sdk", {}),
                "product_scope": diagnostics.get("product_scope", []),
                "capabilities": diagnostics.get("capabilities", {}),
                "notes": diagnostics.get("notes", []),
            }),
            "symbol_probe": diagnostics.get("symbol_probe", {}),
            "symbol_inventory_summary": {
                "count": len(symbol_inventory) if isinstance(symbol_inventory, list) else 0,
                "preview": (symbol_inventory[:30] if isinstance(symbol_inventory, list) else []),
            },
            "symbol_presence_checks": diagnostics.get("symbol_presence_checks", []),
            "windows_symbol_parity": diagnostics.get("windows_symbol_parity", []),
            "capability_evidence": diagnostics.get("capability_evidence", {}),
        }
        # Include artifact metadata if manifest was used
        if artifact_metadata:
            payload["artifact"] = artifact_metadata
        report_path = write_report(args.output_dir, "management_sdk_symbols", payload)
        print(report_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.sdk_device_id_classification_matrix:
        return _run_device_id_classification_matrix(args)

    if args.sdk_device_id_matrix:
        return _run_login_matrix(args)

    client = make_client(args)
    report = base_report(args)
    report["sequence"] = [
        "login",
        "get_server_info",
        "list_devices",
        "get_device_statuses",
        "subscribe_alarms",
        "close_alarm_subscription",
        "client.close",
    ]
    attach_backend_diagnostics(report, client, artifact_metadata=artifact_metadata)
    report["operations"] = []

    if args.require_sdk:
        diag = report["backend_diagnostics_prelogin"]
        blockers = _sdk_blockers_from_diagnostics(diag)
        if blockers:
            report["final_status"] = "sdk_not_ready"
            report["hard_blockers"] = blockers
            report_path = write_report(args.output_dir, "management_live_validation", report)
            print(report_path)
            print(json.dumps({"final_status": report["final_status"], "hard_blockers": blockers}, indent=2))
            return 2

    subscription = None
    try:
        op, _ = run_operation(
            "login",
            lambda: client.login(args.username, args.password, device_id=args.sdk_device_id),
            "Establish session on the selected backend family and freeze selection.",
        )
        login_backend = report["backend_diagnostics_prelogin"].get("login_backend")
        if isinstance(login_backend, dict):
            op["login_mode"] = login_backend.get("mode")
            op["login_connect_type"] = login_backend.get("connect_type")
            op["login_connect_type_code"] = login_backend.get("connect_type_code")
            op["login_function_used"] = login_backend.get("symbol_name")
        op["login_device_id"] = args.sdk_device_id
        op["handle"] = _active_sdk_session_handle(client)
        print(
            f"[LOGIN TEST] device_id={_format_device_id_for_log(args.sdk_device_id)} -> "
            f"handle={op.get('handle')} err={0 if op.get('success') else op.get('error_code')}"
        )
        if op.get("success"):
            device_rows = client.list_devices_for_login_routing()
            _print_device_list(device_rows)
            op["device_list"] = device_rows
            op["matching_device_sn"] = next(
                (
                    item.get("device_sn")
                    for item in device_rows
                    if args.sdk_device_id is not None and str(item.get("device_sn") or "").strip() == args.sdk_device_id
                ),
                None,
            )
        report["operations"].append(op)
        report["backend_selected"] = client.backend_name
        if not op["success"]:
            report["final_status"] = "login_failed"
            report_path = write_report(args.output_dir, "management_live_validation", report)
            print(report_path)
            return 1

        for name, func, note in [
            (
                "get_server_info",
                client.get_server_info,
                "Validate current server-info assumptions against live payload shape.",
            ),
            (
                "list_devices",
                client.list_devices,
                "Check whether configured IPC inventory semantics still match live rows.",
            ),
            (
                "get_device_statuses",
                client.get_device_statuses,
                "Check channel-connectivity status rows and identifier alignment.",
            ),
        ]:
            op, result = run_operation(name, func, note)
            if result is not None:
                op["plain_result"] = to_plain_data(result)
            report["operations"].append(op)

        if args.subscribe_alarms:
            op, subscription = run_operation(
                "subscribe_alarms",
                client.subscribe_alarms,
                "Validate alarm-channel registration lifecycle only; payload semantics remain opaque.",
            )
            if subscription is not None:
                op["plain_result"] = to_plain_data(subscription)
            report["operations"].append(op)

            if subscription is not None:
                close_op, _ = run_operation(
                    "close",
                    subscription.close,
                    "Validate alarm-channel teardown via NET_SDK_CloseAlarmChan.",
                )
                report["operations"].append(close_op)
    finally:
        close_op, _ = run_operation(
            "close",
            client.close,
            "Always validate session cleanup and logout behavior.",
        )
        report["operations"].append(close_op)

    report["backend_diagnostics_postrun"] = client.get_backend_diagnostics()
    failures = [op for op in report["operations"] if not op.get("success")]
    report["final_status"] = "success" if not failures else "failed"
    report_path = write_report(args.output_dir, "management_live_validation", report)
    print(report_path)
    print(json.dumps({"final_status": report["final_status"], "backend_selected": report.get("backend_selected")}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())