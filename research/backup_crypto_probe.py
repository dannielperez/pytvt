#!/usr/bin/env python3
"""Compare TVT backup exports and locate encryption-parameter candidates.

The tool deliberately calls values *candidates*. A file comparison can show that a
field is stable, random-looking, or mutation-dependent, but it cannot by itself
prove whether the field is a salt, IV, key identifier, or checksum.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


HEX_RUN = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{16,64}(?![0-9A-Fa-f])")


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def differing_ranges(left: bytes, right: bytes) -> list[ByteRange]:
    """Return maximal half-open ranges whose bytes differ."""
    limit = min(len(left), len(right))
    ranges: list[ByteRange] = []
    start: int | None = None
    for offset in range(limit):
        different = left[offset] != right[offset]
        if different and start is None:
            start = offset
        elif not different and start is not None:
            ranges.append(ByteRange(start, offset))
            start = None
    if start is not None:
        ranges.append(ByteRange(start, limit))
    if len(left) != len(right):
        ranges.append(ByteRange(limit, max(len(left), len(right))))
    return ranges


def merge_ranges(ranges: Iterable[ByteRange], maximum_gap: int) -> list[ByteRange]:
    merged: list[ByteRange] = []
    for item in ranges:
        if merged and item.start - merged[-1].end <= maximum_gap:
            merged[-1] = ByteRange(merged[-1].start, item.end)
        else:
            merged.append(item)
    return merged


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    size = len(data)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def likely_block_sizes(region: ByteRange) -> list[int]:
    return [
        size
        for size in (4, 8, 16, 32)
        if region.start % size == 0 and region.length % size == 0
    ]


def repeated_blocks(data: bytes, block_size: int, limit: int = 5) -> list[dict[str, object]]:
    blocks = Counter(
        data[offset : offset + block_size]
        for offset in range(0, len(data) - block_size + 1, block_size)
    )
    return [
        {"hex": block.hex(), "count": count}
        for block, count in blocks.most_common(limit)
        if count > 1
    ]


def classify_hex_fields(
    reference: bytes,
    repeat: bytes,
    changed: bytes | None,
    header_bytes: int,
) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    header = reference[:header_bytes]
    for match in HEX_RUN.finditer(header):
        start, end = match.span()
        value = match.group().decode("ascii")
        stable_repeat = repeat[start:end] == reference[start:end]
        stable_changed = changed is None or changed[start:end] == reference[start:end]
        if stable_repeat and stable_changed:
            classification = "fixed salt/key-id/format candidate"
        elif stable_repeat:
            classification = "mutation-dependent digest/checksum candidate"
        else:
            classification = "per-export salt/IV/nonce candidate"
        fields.append(
            {
                "offset": start,
                "length": end - start,
                "value": value,
                "stable_in_repeat": stable_repeat,
                "stable_after_mutation": stable_changed,
                "classification": classification,
            }
        )
    return fields


def binary_header_candidates(
    reference: bytes,
    repeat: bytes,
    changed: bytes | None,
    header_bytes: int,
    limit: int = 8,
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    end = min(header_bytes, len(reference), len(repeat))
    if changed is not None:
        end = min(end, len(changed))
    for size in (16, 8):
        for offset in range(0, end - size + 1, size):
            value = reference[offset : offset + size]
            if repeat[offset : offset + size] != value:
                continue
            if changed is not None and changed[offset : offset + size] != value:
                continue
            entropy = shannon_entropy(value)
            if entropy < 3.0 or all(32 <= byte < 127 for byte in value):
                continue
            candidates.append(
                {
                    "offset": offset,
                    "length": size,
                    "hex": value.hex(),
                    "entropy": round(entropy, 3),
                    "classification": "stable binary salt/IV/key-id candidate",
                }
            )
    candidates.sort(key=lambda item: (-float(item["entropy"]), -int(item["length"]), int(item["offset"])))
    return candidates[:limit]


def analyze(
    reference_path: Path,
    repeat_path: Path,
    changed_path: Path | None,
    header_bytes: int = 256,
    merge_gap: int = 32,
) -> dict[str, object]:
    reference = reference_path.read_bytes()
    repeat = repeat_path.read_bytes()
    changed = changed_path.read_bytes() if changed_path else None

    report: dict[str, object] = {
        "files": {
            "reference": {"path": str(reference_path), "size": len(reference), "sha256": sha256(reference)},
            "repeat": {"path": str(repeat_path), "size": len(repeat), "sha256": sha256(repeat)},
        },
        "repeat_is_identical": reference == repeat,
        "interpretation": [],
    }
    if changed_path is not None and changed is not None:
        report["files"]["changed"] = {
            "path": str(changed_path),
            "size": len(changed),
            "sha256": sha256(changed),
        }

    interpretation = report["interpretation"]
    assert isinstance(interpretation, list)
    if reference == repeat:
        interpretation.append(
            "Repeated exports are byte-identical; no per-export random salt/IV/nonce is visible."
        )
    else:
        interpretation.append(
            "Repeated exports differ; changing header fields are possible salt/IV/nonce values."
        )

    report["ascii_hex_header_fields"] = classify_hex_fields(
        reference, repeat, changed, header_bytes
    )
    report["binary_header_candidates"] = binary_header_candidates(
        reference, repeat, changed, header_bytes
    )
    report["repeated_blocks"] = {
        str(size): repeated_blocks(reference, size) for size in (8, 16)
    }

    if changed is not None:
        raw = differing_ranges(reference, changed)
        merged = merge_ranges(raw, merge_gap)
        report["mutation_diff"] = {
            "different_byte_count": sum(
                left != right for left, right in zip(reference, changed)
            )
            + abs(len(reference) - len(changed)),
            "raw_ranges": [asdict(item) | {"length": item.length} for item in raw],
            "merged_ranges": [asdict(item) | {"length": item.length} for item in merged],
        }
        if merged:
            encrypted_region = max(merged, key=lambda item: item.length)
            sizes = likely_block_sizes(encrypted_region)
            report["probable_mutated_record"] = (
                asdict(encrypted_region)
                | {"length": encrypted_region.length, "aligned_block_sizes": sizes}
            )
            if sizes:
                interpretation.append(
                    "The largest changed region is aligned like a {}-byte block-cipher record."
                    .format(max(sizes))
                )
    interpretation.append(
        "A stable candidate is not proven to be a salt; confirm it by tracing the KDF or decryptor."
    )
    return report


def format_report(report: dict[str, object]) -> str:
    lines = ["TVT backup crypto probe", ""]
    files = report["files"]
    assert isinstance(files, dict)
    for label, metadata in files.items():
        assert isinstance(metadata, dict)
        lines.append(
            f"{label}: {metadata['path']} ({metadata['size']} bytes, sha256 {metadata['sha256']})"
        )
    lines.extend(["", f"repeat byte-identical: {str(report['repeat_is_identical']).lower()}"])

    lines.extend(["", "interpretation:"])
    for item in report["interpretation"]:
        lines.append(f"  - {item}")

    lines.extend(["", "ASCII-hex header fields:"])
    fields = report["ascii_hex_header_fields"]
    if not fields:
        lines.append("  (none)")
    for field in fields:
        lines.append(
            "  0x{offset:x} len={length} {classification}: {value}".format(**field)
        )

    lines.extend(["", "stable binary header candidates:"])
    candidates = report["binary_header_candidates"]
    if not candidates:
        lines.append("  (none)")
    for candidate in candidates:
        lines.append(
            "  0x{offset:x} len={length} entropy={entropy}: {hex}".format(**candidate)
        )

    mutation = report.get("mutation_diff")
    if isinstance(mutation, dict):
        lines.extend(["", f"different bytes after mutation: {mutation['different_byte_count']}"])
        lines.append("merged changed ranges:")
        for item in mutation["merged_ranges"]:
            lines.append(
                f"  0x{item['start']:x}..0x{item['end']:x} ({item['length']} bytes)"
            )
        record = report.get("probable_mutated_record")
        if isinstance(record, dict):
            lines.append(
                "probable encrypted record: 0x{start:x}..0x{end:x}; "
                "aligned block sizes={aligned_block_sizes}".format(**record)
            )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare deterministic TVT backup exports and rank salt/IV/key/checksum candidates."
        )
    )
    parser.add_argument("reference", type=Path, help="first export with the known configuration")
    parser.add_argument("repeat", type=Path, help="second export made without changing anything")
    parser.add_argument(
        "--changed",
        type=Path,
        help="optional export after changing exactly one known field",
    )
    parser.add_argument("--header-bytes", type=int, default=256)
    parser.add_argument("--merge-gap", type=int, default=32)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = analyze(
        args.reference,
        args.repeat,
        args.changed,
        header_bytes=args.header_bytes,
        merge_gap=args.merge_gap,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
