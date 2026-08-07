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
from pytvt.device_sdk import manager as manager_module
from pytvt.device_sdk.http_client import RtspUrlResult, SnapshotAttempt
from pytvt.device_sdk.manager import Backend, DeviceManager

CREDS = dict(ip="10.0.0.1", username="admin", password="pass123")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 50
RTSP = "rtsp://10.0.0.1:554/chID=1&streamType=main"
ORIGINAL_HTTP_RTSP_URL = DeviceManager._http_rtsp_url


@pytest.fixture(autouse=True)
def clear_rtsp_url_cache():
    manager_module._RTSP_URL_CACHE.clear()
    yield
    manager_module._RTSP_URL_CACHE.clear()


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
        proc = MagicMock(returncode=1, stdout=b"", stderr=b"")
        with patch.object(xml_api.subprocess, "run", return_value=proc):
            assert xml_api.rtsp_snapshot_bytes(RTSP) is None

    def test_none_on_empty_stdout(self):
        proc = MagicMock(returncode=0, stdout=b"")
        with patch.object(xml_api.subprocess, "run", return_value=proc):
            assert xml_api.rtsp_snapshot_bytes(RTSP) is None

    def test_raises_when_ffmpeg_missing(self):
        """A missing binary is a deployment fault, not a frameless stream.

        This used to return ``None`` like every other failure, so an image
        shipped without ffmpeg reported "RTSP stream yielded no frame" and
        looked like a broken recorder. It has to be its own signal.
        """
        with patch.object(xml_api.subprocess, "run", side_effect=FileNotFoundError):
            with pytest.raises(xml_api.FfmpegUnavailable):
                xml_api.rtsp_snapshot_bytes(RTSP)

    def test_none_on_timeout(self):
        with patch.object(xml_api.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 5)):
            assert xml_api.rtsp_snapshot_bytes(RTSP) is None

    def test_wall_timeout_caps_the_ffmpeg_process(self):
        proc = MagicMock(returncode=0, stdout=JPEG)
        with patch.object(xml_api.subprocess, "run", return_value=proc) as run:
            assert (
                xml_api.rtsp_snapshot_bytes(
                    RTSP,
                    timeout=3,
                    wall_timeout=1.25,
                )
                == JPEG
            )

        assert run.call_args.kwargs["timeout"] == 1.25

    def test_shares_args_with_file_variant(self):
        # The file and bytes paths must build the same core ffmpeg frame grab.
        base = xml_api._ffmpeg_rtsp_frame_args(RTSP, 10)
        assert base[:2] == ["ffmpeg", "-y"]
        assert "tcp" in base and RTSP in base and "-q:v" in base


class TestRtspSnapshotDiagnostics:
    def test_auth_failure_is_typed_without_returning_the_secret_url(self):
        secret_url = "rtsp://admin:super-secret@10.0.0.1/stream"
        proc = MagicMock(
            returncode=1,
            stdout=b"",
            stderr=(f"{secret_url}: Server returned 401 Unauthorized").encode(),
        )

        with patch.object(xml_api.subprocess, "run", return_value=proc):
            result = xml_api.rtsp_snapshot_attempt_bytes(secret_url)

        assert result.error_kind == "rtsp_auth"
        assert result.error == "RTSP authentication was rejected."
        assert "super-secret" not in result.error

    def test_timeout_is_distinct_from_an_empty_stream(self):
        with patch.object(
            xml_api.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("ffmpeg", 5),
        ):
            result = xml_api.rtsp_snapshot_attempt_bytes(RTSP)

        assert result.error_kind == "rtsp_timeout"
        assert "timed out" in result.error

    def test_decoder_failure_is_typed(self):
        proc = MagicMock(
            returncode=1,
            stdout=b"",
            stderr=b"Invalid data found when processing input",
        )

        with patch.object(xml_api.subprocess, "run", return_value=proc):
            result = xml_api.rtsp_snapshot_attempt_bytes(RTSP)

        assert result.error_kind == "rtsp_decode"


