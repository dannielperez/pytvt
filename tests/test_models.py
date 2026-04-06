"""Tests for pytvt.models — dataclass construction, factories, serialization."""

from __future__ import annotations

import pytest

from pytvt.models import (
    CameraInfo,
    DeviceEntry,
    NvrApiError,
    ScannerConfig,
    ScanResult,
)

# ── ScannerConfig ────────────────────────────────────────────────────


class TestScannerConfig:
    def test_defaults(self):
        c = ScannerConfig()
        assert c.username == "admin"
        assert c.password == ""
        assert c.port == 6036
        assert c.timeout == 10
        assert c.max_channels == 64
        assert c.concurrency == 4
        assert c.api_url == "http://localhost:3000"

    def test_override(self):
        c = ScannerConfig(port=9000, concurrency=8)
        assert c.port == 9000
        assert c.concurrency == 8


# ── DeviceEntry ──────────────────────────────────────────────────────


class TestDeviceEntry:
    def test_from_dict_minimal(self):
        d = DeviceEntry.from_dict({"ip": "10.0.0.1"})
        assert d.ip == "10.0.0.1"
        assert d.site == ""
        assert d.port == 0
        assert d.manufacturer == ""

    def test_from_dict_full(self):
        d = DeviceEntry.from_dict(
            {
                "ip": "10.0.0.2",
                "site": "HQ",
                "hostname": "NVR1",
                "mac": "58:5B:69:00:00:01",
                "port": 7000,
                "manufacturer": "TVT",
            }
        )
        assert d.ip == "10.0.0.2"
        assert d.site == "HQ"
        assert d.hostname == "NVR1"
        assert d.mac == "58:5B:69:00:00:01"
        assert d.port == 7000
        assert d.manufacturer == "TVT"

    def test_from_dict_ignores_unknown_keys(self):
        d = DeviceEntry.from_dict({"ip": "1.2.3.4", "unknown_field": "whatever"})
        assert d.ip == "1.2.3.4"

    def test_effective_port_uses_device_port(self, default_config):
        d = DeviceEntry(ip="1.1.1.1", port=7777)
        assert d.effective_port(default_config) == 7777

    def test_effective_port_falls_back_to_config(self, default_config):
        d = DeviceEntry(ip="1.1.1.1", port=0)
        assert d.effective_port(default_config) == default_config.port

    def test_from_dict_port_cast(self):
        d = DeviceEntry.from_dict({"ip": "1.1.1.1", "port": "8080"})
        assert d.port == 8080


# ── CameraInfo ───────────────────────────────────────────────────────


class TestCameraInfo:
    def test_from_dict(self):
        c = CameraInfo.from_dict(
            {
                "channel": 1,
                "name": "Lobby",
                "address": "192.168.1.10",
                "port": 9008,
                "status": "Online",
            }
        )
        assert c.channel == 1
        assert c.name == "Lobby"
        assert c.has_address is True

    def test_has_address_empty(self):
        c = CameraInfo(address="")
        assert c.has_address is False

    def test_has_address_whitespace(self):
        c = CameraInfo(address="   ")
        assert c.has_address is False

    def test_from_dict_defaults(self):
        c = CameraInfo.from_dict({})
        assert c.channel == ""
        assert c.name == ""
        assert c.address == ""


# ── ScanResult ───────────────────────────────────────────────────────


class TestScanResult:
    def test_for_device_factory(self, sample_device, default_config):
        r = ScanResult.for_device(sample_device, default_config, backend="sdk")
        assert r.nvr_ip == "10.0.0.1"
        assert r.site == "Test Site"
        assert r.hostname == "NVR-01"
        assert r.nvr_mac == "58:5B:69:AA:BB:CC"
        assert r.backend == "sdk"
        assert r.success is False  # default is failure

    def test_camera_count(self, successful_result):
        assert successful_result.camera_count == 1

    def test_camera_count_excludes_no_address(self):
        r = ScanResult(
            cameras=[
                CameraInfo(address="10.0.0.1"),
                CameraInfo(address=""),
                CameraInfo(address="10.0.0.2"),
            ]
        )
        assert r.camera_count == 2

    def test_to_dict(self, successful_result):
        d = successful_result.to_dict()
        assert isinstance(d, dict)
        assert d["nvr_ip"] == "10.0.0.1"
        assert d["success"] is True
        assert isinstance(d["cameras"], list)
        assert d["cameras"][0]["name"] == "Lobby"

    def test_for_device_uses_effective_port(self, default_config):
        dev = DeviceEntry(ip="1.1.1.1", port=0)
        r = ScanResult.for_device(dev, default_config)
        assert r.nvr_port == default_config.port


# ── NvrApiError ──────────────────────────────────────────────────────


class TestNvrApiError:
    def test_basic(self):
        e = NvrApiError("bad request", error_code="401")
        assert str(e) == "bad request"
        assert e.error_code == "401"

    def test_no_code(self):
        e = NvrApiError("oops")
        assert e.error_code is None
