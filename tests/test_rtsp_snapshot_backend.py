"""Tests for the direct-RTSP snapshot backend (OPS-SNAP-7).

Covers the byte-returning ffmpeg frame grab (``pytvt.xml_api.rtsp_snapshot_bytes``)
and the ``DeviceManager.snapshot`` preferred-RTSP path with transparent fallback
to the SDK/HTTP snapshot. No network — ffmpeg and the RTSP URL are mocked.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pytvt import xml_api
from pytvt.device_sdk.http_client import RtspUrlResult
from pytvt.device_sdk.manager import Backend, DeviceManager

CREDS = dict(ip="10.0.0.1", username="admin", password="pass123")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 50
RTSP = "rtsp://10.0.0.1:554/chID=1&streamType=main"


class TestRtspSnapshotBytes:
    def test_returns_stdout_on_success(self):
        proc = MagicMock(returncode=0, stdout=JPEG)
        with patch.object(xml_api.subprocess, "run", return_value=proc) as run:
            out = xml_api.rtsp_snapshot_bytes(RTSP, timeout=7)
        assert out == JPEG
        # frame grabbed to stdout, not a file
        argv = run.call_args.args[0]
        assert argv[0] == "ffmpeg" and "pipe:1" in argv and RTSP in argv
        assert "-frames:v" in argv and "1" in argv

    def test_none_on_nonzero_exit(self):
        proc = MagicMock(returncode=1, stdout=b"")
        with patch.object(xml_api.subprocess, "run", return_value=proc):
            assert xml_api.rtsp_snapshot_bytes(RTSP) is None

    def test_none_on_empty_stdout(self):
        proc = MagicMock(returncode=0, stdout=b"")
        with patch.object(xml_api.subprocess, "run", return_value=proc):
            assert xml_api.rtsp_snapshot_bytes(RTSP) is None

    def test_none_when_ffmpeg_missing(self):
        with patch.object(xml_api.subprocess, "run", side_effect=FileNotFoundError):
            assert xml_api.rtsp_snapshot_bytes(RTSP) is None

    def test_none_on_timeout(self):
        with patch.object(xml_api.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 5)):
            assert xml_api.rtsp_snapshot_bytes(RTSP) is None

    def test_shares_args_with_file_variant(self):
        # The file and bytes paths must build the same core ffmpeg frame grab.
        base = xml_api._ffmpeg_rtsp_frame_args(RTSP, 10)
        assert base[:2] == ["ffmpeg", "-y"]
        assert "tcp" in base and RTSP in base and "-q:v" in base


class TestManagerSnapshotPrefersRtsp:
    @pytest.fixture
    def mgr(self) -> DeviceManager:
        return DeviceManager(**CREDS, backend=Backend.SDK_HTTP)

    def test_rtsp_first_returns_without_sdk_fallback(self, mgr):
        ok_url = RtspUrlResult(success=True, rtsp_url=RTSP)
        with (
            patch.object(mgr, "rtsp_url", return_value=ok_url),
            patch.object(xml_api, "rtsp_snapshot_bytes", return_value=JPEG) as grab,
            patch.object(mgr, "_get_http") as http,
        ):
            out = mgr.snapshot(channel=1)
        assert out == JPEG
        grab.assert_called_once_with(RTSP, timeout=10)
        http.assert_not_called()  # RTSP succeeded -> no SDK/HTTP fallback

    def test_falls_back_to_http_when_rtsp_grab_fails(self, mgr):
        ok_url = RtspUrlResult(success=True, rtsp_url=RTSP)
        with (
            patch.object(mgr, "rtsp_url", return_value=ok_url),
            patch.object(xml_api, "rtsp_snapshot_bytes", return_value=None),
            patch.object(mgr, "_get_http") as http,
        ):
            http.return_value.snapshot.return_value = JPEG
            out = mgr.snapshot(channel=1)
        assert out == JPEG
        http.return_value.snapshot.assert_called_once()

    def test_falls_back_when_rtsp_url_unavailable(self, mgr):
        bad_url = RtspUrlResult(success=False, error="no url")
        with (
            patch.object(mgr, "rtsp_url", return_value=bad_url),
            patch.object(xml_api, "rtsp_snapshot_bytes") as grab,
            patch.object(mgr, "_get_http") as http,
        ):
            http.return_value.snapshot.return_value = JPEG
            out = mgr.snapshot(channel=1)
        assert out == JPEG
        grab.assert_not_called()  # never grab without a real URL
        http.return_value.snapshot.assert_called_once()

    def test_prefer_rtsp_false_skips_rtsp(self, mgr):
        with (
            patch.object(xml_api, "rtsp_snapshot_bytes") as grab,
            patch.object(mgr, "_get_http") as http,
        ):
            http.return_value.snapshot.return_value = JPEG
            out = mgr.snapshot(channel=1, prefer_rtsp=False)
        assert out == JPEG
        grab.assert_not_called()

    def test_netsdk_backend_falls_back_when_rtsp_fails(self):
        mgr = DeviceManager(**CREDS, backend=Backend.NETSDK)
        ok_url = RtspUrlResult(success=True, rtsp_url=RTSP)
        with (
            patch.object(mgr, "rtsp_url", return_value=ok_url),
            patch.object(xml_api, "rtsp_snapshot_bytes", return_value=None),
            patch.object(mgr, "_netsdk_snapshot", return_value=JPEG) as netsdk,
        ):
            out = mgr.snapshot(channel=1)
        assert out == JPEG
        netsdk.assert_called_once()
