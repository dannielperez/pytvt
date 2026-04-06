"""Tests for pytvt.cli — parser construction, dedup helpers, entry points."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pytvt.cli import _build_parser, _dedupe_by_mac, _dedupe_devices
from pytvt.models import DeviceEntry

# ── _build_parser ────────────────────────────────────────────────────


class TestBuildParser:
    def test_default_backend(self):
        args = _build_parser().parse_args(["devices.json"])
        assert args.backend == "protocol"
        assert args.input == "devices.json"

    def test_backend_choices(self):
        for backend in ["protocol", "sdk", "sdk-local", "both"]:
            args = _build_parser().parse_args(["d.json", "--backend", backend])
            assert args.backend == backend

    def test_invalid_backend(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["d.json", "--backend", "invalid"])

    def test_discover_only(self):
        args = _build_parser().parse_args(["--discover-only"])
        assert args.discover_only is True
        assert args.input is None

    def test_subnet_repeatable(self):
        args = _build_parser().parse_args(
            [
                "--subnet",
                "10.0.0.0/24",
                "--subnet",
                "10.0.1.0/24",
            ]
        )
        assert args.subnet == ["10.0.0.0/24", "10.0.1.0/24"]

    def test_output_flag(self):
        args = _build_parser().parse_args(["d.json", "-o", "out.csv"])
        assert args.output == "out.csv"

    def test_xlsx_flag(self):
        args = _build_parser().parse_args(["d.json", "--xlsx", "files/"])
        assert args.xlsx == "files/"

    def test_credentials(self):
        args = _build_parser().parse_args(["d.json", "-u", "user", "-p", "pass"])
        assert args.username == "user"
        assert args.password == "pass"

    def test_concurrency(self):
        args = _build_parser().parse_args(["d.json", "-c", "8"])
        assert args.concurrency == 8


# ── _dedupe_by_mac ───────────────────────────────────────────────────


class TestDedupeByMac:
    def test_dedup(self):
        devices = [
            {"ip": "10.0.0.1", "mac": "AA:BB:CC:DD:EE:01"},
            {"ip": "10.0.0.2", "mac": "AA:BB:CC:DD:EE:01"},  # duplicate
            {"ip": "10.0.0.3", "mac": "AA:BB:CC:DD:EE:02"},
        ]
        result = _dedupe_by_mac(devices)
        assert len(result) == 2

    def test_no_mac(self):
        devices = [
            {"ip": "10.0.0.1", "mac": ""},
            {"ip": "10.0.0.2", "mac": ""},
        ]
        result = _dedupe_by_mac(devices)
        assert len(result) == 2  # no mac means no dedup key

    def test_empty(self):
        assert _dedupe_by_mac([]) == []


# ── _dedupe_devices ──────────────────────────────────────────────────


class TestDedupeDevices:
    def test_dedup_by_ip(self):
        devices = [
            DeviceEntry(ip="10.0.0.1"),
            DeviceEntry(ip="10.0.0.1"),  # duplicate
            DeviceEntry(ip="10.0.0.2"),
        ]
        result = _dedupe_devices(devices)
        assert len(result) == 2

    def test_skips_link_local(self):
        devices = [
            DeviceEntry(ip="169.254.1.1"),
            DeviceEntry(ip="10.0.0.1"),
        ]
        result = _dedupe_devices(devices)
        assert len(result) == 1
        assert result[0].ip == "10.0.0.1"

    def test_empty(self):
        assert _dedupe_devices([]) == []
