"""Tests for pytvt.sdk_local — subprocess JSON extraction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pytvt.models import ScannerConfig
from pytvt.sdk_local import _extract_json, sdk_scan_local


class TestExtractJson:
    def test_valid_markers(self):
        stdout = 'noise noise\n___JSON_START___\n{"key": "value"}\n___JSON_END___\nmore noise'
        result = _extract_json(stdout)
        assert result == '{"key": "value"}'

    def test_no_markers(self):
        assert _extract_json("just some output") is None

    def test_only_start_marker(self):
        assert _extract_json('___JSON_START___\n{"a": 1}') is None

    def test_only_end_marker(self):
        assert _extract_json('{"a": 1}\n___JSON_END___') is None

    def test_empty_between_markers(self):
        result = _extract_json("___JSON_START___ ___JSON_END___")
        assert result == ""

    def test_multiline_json(self):
        stdout = '___JSON_START___\n{\n  "a": 1,\n  "b": 2\n}\n___JSON_END___'
        result = _extract_json(stdout)
        assert '"a": 1' in result
        assert '"b": 2' in result


class TestSdkScanLocal:
    def test_missing_scan_script_reports_configuration(self, sample_device):
        cfg = ScannerConfig(username="admin", password="test123", scan_script="/missing/scan_nvr.mjs")
        result = sdk_scan_local(sample_device, cfg)
        assert result.success is False
        assert "TVT_SCAN_SCRIPT" in (result.error or "")

    def test_sdk_path_forwarded_to_subprocess(self, sample_device, tmp_path):
        script = tmp_path / "scan_nvr.mjs"
        script.write_text("// bridge", encoding="utf-8")
        cfg = ScannerConfig(
            username="admin",
            password="test123",
            sdk_path="/opt/tvt-sdk",
            scan_script=str(script),
        )

        proc = MagicMock()
        proc.stdout = '___JSON_START___{"success": true, "cameras": []}___JSON_END___'
        proc.stderr = ""

        with patch("pytvt.sdk_local.subprocess.run", return_value=proc) as mock_run:
            result = sdk_scan_local(sample_device, cfg)

        assert result.success is True
        assert mock_run.call_args.kwargs["env"]["TVT_SDK_PATH"] == "/opt/tvt-sdk"
