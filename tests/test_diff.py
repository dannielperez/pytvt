"""Tests for pytvt.diff — scan diffing and change detection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pytvt.diff import (
    CameraDiff,
    DeviceDiff,
    FieldChange,
    ScanDiff,
    diff_scans,
    format_diff_json,
    format_diff_summary,
    format_diff_text,
    load_scan_file,
)
from pytvt.models import CameraInfo, ScanResult

# ── Fixtures ─────────────────────────────────────────────────────────


def _make_result(
    ip: str = "10.0.0.1",
    site: str = "Site A",
    hostname: str = "NVR1",
    mac: str = "AA:BB:CC:DD:EE:01",
    success: bool = True,
    device_name: str = "NVR-01",
    device_model: str = "TD-3332B4",
    serial_number: str = "ABC123",
    firmware: str = "5.2.3",
    total_channels: int = 4,
    cameras: list[CameraInfo] | None = None,
    error: str | None = None,
    backend: str = "protocol",
) -> ScanResult:
    if cameras is None:
        cameras = [
            CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Online", model="IPC-A"),
            CameraInfo(channel=1, name="Parking", address="192.168.1.101", port=9008, status="Online", model="IPC-B"),
        ]
    return ScanResult(
        site=site,
        hostname=hostname,
        nvr_ip=ip,
        nvr_mac=mac,
        nvr_port=6036,
        success=success,
        device_name=device_name,
        device_model=device_model,
        serial_number=serial_number,
        firmware=firmware,
        total_channels=total_channels,
        cameras=cameras,
        error=error,
        backend=backend,
    )


def _write_scan_file(path: Path, results: list[ScanResult]) -> None:
    path.write_text(json.dumps([r.to_dict() for r in results], indent=2))


# ── FieldChange ──────────────────────────────────────────────────────


class TestFieldChange:
    def test_str(self):
        fc = FieldChange(field="firmware", old="5.1", new="5.2")
        assert str(fc) == "firmware: '5.1' → '5.2'"

    def test_str_bool(self):
        fc = FieldChange(field="success", old=False, new=True)
        assert "False" in str(fc) and "True" in str(fc)


# ── load_scan_file ───────────────────────────────────────────────────


class TestLoadScanFile:
    def test_load_valid_file(self, tmp_path):
        results = [_make_result()]
        path = tmp_path / "scan.json"
        _write_scan_file(path, results)

        loaded = load_scan_file(path)
        assert len(loaded) == 1
        assert loaded[0].nvr_ip == "10.0.0.1"
        assert loaded[0].success is True
        assert len(loaded[0].cameras) == 2

    def test_load_empty_array(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("[]")
        loaded = load_scan_file(path)
        assert loaded == []

    def test_load_file_not_found(self):
        with pytest.raises(ValueError, match="File not found"):
            load_scan_file("/nonexistent/path.json")

    def test_load_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{invalid json")
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_scan_file(path)

    def test_load_not_array(self, tmp_path):
        path = tmp_path / "obj.json"
        path.write_text('{"key": "value"}')
        with pytest.raises(ValueError, match="Expected a JSON array"):
            load_scan_file(path)

    def test_load_entry_not_object(self, tmp_path):
        path = tmp_path / "bad_entry.json"
        path.write_text("[42]")
        with pytest.raises(ValueError, match=r"Entry 0.*is not an object"):
            load_scan_file(path)

    def test_load_tolerates_extra_keys(self, tmp_path):
        path = tmp_path / "extra.json"
        data = [{"nvr_ip": "10.0.0.1", "success": True, "unknown_field": "ignored", "cameras": []}]
        path.write_text(json.dumps(data))
        loaded = load_scan_file(path)
        assert len(loaded) == 1
        assert loaded[0].nvr_ip == "10.0.0.1"

    def test_load_missing_fields_get_defaults(self, tmp_path):
        path = tmp_path / "minimal.json"
        path.write_text('[{"nvr_ip": "10.0.0.1"}]')
        loaded = load_scan_file(path)
        assert loaded[0].success is False
        assert loaded[0].cameras == []
        assert loaded[0].firmware == ""

    def test_load_string_path(self, tmp_path):
        path = tmp_path / "scan.json"
        _write_scan_file(path, [_make_result()])
        loaded = load_scan_file(str(path))
        assert len(loaded) == 1


# ── diff_scans — device level ────────────────────────────────────────


class TestDiffScansDevices:
    def test_identical_scans(self):
        results = [_make_result()]
        diff = diff_scans(results, results)
        assert not diff.has_changes
        assert diff.unchanged_count == 1
        assert diff.devices_added == []
        assert diff.devices_removed == []
        assert diff.devices_changed == []

    def test_device_added(self):
        old = [_make_result(ip="10.0.0.1")]
        new = [_make_result(ip="10.0.0.1"), _make_result(ip="10.0.0.2", hostname="NVR2")]
        diff = diff_scans(old, new)
        assert len(diff.devices_added) == 1
        assert diff.devices_added[0].nvr_ip == "10.0.0.2"
        assert diff.devices_added[0].status == "added"
        assert diff.old_device_count == 1
        assert diff.new_device_count == 2

    def test_device_removed(self):
        old = [_make_result(ip="10.0.0.1"), _make_result(ip="10.0.0.2")]
        new = [_make_result(ip="10.0.0.1")]
        diff = diff_scans(old, new)
        assert len(diff.devices_removed) == 1
        assert diff.devices_removed[0].nvr_ip == "10.0.0.2"
        assert diff.devices_removed[0].status == "removed"

    def test_device_field_changed(self):
        old = [_make_result(firmware="5.1")]
        new = [_make_result(firmware="5.2")]
        diff = diff_scans(old, new)
        assert len(diff.devices_changed) == 1
        dd = diff.devices_changed[0]
        assert dd.status == "changed"
        assert any(fc.field == "firmware" for fc in dd.field_changes)
        fw_change = next(fc for fc in dd.field_changes if fc.field == "firmware")
        assert fw_change.old == "5.1"
        assert fw_change.new == "5.2"

    def test_success_status_change(self):
        old = [_make_result(success=True, error=None)]
        new = [_make_result(success=False, error="Connection refused")]
        diff = diff_scans(old, new)
        assert len(diff.devices_changed) == 1
        dd = diff.devices_changed[0]
        assert any(fc.field == "success" for fc in dd.field_changes)
        assert any(fc.field == "error" for fc in dd.field_changes)

    def test_backend_change_detected(self):
        old = [_make_result(backend="protocol")]
        new = [_make_result(backend="sdk")]
        diff = diff_scans(old, new)
        assert len(diff.devices_changed) == 1
        assert any(fc.field == "backend" for fc in diff.devices_changed[0].field_changes)

    def test_mixed_add_remove_change(self):
        old = [_make_result(ip="10.0.0.1"), _make_result(ip="10.0.0.2"), _make_result(ip="10.0.0.3")]
        new = [
            _make_result(ip="10.0.0.1"),
            _make_result(ip="10.0.0.3", firmware="6.0"),
            _make_result(ip="10.0.0.4"),
        ]
        diff = diff_scans(old, new)
        assert len(diff.devices_added) == 1  # 10.0.0.4
        assert len(diff.devices_removed) == 1  # 10.0.0.2
        assert len(diff.devices_changed) == 1  # 10.0.0.3
        assert diff.unchanged_count == 1  # 10.0.0.1

    def test_file_labels_preserved(self):
        diff = diff_scans([], [], old_file="old.json", new_file="new.json")
        assert diff.old_file == "old.json"
        assert diff.new_file == "new.json"

    def test_both_empty(self):
        diff = diff_scans([], [])
        assert not diff.has_changes
        assert diff.unchanged_count == 0


# ── diff_scans — camera level ────────────────────────────────────────


class TestDiffScansCameras:
    def test_camera_added(self):
        old_cams = [CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Online")]
        new_cams = [
            CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Online"),
            CameraInfo(channel=1, name="Parking", address="192.168.1.101", port=9008, status="Online"),
        ]
        old = [_make_result(cameras=old_cams, total_channels=1)]
        new = [_make_result(cameras=new_cams, total_channels=2)]
        diff = diff_scans(old, new)
        assert len(diff.devices_changed) == 1
        dd = diff.devices_changed[0]
        assert len(dd.cameras_added) == 1
        assert dd.cameras_added[0].channel == 1
        assert dd.cameras_added[0].name == "Parking"

    def test_camera_removed(self):
        old_cams = [
            CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Online"),
            CameraInfo(channel=1, name="Parking", address="192.168.1.101", port=9008, status="Online"),
        ]
        new_cams = [CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Online")]
        old = [_make_result(cameras=old_cams)]
        new = [_make_result(cameras=new_cams)]
        diff = diff_scans(old, new)
        assert len(diff.devices_changed) == 1
        assert len(diff.devices_changed[0].cameras_removed) == 1
        assert diff.devices_changed[0].cameras_removed[0].name == "Parking"

    def test_camera_status_changed(self):
        old_cams = [CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Online")]
        new_cams = [CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Offline")]
        old = [_make_result(cameras=old_cams)]
        new = [_make_result(cameras=new_cams)]
        diff = diff_scans(old, new)
        assert len(diff.devices_changed) == 1
        dd = diff.devices_changed[0]
        assert len(dd.cameras_changed) == 1
        assert dd.cameras_changed[0].changes[0].field == "status"

    def test_camera_name_changed(self):
        old_cams = [CameraInfo(channel=0, name="Old Name", address="192.168.1.100", port=9008, status="Online")]
        new_cams = [CameraInfo(channel=0, name="New Name", address="192.168.1.100", port=9008, status="Online")]
        old = [_make_result(cameras=old_cams)]
        new = [_make_result(cameras=new_cams)]
        diff = diff_scans(old, new)
        dd = diff.devices_changed[0]
        assert dd.cameras_changed[0].changes[0].field == "name"

    def test_camera_address_changed(self):
        old_cams = [CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Online")]
        new_cams = [CameraInfo(channel=0, name="Lobby", address="192.168.1.200", port=9008, status="Online")]
        old = [_make_result(cameras=old_cams)]
        new = [_make_result(cameras=new_cams)]
        diff = diff_scans(old, new)
        assert len(diff.devices_changed[0].cameras_changed) == 1
        assert diff.devices_changed[0].cameras_changed[0].changes[0].field == "address"

    def test_cameras_without_address_ignored(self):
        """Cameras without addresses are filtered out of comparison."""
        old_cams = [
            CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Online"),
            CameraInfo(channel=1, name="Empty", address="", port=0, status=""),
        ]
        new_cams = [
            CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Online"),
        ]
        old = [_make_result(cameras=old_cams)]
        new = [_make_result(cameras=new_cams)]
        diff = diff_scans(old, new)
        # Channel 1 had no address, so no camera removal detected
        assert diff.unchanged_count == 1

    def test_camera_count_tracked(self):
        old_cams = [CameraInfo(channel=0, name="A", address="10.0.0.1", status="Online")]
        new_cams = [
            CameraInfo(channel=0, name="A", address="10.0.0.1", status="Online"),
            CameraInfo(channel=1, name="B", address="10.0.0.2", status="Online"),
        ]
        old = [_make_result(cameras=old_cams)]
        new = [_make_result(cameras=new_cams)]
        diff = diff_scans(old, new)
        dd = diff.devices_changed[0]
        assert dd.camera_count_old == 1
        assert dd.camera_count_new == 2


# ── ScanDiff model ───────────────────────────────────────────────────


class TestScanDiffModel:
    def test_has_changes_false_when_empty(self):
        diff = ScanDiff()
        assert not diff.has_changes

    def test_has_changes_true_with_added(self):
        diff = ScanDiff(devices_added=[DeviceDiff(nvr_ip="10.0.0.1", status="added")])
        assert diff.has_changes

    def test_to_dict(self):
        diff = ScanDiff(old_device_count=2, new_device_count=3)
        d = diff.to_dict()
        assert d["old_device_count"] == 2
        assert d["new_device_count"] == 3
        assert isinstance(d["devices_added"], list)


class TestDeviceDiffModel:
    def test_has_camera_changes(self):
        dd = DeviceDiff(nvr_ip="10.0.0.1", cameras_added=[CameraDiff(channel=0, status="added")])
        assert dd.has_camera_changes

    def test_no_camera_changes(self):
        dd = DeviceDiff(nvr_ip="10.0.0.1")
        assert not dd.has_camera_changes


# ── format_diff_text ─────────────────────────────────────────────────


class TestFormatDiffText:
    def test_no_changes(self):
        diff = ScanDiff(old_device_count=2, new_device_count=2, unchanged_count=2)
        text = format_diff_text(diff)
        assert "No changes detected" in text
        assert "2 unchanged" in text

    def test_added_device_shown(self):
        diff = ScanDiff(
            old_device_count=1,
            new_device_count=2,
            devices_added=[
                DeviceDiff(nvr_ip="10.0.0.2", hostname="NVR2", site="Site B", status="added", camera_count_new=5)
            ],
            unchanged_count=1,
        )
        text = format_diff_text(diff)
        assert "ADDED DEVICES" in text
        assert "10.0.0.2" in text
        assert "5 cameras" in text

    def test_removed_device_shown(self):
        diff = ScanDiff(
            devices_removed=[DeviceDiff(nvr_ip="10.0.0.3", status="removed", camera_count_old=3)],
        )
        text = format_diff_text(diff)
        assert "REMOVED DEVICES" in text
        assert "10.0.0.3" in text

    def test_changed_device_shows_field_changes(self):
        diff = ScanDiff(
            devices_changed=[
                DeviceDiff(
                    nvr_ip="10.0.0.1",
                    hostname="NVR1",
                    status="changed",
                    field_changes=[FieldChange(field="firmware", old="5.1", new="5.2")],
                )
            ],
        )
        text = format_diff_text(diff)
        assert "CHANGED DEVICES" in text
        assert "firmware" in text
        assert "5.1" in text
        assert "5.2" in text

    def test_camera_changes_in_text(self):
        diff = ScanDiff(
            devices_changed=[
                DeviceDiff(
                    nvr_ip="10.0.0.1",
                    status="changed",
                    cameras_added=[CameraDiff(channel=2, name="New Cam", status="added")],
                    cameras_removed=[CameraDiff(channel=3, name="Old Cam", status="removed")],
                    cameras_changed=[
                        CameraDiff(
                            channel=0,
                            name="Lobby",
                            status="changed",
                            changes=[FieldChange(field="status", old="Online", new="Offline")],
                        )
                    ],
                )
            ],
        )
        text = format_diff_text(diff)
        assert "+ ch 2" in text
        assert "- ch 3" in text
        assert "~ ch 0" in text

    def test_file_labels_in_header(self):
        diff = ScanDiff(old_file="monday.json", new_file="friday.json")
        text = format_diff_text(diff)
        assert "monday.json" in text
        assert "friday.json" in text


# ── format_diff_json ─────────────────────────────────────────────────


class TestFormatDiffJson:
    def test_valid_json(self):
        diff = ScanDiff(old_device_count=1, new_device_count=2)
        result = format_diff_json(diff)
        parsed = json.loads(result)
        assert parsed["old_device_count"] == 1
        assert parsed["new_device_count"] == 2

    def test_nested_structures(self):
        diff = ScanDiff(
            devices_changed=[
                DeviceDiff(
                    nvr_ip="10.0.0.1",
                    status="changed",
                    field_changes=[FieldChange(field="firmware", old="5.1", new="5.2")],
                )
            ],
        )
        result = json.loads(format_diff_json(diff))
        assert len(result["devices_changed"]) == 1
        assert result["devices_changed"][0]["field_changes"][0]["field"] == "firmware"


# ── format_diff_summary ──────────────────────────────────────────────


class TestFormatDiffSummary:
    def test_no_changes(self):
        diff = ScanDiff(old_device_count=3, new_device_count=3, unchanged_count=3)
        s = format_diff_summary(diff)
        assert "3 → 3" in s
        assert "3 unchanged" in s

    def test_mixed_changes(self):
        diff = ScanDiff(
            old_device_count=3,
            new_device_count=4,
            devices_added=[DeviceDiff(nvr_ip="x", status="added")],
            devices_changed=[DeviceDiff(nvr_ip="y", status="changed")],
            unchanged_count=2,
        )
        s = format_diff_summary(diff)
        assert "+1 added" in s
        assert "~1 changed" in s
        assert "2 unchanged" in s


# ── CLI integration ──────────────────────────────────────────────────


class TestDiffCli:
    def test_diff_console_output(self, tmp_path):
        old_results = [_make_result(ip="10.0.0.1")]
        new_results = [_make_result(ip="10.0.0.1", firmware="6.0"), _make_result(ip="10.0.0.2", hostname="NVR2")]
        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.json"
        _write_scan_file(old_path, old_results)
        _write_scan_file(new_path, new_results)

        from pytvt.cli import diff_cli

        with patch("sys.argv", ["pytvt-diff", str(old_path), str(new_path)]), patch("builtins.print") as mock_print:
            diff_cli()
            output = mock_print.call_args[0][0]
            assert "CHANGED DEVICES" in output
            assert "ADDED DEVICES" in output

    def test_diff_json_output(self, tmp_path):
        old_results = [_make_result(ip="10.0.0.1")]
        new_results = [_make_result(ip="10.0.0.1"), _make_result(ip="10.0.0.2")]
        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.json"
        _write_scan_file(old_path, old_results)
        _write_scan_file(new_path, new_results)

        from pytvt.cli import diff_cli

        with (
            patch("sys.argv", ["pytvt-diff", str(old_path), str(new_path), "--json"]),
            patch("builtins.print") as mock_print,
        ):
            diff_cli()
            output = mock_print.call_args[0][0]
            parsed = json.loads(output)
            assert len(parsed["devices_added"]) == 1

    def test_diff_summary_output(self, tmp_path):
        old_results = [_make_result()]
        new_results = [_make_result()]
        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.json"
        _write_scan_file(old_path, old_results)
        _write_scan_file(new_path, new_results)

        from pytvt.cli import diff_cli

        with (
            patch("sys.argv", ["pytvt-diff", str(old_path), str(new_path), "--summary"]),
            patch("builtins.print") as mock_print,
        ):
            diff_cli()
            output = mock_print.call_args[0][0]
            assert "1 unchanged" in output

    def test_diff_bad_file(self, tmp_path):
        bad_path = tmp_path / "nope.json"
        good_path = tmp_path / "good.json"
        _write_scan_file(good_path, [_make_result()])

        from pytvt.cli import diff_cli

        with patch("sys.argv", ["pytvt-diff", str(bad_path), str(good_path)]):
            with pytest.raises(SystemExit) as exc_info:
                diff_cli()
            assert exc_info.value.code == 1

    def test_diff_invalid_json(self, tmp_path):
        bad_path = tmp_path / "bad.json"
        bad_path.write_text("not json")
        good_path = tmp_path / "good.json"
        _write_scan_file(good_path, [_make_result()])

        from pytvt.cli import diff_cli

        with patch("sys.argv", ["pytvt-diff", str(bad_path), str(good_path)]):
            with pytest.raises(SystemExit) as exc_info:
                diff_cli()
            assert exc_info.value.code == 1


# ── End-to-end with real JSON files ──────────────────────────────────


class TestEndToEnd:
    def test_full_cycle(self, tmp_path):
        """Load files, diff, format text, format JSON — full pipeline."""
        old = [
            _make_result(
                ip="10.0.0.1",
                firmware="5.1",
                cameras=[
                    CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Online"),
                    CameraInfo(channel=1, name="Parking", address="192.168.1.101", port=9008, status="Online"),
                ],
            ),
            _make_result(ip="10.0.0.2", hostname="NVR2"),
        ]
        new = [
            _make_result(
                ip="10.0.0.1",
                firmware="5.2",
                cameras=[
                    CameraInfo(channel=0, name="Lobby", address="192.168.1.100", port=9008, status="Offline"),
                    CameraInfo(channel=2, name="Entrance", address="192.168.1.102", port=9008, status="Online"),
                ],
            ),
            _make_result(ip="10.0.0.3", hostname="NVR3"),
        ]

        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.json"
        _write_scan_file(old_path, old)
        _write_scan_file(new_path, new)

        # Load
        old_loaded = load_scan_file(old_path)
        new_loaded = load_scan_file(new_path)
        assert len(old_loaded) == 2
        assert len(new_loaded) == 2

        # Diff
        diff = diff_scans(old_loaded, new_loaded, old_file=str(old_path), new_file=str(new_path))

        assert len(diff.devices_added) == 1  # 10.0.0.3
        assert diff.devices_added[0].nvr_ip == "10.0.0.3"

        assert len(diff.devices_removed) == 1  # 10.0.0.2
        assert diff.devices_removed[0].nvr_ip == "10.0.0.2"

        assert len(diff.devices_changed) == 1  # 10.0.0.1
        dd = diff.devices_changed[0]
        assert any(fc.field == "firmware" for fc in dd.field_changes)
        assert len(dd.cameras_added) == 1  # ch 2
        assert len(dd.cameras_removed) == 1  # ch 1
        assert len(dd.cameras_changed) == 1  # ch 0 status

        # Text output
        text = format_diff_text(diff)
        assert "ADDED" in text
        assert "REMOVED" in text
        assert "CHANGED" in text

        # JSON output
        j = json.loads(format_diff_json(diff))
        assert j["old_device_count"] == 2
        assert j["new_device_count"] == 2

        # Summary
        s = format_diff_summary(diff)
        assert "+1 added" in s
        assert "-1 removed" in s
        assert "~1 changed" in s
