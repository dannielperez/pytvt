#!/usr/bin/env python3
"""Summarize a JSON report produced by the management live-validation tools.

Purpose:
- Turn raw per-operation JSON into a compact terminal summary for debugging.

Outputs:
- Writes a human-readable summary to stdout.

Reusable evidence captured:
- None; this is a pure summarizer for already-captured reports.

Maintenance note:
- Retained as a lightweight report summarizer for operational debugging.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a pytvt management validation report")
    parser.add_argument("report", help="Path to a JSON report produced by management validation tooling")
    args = parser.parse_args()

    payload = json.loads(Path(args.report).read_text(encoding="utf-8"))
    print(f"report={args.report}")
    print(f"captured_at={payload.get('captured_at')}")
    print(f"target={payload.get('target')}")
    print(f"backend_selected={payload.get('backend_selected')}")
    print(f"final_status={payload.get('final_status')}")

    if "operations" in payload:
        print("operations:")
        for op in payload["operations"]:
            print(
                f"  - {op['operation']}: success={op.get('success')} elapsed_ms={op.get('elapsed_ms')} "
                f"error={op.get('exception_type')}"
            )

    if "iterations" in payload:
        print("iterations:")
        for cycle in payload["iterations"]:
            failures = [op for op in cycle["operations"] if not op.get("success")]
            print(f"  - cycle={cycle['cycle']} failures={len(failures)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())