class TestManagerSnapshotPrefersRtsp:
    @pytest.fixture
    def mgr(self) -> DeviceManager:
        return DeviceManager(**CREDS, backend=Backend.SDK_HTTP)

    def test_rtsp_first_returns_without_sdk_fallback(self, mgr):
        ok_url = RtspUrlResult(success=True, rtsp_url=RTSP)
        with (
            patch.object(mgr, "rtsp_url", return_value=ok_url),
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                return_value=xml_api.RtspFrameGrabResult(image=JPEG),
            ) as grab,
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
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                return_value=xml_api.RtspFrameGrabResult(
                    error="RTSP stream produced no JPEG frame.",
                    error_kind="empty_frame",
                ),
            ),
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
            patch.object(xml_api, "rtsp_snapshot_attempt_bytes") as grab,
            patch.object(mgr, "_get_http") as http,
        ):
            http.return_value.snapshot.return_value = JPEG
            out = mgr.snapshot(channel=1)
        assert out == JPEG
        grab.assert_not_called()  # never grab without a real URL
        http.return_value.snapshot.assert_called_once()

    def test_prefer_rtsp_false_skips_rtsp(self, mgr):
        with (
            patch.object(xml_api, "rtsp_snapshot_attempt_bytes") as grab,
            patch.object(mgr, "_get_http") as http,
        ):
            http.return_value.snapshot.return_value = JPEG
            out = mgr.snapshot(channel=1, prefer_rtsp=False)
        assert out == JPEG
        grab.assert_not_called()

    def test_semantic_substream_is_mapped_inside_the_wrapper(self, mgr):
        with patch.object(
            mgr,
            "_rtsp_snapshot_attempt",
            return_value=SnapshotAttempt(image=JPEG, method="rtsp"),
        ) as rtsp:
            attempt = mgr.snapshot_attempt(channel=1, stream="sub")

        assert attempt.image == JPEG
        rtsp.assert_called_once_with(
            channel=1,
            stream_type=1,
            timeout=10,
            deadline=None,
            allow_resolver_fallback=True,
        )

    def test_unknown_semantic_stream_is_rejected(self, mgr):
        with pytest.raises(ValueError, match="main, sub, third"):
            mgr.snapshot_attempt(channel=1, stream="unsupported")

    def test_bounded_direct_mode_does_not_enter_bridge_fallback(self, mgr):
        with (
            patch.object(mgr, "_http_rtsp_url", return_value=None),
            patch.object(mgr, "rtsp_url") as bridge_url,
            patch.object(mgr, "_get_http") as bridge_snapshot,
        ):
            attempt = mgr.snapshot_attempt(
                channel=1,
                stream="sub",
                total_timeout=3,
                allow_fallback=False,
            )

        assert attempt.success is False
        assert attempt.error_kind == "no_stream_url"
        bridge_url.assert_not_called()
        bridge_snapshot.assert_not_called()

    def test_netsdk_backend_falls_back_when_rtsp_fails(self):
        mgr = DeviceManager(**CREDS, backend=Backend.NETSDK)
        ok_url = RtspUrlResult(success=True, rtsp_url=RTSP)
        with (
            patch.object(mgr, "rtsp_url", return_value=ok_url),
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                return_value=xml_api.RtspFrameGrabResult(
                    error="RTSP stream produced no JPEG frame.",
                    error_kind="empty_frame",
                ),
            ),
            # The NETSDK leg now reports WHY it produced no image, so the seam
            # snapshot() reaches is the attempt-returning helper. _netsdk_snapshot
            # remains as the bytes|None form for direct callers.
            patch.object(
                mgr,
                "_netsdk_snapshot_attempt",
                return_value=SnapshotAttempt(image=JPEG, method="netsdk"),
            ) as netsdk,
        ):
            out = mgr.snapshot(channel=1)
        assert out == JPEG
        netsdk.assert_called_once()


