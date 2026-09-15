import io
import zipfile
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from pytvt.maintenance import MaintenanceClient, MaintenanceError, analyze_archive


def archive(lines, name="opt/NVMS/DeviceWeb/operationsLog/IntelligentAnalysisServer_log/IntelligentAnalysisServer.log"):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as z:
        z.writestr(name, lines)
    target.seek(0)
    return target


def analyze(target):
    return analyze_archive(
        target,
        server="10.0.0.1",
        tz_name="America/Puerto_Rico",
        start=datetime(2026, 9, 15, tzinfo=timezone.utc),
        end=datetime(2026, 9, 16, tzinfo=timezone.utc),
        source_sha256="a" * 64,
    )


def test_message_counts_and_no_raw_secrets():
    text = (
        "2026-09-15 14:00:00,001 [x] INFO - not enough thread to process 1400input\n"
        "2026-09-15 14:00:00,002 [x] ERROR - losolmos does not have device authority, device name is A\n"
        "2026-09-15 14:01:00,002 [x] ERROR - password=private unknown problem\n"
        "2026-09-14 14:00:00,001 [x] INFO - not enough thread to process 1400input\n"
    )
    result, meta = analyze(archive(text))
    assert {r["code"]: r["value"] for r in result["observations"]} == {
        "worker_capacity": 1,
        "device_authority": 1,
        "unclassified_errors": 1,
    }
    assert "private" not in str(result) + str(meta)
    assert result["observations"][0]["window_start"].endswith("-04:00")
    assert any(r["subject"] == "losolmos" for r in result["observations"])


def test_multiline_license_and_oversized_line():
    text = (
        "2026-09-15 14:00:00,001 [x] INFO - license\n<maxDecodeOutputCount>128</maxDecodeOutputCount>\n"
        + "x" * 20000
        + "\n"
    )
    result, meta = analyze(archive(text, "AuthenticationServer_log/AuthenticationServer.log"))
    assert result["observations"][0]["value"] == 128
    assert meta["oversized_lines_skipped"] == 1


def test_reject_cross_origin_export_before_download():
    import xml.etree.ElementTree as ET

    client = MaintenanceClient("10.0.0.1", "admin", "test")
    client._post = Mock(
        return_value=ET.fromstring(
            "<response><content><logPackageUrl>http://other.test/steal</logPackageUrl></content></response>"
        )
    )
    client._request = Mock()
    with pytest.raises(MaintenanceError, match="origin"):
        client.download(io.BytesIO())
    client._request.assert_not_called()


def test_login_rejection_is_not_retried():
    import xml.etree.ElementTree as ET

    client = MaintenanceClient("10.0.0.1", "admin", "test")
    challenge = ET.fromstring(
        "<response><content><token>a</token><nonce>b</nonce><sessionId>{c}</sessionId></content></response>"
    )
    client._post = Mock(side_effect=[challenge, MaintenanceError("rejected")])
    with pytest.raises(MaintenanceError, match="rejected"):
        client.login()
    assert client._post.call_count == 2


def test_archive_limits_checked_before_read():
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as z:
        for i in range(513):
            z.writestr(str(i), "")
    target.seek(0)
    with pytest.raises(MaintenanceError, match="limits"):
        analyze(target)


def test_resource_samples_use_collection_clock_and_cleanup():
    from unittest.mock import patch

    from pytvt.maintenance import collect_audit

    client = Mock()
    client.system_info.return_value = {
        "cpu": "99.5%",
        "serverTime": "2099-01-01 00:00:00",
        "collected_at": "2026-09-15T19:00:00+00:00",
    }
    client.download.return_value = ("a" * 64, 123)
    with patch("pytvt.maintenance.analyze_archive", return_value=({"schema": 1, "observations": []}, {})):
        result, _ = collect_audit(
            client, tz_name="America/Puerto_Rico", start=datetime.now(timezone.utc), end=datetime.now(timezone.utc)
        )
    assert len(result["observations"]) == 2
    assert result["observations"][0]["window_end"] == "2026-09-15T19:00:00+00:00"
    client.logout.assert_called_once()
