import io
import zipfile
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from pytvt.maintenance import MaintenanceAuthenticationError, MaintenanceClient, MaintenanceError, analyze_archive


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


def test_login_matches_deviceweb_challenge_and_management_web_proof():
    import xml.etree.ElementTree as ET

    from pytvt.platform_sdk.web_crypto import login_password_hash

    # Non-secret vector: DeviceWeb SparkMD5.hash("test") returns uppercase
    # 098F6BCD4621D373CADE4E832627B4F6; CryptoJS hashes that string + nonce.
    expected = "b67d6792845d8fe500e3c205c6d6a887a135203154cf8464bb1d2892efd94115"
    client = MaintenanceClient("10.0.0.1", "admin", "test")
    challenge = ET.fromstring(
        "<response><content><token>a</token><nonce>b</nonce><sessionId>{c}</sessionId></content></response>"
    )
    client._post = Mock(side_effect=[challenge, ET.fromstring("<response/>")])
    client.login()
    endpoint, content = client._post.call_args.args
    request = ET.fromstring("<request>" + content + "</request>")
    assert endpoint == "doLogin"
    assert request.findtext("content/password") == expected
    assert login_password_hash("test", "b") == expected
    assert client.session == "c"
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


def test_resource_sample_never_downloads_or_guesses_unknown_units(monkeypatch):
    client = MaintenanceClient("example.test", "operator", "fixture")
    monkeypatch.setattr(
        client,
        "system_info",
        lambda: {
            "cpu": "99.5%",
            "memory": "2048MB",
            "serverRunTime": "3 days",
            "collected_at": "2026-10-18T12:00:00+00:00",
        },
    )
    monkeypatch.setattr(client, "download", lambda *a, **k: pytest.fail("must not export logs"))
    result = client.resource_sample()
    assert result.cpu_percent == 99.5
    assert result.memory_percent is None
    assert result.uptime == "3 days"


@pytest.mark.parametrize("value", ["", "nan", "inf", "-1", "101%", "unavailable"])
def test_resource_sample_invalid_percentage_is_unknown(monkeypatch, value):
    client = MaintenanceClient("example.test", "operator", "fixture")
    monkeypatch.setattr(
        client,
        "system_info",
        lambda: {
            "cpu": value,
            "memory": value,
            "collected_at": "2026-10-18T12:00:00+00:00",
        },
    )
    assert client.resource_sample().cpu_percent is None
    assert client.resource_sample().memory_percent is None


@pytest.mark.parametrize("endpoint", ["reqLogin", "doLogin"])
def test_authentication_rejection_is_typed_and_not_retried(endpoint):
    client = MaintenanceClient("example.test", "operator", "fixture")
    connection = Mock()
    response = Mock()
    response.read.return_value = b"<response><status>failed</status></response>"
    client._request = Mock(return_value=(connection, response))
    with pytest.raises(MaintenanceAuthenticationError):
        client._post(endpoint)
    client._request.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.parametrize(("text", "expected"), [("4195M", 4195), ("4GiB", 4096), ("bad", None), ("95%", None)])
def test_memory_size_keeps_units_separate_from_percentage(text, expected):
    from pytvt.maintenance import _memory_mib

    assert _memory_mib(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("06:23:57,3Days", 282237), ("00:00:00,0Days", 0), ("25:00:00,1Days", None), ("unknown", None)],
)
def test_runtime_normalization(text, expected):
    from pytvt.maintenance import _uptime_seconds

    assert _uptime_seconds(text) == expected