class TestRtspUrlIsResolvedWithoutANativeLogin:
    """The RTSP leg must not pay for a NetSDK session just to learn a URL.

    ``NET_SDK_GetRtspUrl`` needs a native login, measured at ~5.9s on a loaded
    Palmares recorder. The bridge runs each capture in a throwaway process, so
    that cost was paid on every single snapshot. The web CGI answers the same
    question over HTTP.
    """

    def test_http_resolved_url_is_used_and_the_native_resolver_is_never_called(self):
        mgr = DeviceManager(**CREDS, backend=Backend.NETSDK)
        with (
            patch.object(DeviceManager, "_http_rtsp_url", return_value=RTSP) as http_url,
            patch.object(mgr, "rtsp_url") as native_url,
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                return_value=xml_api.RtspFrameGrabResult(image=JPEG),
            ) as grab,
        ):
            attempt = mgr._rtsp_snapshot_attempt(channel=1)

        assert attempt.success is True
        assert attempt.image == JPEG
        http_url.assert_called_once()
        native_url.assert_not_called()
        assert grab.call_args.args[0] == RTSP

    @pytest.mark.parametrize(
        ("stream_type", "profile"),
        ((0, "profile1"), (1, "profile2"), (2, "profile3")),
    )
    def test_standalone_camera_uses_its_standard_profile_without_nvr_login(
        self,
        stream_type,
        profile,
        monkeypatch,
    ):
        monkeypatch.setattr(
            DeviceManager,
            "_http_rtsp_url",
            ORIGINAL_HTTP_RTSP_URL,
        )
        mgr = DeviceManager(
            ip="10.0.0.25",
            username="operator@example.com",
            password="p@ss/word",
            backend=Backend.NETSDK,
            direct_camera=True,
        )

        with patch.object(xml_api, "NvrClient") as nvr_client:
            url = mgr._http_rtsp_url(channel=0, stream_type=stream_type)

        assert url == (f"rtsp://operator%40example.com:p%40ss%2Fword@10.0.0.25:554/{profile}")
        nvr_client.assert_not_called()

    def test_standalone_camera_uses_a_configured_rtsp_port(self, monkeypatch):
        monkeypatch.setattr(
            DeviceManager,
            "_http_rtsp_url",
            ORIGINAL_HTTP_RTSP_URL,
        )
        mgr = DeviceManager(
            **CREDS,
            backend=Backend.NETSDK,
            direct_camera=True,
            rtsp_port=8554,
        )

        assert mgr._http_rtsp_url() == "rtsp://admin:pass123@10.0.0.1:8554/profile1"

    @pytest.mark.parametrize("rtsp_port", (True, 0, 65536))
    def test_rejects_an_invalid_rtsp_port(self, rtsp_port):
        with pytest.raises(ValueError, match="rtsp_port"):
            DeviceManager(
                **CREDS,
                backend=Backend.NETSDK,
                direct_camera=True,
                rtsp_port=rtsp_port,
            )

    def test_native_resolver_still_covers_a_recorder_the_web_cgi_cannot_answer(self):
        mgr = DeviceManager(**CREDS, backend=Backend.NETSDK)
        with (
            patch.object(DeviceManager, "_http_rtsp_url", return_value=None),
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=True, rtsp_url=RTSP)) as native_url,
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                return_value=xml_api.RtspFrameGrabResult(image=JPEG),
            ),
        ):
            attempt = mgr._rtsp_snapshot_attempt(channel=1)

        assert attempt.success is True
        native_url.assert_called_once()

    def test_a_missing_ffmpeg_is_not_reported_as_an_empty_stream(self):
        """The regression that hid this: a deployment fault blamed on the camera."""
        mgr = DeviceManager(**CREDS, backend=Backend.NETSDK)
        with (
            patch.object(DeviceManager, "_http_rtsp_url", return_value=RTSP),
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                side_effect=xml_api.FfmpegUnavailable("ffmpeg is not installed"),
            ),
        ):
            attempt = mgr._rtsp_snapshot_attempt(channel=1)

        assert attempt.success is False
        assert attempt.error_kind == "rtsp_error"
        assert attempt.error_kind != "empty_frame"
        assert "ffmpeg" in attempt.error


