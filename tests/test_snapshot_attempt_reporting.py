"""A snapshot that produces no image must say WHY.

``DeviceManager.snapshot`` returns ``bytes | None``, and every failure used to
arrive as ``None``: a failed login, an unsupported capability, a socket
timeout, and a recorder with genuinely nothing to send were indistinguishable.
The NETSDK leg made that worse with a bare ``except Exception: return None``,
which discarded the vendor error one frame before anything could record it.

Downstream that surfaced to operators as "Device returned empty snapshot" for
every cause, with the real reason recoverable only from a stack trace nobody
logged. These tests pin the reporting path and the backward compatibility of
the original method.

No network: the SDK session, HTTP client and RTSP grab are all mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pytvt import xml_api
from pytvt.device_sdk.http_client import RtspUrlResult, SnapshotAttempt
from pytvt.device_sdk.manager import Backend, DeviceManager

CREDS = dict(ip="10.0.0.1", username="admin", password="pass123")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 50
RTSP = "rtsp://10.0.0.1:554/chID=1&streamType=main"


def _manager(backend=Backend.NETSDK) -> DeviceManager:
    return DeviceManager(**CREDS, backend=backend)


class TestSnapshotAttemptShape:
    def test_success_is_derived_from_the_image(self):
        assert SnapshotAttempt(image=JPEG, method="rtsp").success is True

    def test_a_failure_carries_no_image_and_is_not_successful(self):
        attempt = SnapshotAttempt(error="boom", error_kind="sdk_error")

        assert attempt.success is False
        assert attempt.image is None


class TestNetsdkLegReportsTheVendorError:
    """The bare except that hid every cause."""

    def test_capture_exception_is_preserved(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.side_effect = RuntimeError("NET_SDK login failed (err=7)")

        with patch.object(mgr, "_get_netsdk_session", return_value=session):
            attempt = mgr._netsdk_snapshot_attempt(channel=3)

        assert attempt.success is False
        assert attempt.error_kind == "sdk_error"
        # The vendor's own words must survive to the caller.
        assert "NET_SDK login failed (err=7)" in attempt.error

    def test_session_failure_is_distinct_from_capture_failure(self):
        # Never reaching a session points somewhere different from a capture
        # call that ran and failed, so the kinds differ.
        mgr = _manager()

        with patch.object(
            mgr, "_get_netsdk_session", side_effect=OSError("connection refused")
        ):
            attempt = mgr._netsdk_snapshot_attempt(channel=3)

        assert attempt.error_kind == "session_error"
        assert "connection refused" in attempt.error

    def test_empty_buffer_is_reported_as_empty_not_as_an_error(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.return_value = b""

        with patch.object(mgr, "_get_netsdk_session", return_value=session):
            attempt = mgr._netsdk_snapshot_attempt(channel=3)

        assert attempt.error_kind == "empty_frame"

    def test_success_returns_the_bytes(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.return_value = JPEG

        with patch.object(mgr, "_get_netsdk_session", return_value=session):
            attempt = mgr._netsdk_snapshot_attempt(channel=3)

        assert attempt.image == JPEG
        assert attempt.method == "netsdk"
        assert attempt.error == ""


class TestRtspLegSeparatesItsTwoFailures:
    def test_missing_stream_url_is_its_own_kind(self):
        mgr = _manager()
        result = RtspUrlResult(success=False, error="channel out of range")

        with patch.object(mgr, "rtsp_url", return_value=result):
            attempt = mgr._rtsp_snapshot_attempt(channel=99)

        assert attempt.error_kind == "no_stream_url"
        assert "channel out of range" in attempt.error

    def test_url_that_yields_no_frame_is_reported_separately(self):
        # A recorder that hands over a URL but no frame is a different problem
        # from one that never had a URL.
        mgr = _manager()
        result = RtspUrlResult(success=True, rtsp_url=RTSP)

        with patch.object(mgr, "rtsp_url", return_value=result), patch.object(xml_api, "rtsp_snapshot_bytes", return_value=b""):
            attempt = mgr._rtsp_snapshot_attempt(channel=1)

        assert attempt.error_kind == "empty_frame"

    def test_grab_exception_is_preserved(self):
        mgr = _manager()
        result = RtspUrlResult(success=True, rtsp_url=RTSP)

        with patch.object(mgr, "rtsp_url", return_value=result), patch.object(
            xml_api, "rtsp_snapshot_bytes", side_effect=TimeoutError("ffmpeg timed out")
        ):
            attempt = mgr._rtsp_snapshot_attempt(channel=1)

        assert attempt.error_kind == "rtsp_error"
        assert "ffmpeg timed out" in attempt.error


class TestFallbackReportsTheMostSpecificReason:
    def test_netsdk_error_wins_over_a_missing_rtsp_url(self):
        """The interesting failure must not be masked by the boring one.

        RTSP having no URL is the common, uninformative case; the SDK error is
        what an operator needs.
        """
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.side_effect = RuntimeError("capability not supported")

        with patch.object(
            mgr, "rtsp_url", return_value=RtspUrlResult(success=False, error="no url")
        ), patch.object(mgr, "_get_netsdk_session", return_value=session):
            attempt = mgr.snapshot_attempt(channel=2)

        assert attempt.error_kind == "sdk_error"
        assert "capability not supported" in attempt.error

    def test_a_working_rtsp_leg_short_circuits(self):
        mgr = _manager()

        with patch.object(
            mgr, "rtsp_url", return_value=RtspUrlResult(success=True, rtsp_url=RTSP)
        ), patch.object(xml_api, "rtsp_snapshot_bytes", return_value=JPEG):
            attempt = mgr.snapshot_attempt(channel=1)

        assert attempt.image == JPEG
        assert attempt.method == "rtsp"


class TestBackwardCompatibility:
    """``snapshot`` keeps its signature and its ``bytes | None`` contract."""

    def test_snapshot_still_returns_bytes_on_success(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.return_value = JPEG

        with patch.object(
            mgr, "rtsp_url", return_value=RtspUrlResult(success=False)
        ), patch.object(mgr, "_get_netsdk_session", return_value=session):
            assert mgr.snapshot(channel=0) == JPEG

    def test_snapshot_still_returns_none_on_failure(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.side_effect = RuntimeError("boom")

        with patch.object(
            mgr, "rtsp_url", return_value=RtspUrlResult(success=False)
        ), patch.object(mgr, "_get_netsdk_session", return_value=session):
            assert mgr.snapshot(channel=0) is None

    def test_legacy_private_helpers_still_return_bytes_or_none(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.return_value = JPEG

        with patch.object(mgr, "_get_netsdk_session", return_value=session):
            assert mgr._netsdk_snapshot(channel=0) == JPEG
