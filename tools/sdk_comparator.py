#!/usr/bin/env python3
"""Evidence-driven multi-SDK comparison helpers.

Consumes existing diagnostics output emitted by pytvt management tooling and
builds deterministic comparison reports without inferring new semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True)
class SDKComparisonReport:
    payload: dict[str, Any]


def _symbol_set_from_diagnostics(diagnostics: dict[str, Any]) -> set[str]:
    symbols: set[str] = set()
    inventory = diagnostics.get("symbol_inventory")
    if isinstance(inventory, list):
        for item in inventory:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            present = item.get("present")
            if isinstance(name, str) and (present is True or present is None):
                symbols.add(name)

    presence_checks = diagnostics.get("symbol_presence_checks")
    if isinstance(presence_checks, list):
        for item in presence_checks:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and bool(item.get("present")):
                symbols.add(name)
    return symbols


def _extract_capability_status(evidence: Any) -> str:
    if not isinstance(evidence, dict):
        return "unknown"

    confirmed = evidence.get("confirmed")
    if isinstance(confirmed, bool):
        return "true" if confirmed else "false"

    # Some backends may encode non-boolean status (for example, provisional)
    # under explicit keys. Keep this evidence-driven and avoid semantic mapping.
    status = evidence.get("status")
    if isinstance(status, str) and status:
        return status

    return "unknown"


def _flatten_context(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key in sorted(value.keys()):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            _flatten_context(child_prefix, value[key], out)
        return
    out[prefix] = value


def _normalized_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def compare_diagnostics_by_manifest(
    manifest_ids: list[str],
    diagnostics_by_manifest: dict[str, dict[str, Any]],
) -> SDKComparisonReport:
    """Build a deterministic multi-SDK comparison report.

    The diagnostics payload for each SDK is the source of truth.
    """
    sdk_ids = [item for item in manifest_ids if item in diagnostics_by_manifest]

    symbol_sets = {
        sdk_id: _symbol_set_from_diagnostics(diagnostics_by_manifest[sdk_id])
        for sdk_id in sdk_ids
    }
    all_symbols = sorted({symbol for items in symbol_sets.values() for symbol in items})
    symbol_diff: list[dict[str, Any]] = []
    for symbol in all_symbols:
        present_in = sorted([sdk_id for sdk_id in sdk_ids if symbol in symbol_sets[sdk_id]])
        missing_in = sorted([sdk_id for sdk_id in sdk_ids if symbol not in symbol_sets[sdk_id]])
        if present_in and missing_in:
            symbol_diff.append(
                {
                    "symbol": symbol,
                    "present_in": present_in,
                    "missing_in": missing_in,
                }
            )

    capability_names = sorted(
        {
            capability
            for sdk_id in sdk_ids
            for capability in (
                diagnostics_by_manifest[sdk_id].get("capability_evidence", {}).keys()
                if isinstance(diagnostics_by_manifest[sdk_id].get("capability_evidence"), dict)
                else []
            )
        }
    )
    capability_diff: list[dict[str, Any]] = []
    for capability in capability_names:
        true_in: list[str] = []
        false_in: list[str] = []
        unknown_in: list[str] = []
        custom_status: dict[str, list[str]] = {}

        for sdk_id in sdk_ids:
            evidence_map = diagnostics_by_manifest[sdk_id].get("capability_evidence", {})
            evidence = evidence_map.get(capability) if isinstance(evidence_map, dict) else None
            status = _extract_capability_status(evidence)
            if status == "true":
                true_in.append(sdk_id)
            elif status == "false":
                false_in.append(sdk_id)
            elif status == "unknown":
                unknown_in.append(sdk_id)
            else:
                custom_status.setdefault(status, []).append(sdk_id)

        status_buckets: list[tuple[str, list[str]]] = []
        if true_in:
            status_buckets.append(("true", true_in))
        if false_in:
            status_buckets.append(("false", false_in))
        if unknown_in:
            status_buckets.append(("unknown", unknown_in))
        for name in sorted(custom_status.keys()):
            status_buckets.append((name, sorted(custom_status[name])))

        if len(status_buckets) > 1:
            item: dict[str, Any] = {
                "capability": capability,
                "true_in": sorted(true_in),
                "false_in": sorted(false_in),
            }
            if unknown_in:
                item["unknown_in"] = sorted(unknown_in)
            for name in sorted(custom_status.keys()):
                item[f"{name}_in"] = sorted(custom_status[name])
            capability_diff.append(item)

    flat_context_by_sdk: dict[str, dict[str, Any]] = {}
    for sdk_id in sdk_ids:
        context = diagnostics_by_manifest[sdk_id].get("context")
        flattened: dict[str, Any] = {}
        if isinstance(context, dict):
            _flatten_context("", context, flattened)
        flat_context_by_sdk[sdk_id] = flattened

    context_fields = sorted({field for flat in flat_context_by_sdk.values() for field in flat.keys()})
    context_diff: list[dict[str, Any]] = []
    for field in context_fields:
        values: dict[str, Any] = {
            sdk_id: flat_context_by_sdk[sdk_id].get(field)
            for sdk_id in sdk_ids
        }
        normalized = {_normalized_value(value) for value in values.values()}
        if len(normalized) > 1:
            context_diff.append({"field": field, "values": values})

    notes: list[str] = []
    for sdk_id in sdk_ids:
        if not diagnostics_by_manifest[sdk_id].get("windows_symbol_parity"):
            notes.append(f"{sdk_id}: windows_symbol_parity unavailable or empty")

    payload: dict[str, Any] = {
        "inputs": list(manifest_ids),
        "summary": {
            "total_sdks": len(sdk_ids),
            "symbols_compared": len(all_symbols),
            "symbols_with_differences": len(symbol_diff),
            "capabilities_compared": len(capability_names),
            "capabilities_with_differences": len(capability_diff),
            "context_fields_compared": len(context_fields),
            "context_fields_with_differences": len(context_diff),
        },
        "symbol_diff": symbol_diff,
        "capability_diff": capability_diff,
        "context_diff": context_diff,
        "notes": notes,
    }
    return SDKComparisonReport(payload=payload)


def format_comparison_summary(report: dict[str, Any]) -> str:
    """Return concise human-readable output for CLI runs."""
    inputs = report.get("inputs", [])
    lines: list[str] = []
    lines.append("## SDK Comparison Summary")
    lines.append("")
    lines.append(f"SDKs: {', '.join(inputs)}")

    symbol_diff = report.get("symbol_diff", [])
    if isinstance(symbol_diff, list) and symbol_diff:
        lines.append("")
        lines.append("Symbol Differences:")
        for item in symbol_diff[:10]:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol", "unknown")
            present = ", ".join(item.get("present_in", []))
            missing = ", ".join(item.get("missing_in", []))
            lines.append(f"- {symbol}")
            lines.append(f"  present: {present}")
            lines.append(f"  missing: {missing}")

    capability_diff = report.get("capability_diff", [])
    if isinstance(capability_diff, list) and capability_diff:
        lines.append("")
        lines.append("Capability Differences:")
        for item in capability_diff[:10]:
            if not isinstance(item, dict):
                continue
            name = item.get("capability", "unknown")
            true_in = ", ".join(item.get("true_in", []))
            false_in = ", ".join(item.get("false_in", []))
            lines.append(f"- {name}")
            lines.append(f"  true: {true_in}")
            lines.append(f"  false: {false_in}")
            for key in sorted(item.keys()):
                if key.endswith("_in") and key not in {"true_in", "false_in"}:
                    values = ", ".join(item.get(key, []))
                    status_name = key[:-3]
                    lines.append(f"  {status_name}: {values}")

    context_diff = report.get("context_diff", [])
    if isinstance(context_diff, list) and context_diff:
        lines.append("")
        lines.append("Context Differences:")
        for item in context_diff[:10]:
            if not isinstance(item, dict):
                continue
            field = item.get("field", "unknown")
            values = item.get("values", {})
            rendered = ", ".join(
                f"{sdk_id}={json.dumps(values.get(sdk_id), sort_keys=True)}"
                for sdk_id in sorted(values.keys())
            ) if isinstance(values, dict) else str(values)
            lines.append(f"- {field}: {rendered}")

    return "\n".join(lines)