class TestCameraDirectRtspUrlCache:
    def test_reuses_a_resolved_url_without_a_second_login(self, monkeypatch):
        monkeypatch.setattr(
            DeviceManager,
            "_http_rtsp_url",
            ORIGINAL_HTTP_RTSP_URL,
        )
        nvr = MagicMock()
        nvr.__enter__.return_value = nvr
        nvr.get_rtsp_url.return_value = RTSP
        mgr = DeviceManager(
            **CREDS,
            backend=Backend.SDK_HTTP,
            rtsp_url_cache_ttl=60,
        )

        with patch.object(xml_api, "NvrClient", return_value=nvr) as client:
            first = mgr._http_rtsp_url(channel=1, stream_type=1)
            second = mgr._http_rtsp_url(channel=1, stream_type=1)

        assert first == second == RTSP
        client.assert_called_once()
        nvr.login.assert_called_once()
        nvr.get_rtsp_url.assert_called_once_with(2, "sub")

    def test_password_is_fingerprinted_and_rotation_misses_the_old_entry(self, monkeypatch):
        monkeypatch.setattr(
            DeviceManager,
            "_http_rtsp_url",
            ORIGINAL_HTTP_RTSP_URL,
        )
        first_nvr = MagicMock()
        first_nvr.__enter__.return_value = first_nvr
        first_nvr.get_rtsp_url.return_value = RTSP
        rotated_nvr = MagicMock()
        rotated_nvr.__enter__.return_value = rotated_nvr
        rotated_nvr.get_rtsp_url.return_value = RTSP
        first = DeviceManager(
            **CREDS,
            backend=Backend.SDK_HTTP,
            rtsp_url_cache_ttl=60,
        )
        rotated = DeviceManager(
            **{**CREDS, "password": "rotated"},
            backend=Backend.SDK_HTTP,
            rtsp_url_cache_ttl=60,
        )

        with patch.object(
            xml_api,
            "NvrClient",
            side_effect=(first_nvr, rotated_nvr),
        ) as client:
            assert first._http_rtsp_url(channel=1) == RTSP
            assert rotated._http_rtsp_url(channel=1) == RTSP

        assert client.call_count == 2
        key = first._rtsp_url_cache_key(channel=1, stream_type=0)
        assert CREDS["password"] not in repr(key)

    def test_failed_frame_evicts_the_cached_url(self, monkeypatch):
        monkeypatch.setattr(
            DeviceManager,
            "_http_rtsp_url",
            ORIGINAL_HTTP_RTSP_URL,
        )
        nvr = MagicMock()
        nvr.__enter__.return_value = nvr
        nvr.get_rtsp_url.return_value = RTSP
        mgr = DeviceManager(
            **CREDS,
            backend=Backend.SDK_HTTP,
            rtsp_url_cache_ttl=60,
        )

        with (
            patch.object(xml_api, "NvrClient", return_value=nvr),
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                return_value=xml_api.RtspFrameGrabResult(
                    error="RTSP stream produced no JPEG frame.",
                    error_kind="empty_frame",
                ),
            ),
        ):
            assert mgr._rtsp_snapshot_attempt(channel=1).success is False
            assert mgr._http_rtsp_url(channel=1) == RTSP

        assert nvr.login.call_count == 2

    def test_ttl_and_lru_bound_are_enforced(self):
        cache = manager_module._RtspUrlCache(max_entries=2)
        key1 = ("nvr-1", 80, "admin", b"a", 0, 1)
        key2 = ("nvr-1", 80, "admin", b"a", 1, 1)
        key3 = ("nvr-1", 80, "admin", b"a", 2, 1)
        cache.set(key1, "rtsp://one", expires_at=20)
        cache.set(key2, "rtsp://two", expires_at=20)
        cache.set(key3, "rtsp://three", expires_at=20)

        assert cache.get(key1, now=10) is None
        assert cache.get(key2, now=10) == "rtsp://two"
        assert cache.get(key3, now=20) is None


class TestFfmpegAvailability:
    def test_reports_present_when_on_path(self):
        with patch.object(xml_api.shutil, "which", return_value="/usr/bin/ffmpeg"):
            assert xml_api.ffmpeg_available() is True

    def test_reports_absent_when_not_on_path(self):
        with patch.object(xml_api.shutil, "which", return_value=None):
            assert xml_api.ffmpeg_available() is False


class TestFrameGrabIsTunedForASingleStill:
    """Measured on a live Palmares channel: probing was ~half the grab time."""

    def test_input_probing_is_disabled_before_the_input(self):
        argv = xml_api._ffmpeg_rtsp_frame_args(RTSP, 10)
        i = argv.index("-i")
        for flag, value in (("-probesize", "32"), ("-analyzeduration", "0")):
            assert flag in argv[:i], f"{flag} must precede -i to affect input probing"
            assert argv[argv.index(flag) + 1] == value

    def test_low_latency_input_flags_are_set(self):
        argv = xml_api._ffmpeg_rtsp_frame_args(RTSP, 10)
        assert argv[argv.index("-fflags") + 1] == "nobuffer"
        assert argv[argv.index("-flags") + 1] == "low_delay"

    def test_audio_is_not_decoded_for_a_still(self):
        assert "-an" in xml_api._ffmpeg_rtsp_frame_args(RTSP, 10)
