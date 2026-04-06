"""Tests for pytvt.output — CSV, JSON, XLSX, console reports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pytvt.models import CameraInfo, DeviceEntry, ScanResult
from pytvt.output import (
    _device_to_dict,
    _flatten_results,
    print_nvr_report,
    print_summary,
    save_csv,
    save_failed_devices,
    save_json,
)

# ── _flatten_results ─────────────────────────────────────────────────


class TestFlattenResults:
    def test_with_cameras(self, successful_result):
        rows = _flatten_results([successful_result])
        assert len(rows) == 1
        assert rows[0]["site"] == "Test Site"
        assert rows[0]["camera_name"] == "Lobby"
        assert rows[0]["camera_address"] == "192.168.1.100"

    def test_without_cameras(self, failed_result):
        rows = _flatten_results([failed_result])
        assert len(rows) == 1
        assert rows[0]["camera_name"] == ""
        assert "error" in rows[0]

    def test_multiple_cameras(self):
        r = ScanResult(
            site="S",
            hostname="H",
            nvr_ip="1.1.1.1",
            cameras=[
                CameraInfo(name="A", address="10.0.0.1"),
                CameraInfo(name="B", address="10.0.0.2"),
                CameraInfo(name="Empty", address=""),  # no address — excluded
            ],
        )
        rows = _flatten_results([r])
        assert len(rows) == 2

    def test_empty_results(self):
        assert _flatten_results([]) == []


# ── _device_to_dict ──────────────────────────────────────────────────


class TestDeviceToDict:
    def test_minimal(self):
        d = DeviceEntry(ip="1.1.1.1")
        result = _device_to_dict(d)
        assert result == {"ip": "1.1.1.1"}

    def test_full(self):
        d = DeviceEntry(
            ip="10.0.0.1",
            site="HQ",
            hostname="NVR-1",
            mac="AA:BB:CC:DD:EE:FF",
            port=7000,
            manufacturer="TVT",
        )
        result = _device_to_dict(d)
        assert result["site"] == "HQ"
        assert result["port"] == 7000
        assert result["manufacturer"] == "TVT"


# ── save_csv ─────────────────────────────────────────────────────────


class TestSaveCsv:
    def test_creates_file(self, tmp_path, successful_result):
        out = str(tmp_path / "out.csv")
        save_csv([successful_result], out)
        assert Path(out).exists()
        with open(out) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["camera_name"] == "Lobby"

    def test_empty_results(self, tmp_path, capsys):
        out = str(tmp_path / "empty.csv")
        save_csv([], out)
        assert not Path(out).exists()
        assert "No data" in capsys.readouterr().err


# ── save_json ────────────────────────────────────────────────────────


class TestSaveJson:
    def test_creates_file(self, tmp_path, successful_result):
        out = str(tmp_path / "out.json")
        save_json([successful_result], out)
        with open(out) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["nvr_ip"] == "10.0.0.1"
        assert data[0]["success"] is True


# ── save_failed_devices ──────────────────────────────────────────────


class TestSaveFailedDevices:
    def test_saves_failed(self, tmp_path, failed_result):
        dev = DeviceEntry(ip="10.0.0.1", site="Test Site")
        out = str(tmp_path / "failed.json")
        save_failed_devices([failed_result], [dev], out)
        with open(out) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["ip"] == "10.0.0.1"

    def test_no_failures(self, tmp_path, successful_result):
        dev = DeviceEntry(ip="10.0.0.1")
        out = str(tmp_path / "failed.json")
        save_failed_devices([successful_result], [dev], out)
        assert not Path(out).exists()

    def test_includes_missing_devices(self, tmp_path):
        """Devices that weren't even scanned should also be listed as failed."""
        scanned = ScanResult(nvr_ip="10.0.0.1", success=True)
        devs = [
            DeviceEntry(ip="10.0.0.1"),
            DeviceEntry(ip="10.0.0.2"),  # not in results
        ]
        out = str(tmp_path / "failed.json")
        save_failed_devices([scanned], devs, out)
        with open(out) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["ip"] == "10.0.0.2"


# ── Console reports ──────────────────────────────────────────────────


class TestPrintNvrReport:
    def test_successful(self, capsys, successful_result):
        print_nvr_report(successful_result)
        out = capsys.readouterr().out
        assert "Test Site" in out
        assert "NVR-01" in out
        assert "Lobby" in out

    def test_failed(self, capsys, failed_result):
        print_nvr_report(failed_result)
        out = capsys.readouterr().out
        assert "ERROR" in out
        assert "timed out" in out


class TestPrintSummary:
    def test_summary(self, capsys, successful_result, failed_result):
        print_summary([successful_result, failed_result])
        out = capsys.readouterr().out
        assert "1/2 NVRs scanned successfully" in out
