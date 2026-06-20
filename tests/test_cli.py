"""Tests for pytvt.tools.cli — parser construction, dedup helpers, entry points."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

# Import the submodule explicitly: the package-level ``pytvt.diagnostics()``
# convenience function shadows the ``pytvt.diagnostics`` submodule, and
# unittest.mock resolves the dotted string ``"pytvt.diagnostics.diagnostics"``
# differently on 3.10 vs 3.11+ (AttributeError on 3.10). Patching the imported
# module object with ``patch.object`` is unambiguous on every supported version.
import pytvt.diagnostics
from pytvt.device_sdk.http_client import DeviceInfoResult
from pytvt.device_sdk.manager import Backend
from pytvt.models import DeviceEntry
from pytvt.tools.cli import (
    _build_connect_parser,
    _build_doctor_parser,
    _build_parser,
    _build_scan_nvr_parser,
    _build_sdk_parser,
    _connect_main,
    _dedupe_by_mac,
    _dedupe_devices,
    doctor_cli,
    main,
    scan_nvr_cli,
    sdk_cli,
)

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


class TestBuildConnectParser:
    def test_nat_arguments(self):
        args = _build_connect_parser().parse_args(["--nat", "--id", "ABC123456", "-u", "admin", "-p", "pass"])
        assert args.nat is True
        assert args.identifier == "ABC123456"
        assert args.username == "admin"

    def test_direct_arguments(self):
        args = _build_connect_parser().parse_args(["--host", "10.0.0.1", "-u", "admin", "-p", "pass"])
        assert args.host == "10.0.0.1"
        assert args.nat is False


class TestBuildScanNvrParser:
    def test_scan_nvr_defaults(self):
        args = _build_scan_nvr_parser().parse_args(["10.0.0.1"])
        assert args.ip == "10.0.0.1"
        assert args.port == 6036
        assert args.username == "admin"
        assert args.password == ""

    def test_scan_nvr_parser_sdk_path(self):
        args = _build_scan_nvr_parser().parse_args(["10.0.0.1", "--sdk-path", "/opt/tvt-sdk"])
        assert args.sdk_path == "/opt/tvt-sdk"

    def test_scan_nvr_parser_timeout_and_json(self):
        args = _build_scan_nvr_parser().parse_args(["10.0.0.1", "--timeout", "15", "--json"])
        assert args.timeout == 15
        assert args.json is True


class TestBuildDoctorParser:
    def test_doctor_parser(self):
        args = _build_doctor_parser().parse_args(["--sdk-path", "/opt/tvt-sdk", "--json"])
        assert args.sdk_path == "/opt/tvt-sdk"
        assert args.json is True


class TestBuildSdkParser:
    def test_sdk_modify_ip_dry_run_args(self):
        args = _build_sdk_parser().parse_args(
            [
                "modify-ip",
                "--mac",
                "AA:BB:CC:DD:EE:FF",
                "--new-ip",
                "10.1.1.20",
                "--mask",
                "255.255.255.0",
                "--gateway",
                "10.1.1.1",
                "--dry-run",
            ]
        )
        assert args.command == "modify-ip"
        assert args.dry_run is True


class TestConnectCommand:
    def test_main_dispatches_scan_nvr(self):
        argv = ["pytvt", "scan-nvr", "10.0.0.1"]
        with patch("sys.argv", argv), patch("pytvt.tools.cli.scan_nvr_cli") as mock_scan:
            main()

        mock_scan.assert_called_once_with(argv[2:])

    def test_main_dispatches_connect(self):
        argv = ["pytvt", "connect", "--nat", "--id", "ABC123456", "-u", "admin", "-p", "pass"]
        with patch("sys.argv", argv), patch("pytvt.tools.cli._connect_main") as mock_connect:
            main()

        mock_connect.assert_called_once_with(argv[2:])

    def test_main_dispatches_doctor(self):
        argv = ["pytvt", "doctor"]
        with patch("sys.argv", argv), patch("pytvt.tools.cli.doctor_cli") as mock_doctor:
            main()

        mock_doctor.assert_called_once_with(argv[2:])

    def test_main_dispatches_sdk(self):
        argv = [
            "pytvt",
            "sdk",
            "modify-ip",
            "--mac",
            "AA:BB:CC:DD:EE:FF",
            "--new-ip",
            "10.1.1.20",
            "--mask",
            "255.255.255.0",
            "--gateway",
            "10.1.1.1",
            "--dry-run",
        ]
        with patch("sys.argv", argv), patch("pytvt.tools.cli.sdk_cli") as mock_sdk:
            main()

        mock_sdk.assert_called_once_with(argv[2:])

    def test_sdk_cli_dry_run_success(self, capsys):
        with patch(
            "pytvt.device_sdk.ip_modify.validate_modify_ip_dry_run",
            return_value={"dry_run": True, "valid_arguments": True, "target_match": {"ip": "10.1.1.10"}},
        ):
            sdk_cli(
                [
                    "modify-ip",
                    "--mac",
                    "AA:BB:CC:DD:EE:FF",
                    "--new-ip",
                    "10.1.1.20",
                    "--mask",
                    "255.255.255.0",
                    "--gateway",
                    "10.1.1.1",
                    "--dry-run",
                    "--json",
                ]
            )

        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True

    def test_sdk_cli_dry_run_never_calls_modify(self, capsys):
        with (
            patch(
                "pytvt.device_sdk.ip_modify.validate_modify_ip_dry_run",
                return_value={"dry_run": True, "valid_arguments": True, "target_match": {"ip": "10.1.1.10"}},
            ),
            patch("pytvt.device_sdk.ip_modify.modify_device_ip_by_mac") as mock_modify,
        ):
            sdk_cli(
                [
                    "modify-ip",
                    "--mac",
                    "AA:BB:CC:DD:EE:FF",
                    "--new-ip",
                    "10.1.1.20",
                    "--mask",
                    "255.255.255.0",
                    "--gateway",
                    "10.1.1.1",
                    "--dry-run",
                    "--json",
                ]
            )

        json.loads(capsys.readouterr().out)
        mock_modify.assert_not_called()

    def test_sdk_cli_blocked_dry_run_exits_nonzero(self, capsys):
        with (
            patch(
                "pytvt.device_sdk.ip_modify.validate_modify_ip_dry_run",
                return_value={"dry_run": True, "blocked": True, "valid_arguments": True, "target_match": None},
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            sdk_cli(
                [
                    "modify-ip",
                    "--mac",
                    "AA:BB:CC:DD:EE:FF",
                    "--new-ip",
                    "10.1.1.20",
                    "--mask",
                    "255.255.255.0",
                    "--gateway",
                    "10.1.1.1",
                    "--dry-run",
                    "--json",
                ]
            )

        payload = json.loads(capsys.readouterr().out)
        assert payload["blocked"] is True

    def test_sdk_cli_scan_match_success(self, capsys):
        with patch(
            "pytvt.device_sdk.ip_modify.scan_device_match",
            return_value={"scan_only": True, "matched": True, "target_match": {"ip": "10.1.1.10"}},
        ):
            sdk_cli(["scan-match", "--mac", "AA:BB:CC:DD:EE:FF", "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["matched"] is True

    def test_sdk_cli_verify_only_failure_exits_nonzero(self, capsys):
        with (
            patch(
                "pytvt.device_sdk.ip_modify.verify_device_ip_after_modify",
                return_value={"verified": False, "blocked": True, "status": "blocked", "reason": "not found"},
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            sdk_cli(["verify-ip", "--mac", "AA:BB:CC:DD:EE:FF", "--new-ip", "10.1.1.20", "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["blocked"] is True

    def test_sdk_cli_modify_requires_yes(self):
        with pytest.raises(SystemExit, match="2"):
            sdk_cli(
                [
                    "modify-ip",
                    "--mac",
                    "AA:BB:CC:DD:EE:FF",
                    "--new-ip",
                    "10.1.1.20",
                    "--mask",
                    "255.255.255.0",
                    "--gateway",
                    "10.1.1.1",
                ]
            )

    def test_sdk_cli_modify_success_but_verification_failure_blocks(self, capsys):
        with (
            patch(
                "pytvt.device_sdk.ip_modify.modify_device_ip_by_mac",
                return_value=type(
                    "ModifyResult",
                    (),
                    {
                        "success": True,
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "old_ip": "10.1.1.10",
                        "new_ip": "10.1.1.20",
                        "error_code": None,
                        "error_message": None,
                        "sdk_function_used": "NET_CLIENT_RequestModifyDeviceIp",
                        "elapsed_ms": 12,
                    },
                )(),
            ),
            patch(
                "pytvt.device_sdk.ip_modify.verify_device_ip_after_modify",
                return_value={
                    "verified": False,
                    "blocked": True,
                    "status": "blocked",
                    "reason": "scan did not confirm",
                },
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            sdk_cli(
                [
                    "modify-ip",
                    "--mac",
                    "AA:BB:CC:DD:EE:FF",
                    "--old-ip",
                    "10.1.1.10",
                    "--new-ip",
                    "10.1.1.20",
                    "--mask",
                    "255.255.255.0",
                    "--gateway",
                    "10.1.1.1",
                    "--yes",
                    "--json",
                ]
            )

        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "blocked"
        assert payload["verified"] is False

    def test_connect_main_success(self, capsys):
        with patch("pytvt.device_sdk.manager.DeviceManager") as mock_manager:
            instance = mock_manager.return_value
            instance.__enter__.return_value = instance
            instance.backend = Backend.NETSDK
            instance.connection_method = "nat"
            instance.ip = ""
            instance.identifier = "ABC123456"
            instance.target = "ABC123456"
            instance.device_info.return_value = DeviceInfoResult(
                success=True,
                device_name="NVR-01",
                serial_number="ABC123456",
            )

            _connect_main(["--nat", "--id", "ABC123456", "-u", "admin", "-p", "pass"])

        out = capsys.readouterr().out
        assert "Connected via nat/netsdk to ABC123456" in out

    def test_scan_nvr_cli_prints_json_without_sentinels(self, capsys):
        with patch(
            "pytvt.device_sdk.sdk_local.scan_nvr_payload",
            return_value={"success": True, "cameras": [], "nvr_ip": "10.0.0.1", "nvr_port": 6036},
        ):
            scan_nvr_cli(["10.0.0.1", "--json"])

        out = capsys.readouterr().out
        assert "___JSON_START___" not in out
        assert '"success": true' in out

    def test_scan_nvr_cli_prints_sentinels_by_default(self, capsys):
        with patch(
            "pytvt.device_sdk.sdk_local.scan_nvr_payload",
            return_value={"success": True, "cameras": [], "nvr_ip": "10.0.0.1", "nvr_port": 6036},
        ):
            scan_nvr_cli(["10.0.0.1"])

        out = capsys.readouterr().out
        assert "___JSON_START___" in out
        assert "___JSON_END___" in out

    def test_scan_nvr_cli_exits_nonzero_on_failure(self):
        with (
            patch(
                "pytvt.device_sdk.sdk_local.scan_nvr_payload",
                return_value={
                    "success": False,
                    "error": "missing SDK",
                    "cameras": [],
                    "nvr_ip": "10.0.0.1",
                    "nvr_port": 6036,
                },
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            scan_nvr_cli(["10.0.0.1"])

    def test_scan_nvr_cli_passes_sdk_path(self):
        with patch(
            "pytvt.device_sdk.sdk_local.scan_nvr_payload",
            return_value={"success": True, "cameras": [], "nvr_ip": "10.0.0.1", "nvr_port": 6036},
        ) as mock_payload:
            scan_nvr_cli(["10.0.0.1", "6036", "admin", "secret", "--sdk-path", "/opt/tvt-sdk", "--timeout", "15"])

        mock_payload.assert_called_once_with(
            "10.0.0.1",
            port=6036,
            username="admin",
            password="secret",
            sdk_path="/opt/tvt-sdk",
            max_channels=64,
            timeout=15.0,
        )

    def test_scan_nvr_cli_invalid_credentials_json_output(self, capsys):
        with (
            patch(
                "pytvt.device_sdk.sdk_local.scan_nvr_payload",
                return_value={
                    "success": False,
                    "error": "Login failed",
                    "cameras": [],
                    "nvr_ip": "10.0.0.1",
                    "nvr_port": 6036,
                },
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            scan_nvr_cli(["10.0.0.1", "6036", "admin", "supersecret", "--json"])

        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["error"] == "Login failed"
        assert "supersecret" not in out

    def test_scan_nvr_cli_unreachable_host_json_output(self, capsys):
        with (
            patch(
                "pytvt.device_sdk.sdk_local.scan_nvr_payload",
                return_value={
                    "success": False,
                    "error": "Connection refused",
                    "cameras": [],
                    "nvr_ip": "10.0.0.9",
                    "nvr_port": 6036,
                },
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            scan_nvr_cli(["10.0.0.9", "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "Connection refused"

    def test_scan_nvr_cli_sdk_not_loaded_json_output(self, capsys):
        with (
            patch(
                "pytvt.device_sdk.sdk_local.scan_nvr_payload",
                return_value={
                    "success": False,
                    "error": "TVT SDK is not available",
                    "cameras": [],
                    "nvr_ip": "10.0.0.1",
                    "nvr_port": 6036,
                },
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            scan_nvr_cli(["10.0.0.1", "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "TVT SDK is not available"

    def test_doctor_cli_json_failure_exits_nonzero(self, capsys):
        class Report:
            sdk_available = False

            @staticmethod
            def to_dict():
                return {"sdk_available": False}

        with patch.object(pytvt.diagnostics, "diagnostics", return_value=Report()):
            with pytest.raises(SystemExit, match="1"):
                doctor_cli(["--json"])

        assert json.loads(capsys.readouterr().out) == {"sdk_available": False}

    def test_doctor_cli_human_output_success(self, capsys):
        class Report:
            sdk_available = True

            @staticmethod
            def to_dict():
                return {"sdk_available": True}

            def __str__(self):
                return "doctor ok"

        with patch.object(pytvt.diagnostics, "diagnostics", return_value=Report()):
            doctor_cli([])

        assert capsys.readouterr().out.strip() == "doctor ok"


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
