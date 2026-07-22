"""Unit tests for the research/backup_crypto_probe.py analysis helper.

The probe lives under research/ (reference-only, excluded from the wheel), so it is
not importable as a package. Load it by path to exercise its pure helpers.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PROBE_PATH = Path(__file__).resolve().parent.parent / "research" / "backup_crypto_probe.py"
_spec = importlib.util.spec_from_file_location("backup_crypto_probe", _PROBE_PATH)
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve the module via sys.modules (py3.14).
sys.modules[_spec.name] = probe
_spec.loader.exec_module(probe)

ByteRange = probe.ByteRange
analyze = probe.analyze
differing_ranges = probe.differing_ranges
merge_ranges = probe.merge_ranges


def test_differing_and_merged_ranges() -> None:
    left = b"aaaabbbbccccdddd"
    right = b"aaaaxbbbyccczddd"

    ranges = differing_ranges(left, right)

    assert ranges == [ByteRange(4, 5), ByteRange(8, 9), ByteRange(12, 13)]
    assert merge_ranges(ranges, maximum_gap=3) == [ByteRange(4, 13)]


def test_analyze_separates_stable_candidate_from_checksum(tmp_path: Path) -> None:
    reference = bytearray(512)
    reference[0x20:0x40] = b"0123456789abcdef0123456789abcdef"
    reference[0x60:0x80] = b"2A128F6ECDC13AD042A72ECAF4120CFB"
    reference[0x100:0x180] = bytes(range(128))
    repeat = bytes(reference)
    changed = bytearray(reference)
    changed[0x20:0x40] = b"abcdef0123456789abcdef0123456789"
    changed[0x120:0x160] = bytes(reversed(range(64)))

    reference_path = tmp_path / "reference.backup"
    repeat_path = tmp_path / "repeat.backup"
    changed_path = tmp_path / "changed.backup"
    reference_path.write_bytes(reference)
    repeat_path.write_bytes(repeat)
    changed_path.write_bytes(changed)

    report = analyze(reference_path, repeat_path, changed_path)
    fields = {field["offset"]: field for field in report["ascii_hex_header_fields"]}

    assert report["repeat_is_identical"] is True
    assert fields[0x20]["classification"] == "mutation-dependent digest/checksum candidate"
    assert fields[0x60]["classification"] == "fixed salt/key-id/format candidate"
    assert report["probable_mutated_record"]["start"] == 0x120
    assert report["probable_mutated_record"]["end"] == 0x160
    assert 8 in report["probable_mutated_record"]["aligned_block_sizes"]
