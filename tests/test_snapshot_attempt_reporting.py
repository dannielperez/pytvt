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
from pytvt.device_sdk.client import NetSdkCapabilityError
from pytvt.device_sdk.http_client import RtspUrlResult, SnapshotAttempt
from pytvt.device_sdk.manager import Backend, DeviceManager
from pytvt.keyframe import KeyframeDecodeError, StillCapture

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

        with patch.object(mgr, "_get_netsdk_session", side_effect=OSError("connection refused")):
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

        with (
            patch.object(mgr, "rtsp_url", return_value=result),
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                return_value=xml_api.RtspFrameGrabResult(
                    error="RTSP stream produced no JPEG frame.",
                    error_kind="empty_frame",
                ),
            ),
        ):
            attempt = mgr._rtsp_snapshot_attempt(channel=1)

        assert attempt.error_kind == "empty_frame"

    def test_frame_grab_reason_reaches_the_snapshot_attempt(self):
        mgr = _manager()
        result = RtspUrlResult(success=True, rtsp_url=RTSP)

        with (
            patch.object(mgr, "rtsp_url", return_value=result),
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                return_value=xml_api.RtspFrameGrabResult(
                    error="RTSP authentication was rejected.",
                    error_kind="rtsp_auth",
                ),
            ),
        ):
            attempt = mgr._rtsp_snapshot_attempt(channel=1)

        assert attempt.error_kind == "rtsp_auth"
        assert attempt.error == "RTSP authentication was rejected."

    def test_grab_exception_is_preserved(self):
        mgr = _manager()
        result = RtspUrlResult(success=True, rtsp_url=RTSP)

        with (
            patch.object(mgr, "rtsp_url", return_value=result),
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                side_effect=TimeoutError("ffmpeg timed out"),
            ),
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
        # An SDK drop without the live-preview symbols reports an uninformative
        # "unsupported" keyframe leg that must not mask the real SDK error.
        session.capture_main_still.side_effect = NetSdkCapabilityError("no LivePlayEx")

        with (
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False, error="no url")),
            patch.object(mgr, "_get_netsdk_session", return_value=session),
        ):
            attempt = mgr.snapshot_attempt(channel=2)

        assert attempt.error_kind == "sdk_error"
        assert "capability not supported" in attempt.error

    def test_a_working_rtsp_leg_short_circuits(self):
        mgr = _manager()

        with (
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=True, rtsp_url=RTSP)),
            patch.object(
                xml_api,
                "rtsp_snapshot_attempt_bytes",
                return_value=xml_api.RtspFrameGrabResult(image=JPEG),
            ),
        ):
            attempt = mgr.snapshot_attempt(channel=1)

        assert attempt.image == JPEG
        assert attempt.method == "rtsp"


class TestWebApiLeg:
    """The LAPI ``GetSnapshot`` pre-leg: cheapest transport, tried first."""

    def test_webapi_success_short_circuits_every_other_leg(self):
        mgr = _manager()

        with (
            patch.object(
                DeviceManager,
                "_webapi_snapshot_attempt",
                return_value=SnapshotAttempt(image=JPEG, method="webapi"),
            ),
            patch.object(mgr, "rtsp_url") as rtsp,
            patch.object(mgr, "_get_netsdk_session") as netsdk,
        ):
            # ``sub`` is the cheapest-first contract; ``main`` is a resolution
            # contract the CIF Web API frame cannot satisfy (see TestMainStreamIntent).
            attempt = mgr.snapshot_attempt(channel=1, stream="sub")

        assert attempt.image == JPEG
        assert attempt.method == "webapi"
        rtsp.assert_not_called()
        netsdk.assert_not_called()

    def test_webapi_failure_falls_through_to_the_existing_legs(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.return_value = JPEG

        with (
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False)),
            patch.object(mgr, "_get_netsdk_session", return_value=session),
        ):
            attempt = mgr.snapshot_attempt(channel=1, stream="sub")

        assert attempt.image == JPEG
        assert attempt.method == "netsdk"

    def test_webapi_failure_does_not_mask_a_specific_sdk_error(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.side_effect = RuntimeError("NET_SDK login failed (err=7)")

        with (
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False)),
            patch.object(mgr, "_get_netsdk_session", return_value=session),
        ):
            attempt = mgr.snapshot_attempt(channel=1, stream="sub")

        assert attempt.error_kind == "sdk_error"
        assert "NET_SDK login failed (err=7)" in attempt.error

    def test_nat_connection_skips_the_webapi_leg(self):
        mgr = DeviceManager(
            ip=None,
            identifier="NAAC909BNQGD",
            username="admin",
            password="pass123",
            backend=Backend.NETSDK,
        )
        session = MagicMock()
        session.capture_jpeg.return_value = JPEG

        with (
            patch.object(
                DeviceManager,
                "_webapi_snapshot_attempt",
                side_effect=AssertionError("webapi leg must not run over NAT"),
            ),
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False)),
            patch.object(mgr, "_get_netsdk_session", return_value=session),
        ):
            attempt = mgr.snapshot_attempt(channel=1, stream="sub")

        assert attempt.image == JPEG

    def test_maps_client_result_into_a_snapshot_attempt(self, no_webapi_snapshot):
        from pytvt.web_api.models import SnapshotResult as WebSnapshotResult

        mgr = _manager()
        web_client = MagicMock()
        web_client.get_snapshot_webapi.return_value = WebSnapshotResult(
            success=True,
            image_data=JPEG,
            content_type="image/jpeg",
            method="webapi",
        )

        with patch("pytvt.web_api.client.WebApiClient", return_value=web_client) as client_cls:
            attempt = no_webapi_snapshot(mgr, channel=3)

        assert attempt.image == JPEG
        assert attempt.method == "webapi"
        # The web API numbers channels from 1; device_sdk from 0.
        web_client.get_snapshot_webapi.assert_called_once_with(channel_id=4)
        assert client_cls.call_args.kwargs["port"] == mgr._http_port

    def test_maps_client_failure_into_a_reported_attempt(self, no_webapi_snapshot):
        from pytvt.web_api.models import SnapshotResult as WebSnapshotResult

        mgr = _manager()
        web_client = MagicMock()
        web_client.get_snapshot_webapi.return_value = WebSnapshotResult(
            success=False,
            method="webapi",
            error="HTTP 404 from /LAPI/V1.0/Image/Channels/4/Snapshot",
        )

        with patch("pytvt.web_api.client.WebApiClient", return_value=web_client):
            attempt = no_webapi_snapshot(mgr, channel=3)

        assert attempt.success is False
        assert attempt.error_kind == "webapi_error"
        assert "HTTP 404" in attempt.error


class TestBackwardCompatibility:
    """``snapshot`` keeps its signature and its ``bytes | None`` contract."""

    def test_snapshot_still_returns_bytes_on_success(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.return_value = JPEG
        # Older SDK drop without live preview: the single-shot leg still answers.
        session.capture_main_still.side_effect = NetSdkCapabilityError("no LivePlayEx")

        with (
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False)),
            patch.object(mgr, "_get_netsdk_session", return_value=session),
        ):
            assert mgr.snapshot(channel=0) == JPEG

    def test_snapshot_still_returns_none_on_failure(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.side_effect = RuntimeError("boom")
        session.capture_main_still.side_effect = NetSdkCapabilityError("no LivePlayEx")

        with (
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False)),
            patch.object(mgr, "_get_netsdk_session", return_value=session),
        ):
            assert mgr.snapshot(channel=0) is None

    def test_legacy_private_helpers_still_return_bytes_or_none(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_jpeg.return_value = JPEG

        with patch.object(mgr, "_get_netsdk_session", return_value=session):
            assert mgr._netsdk_snapshot(channel=0) == JPEG


def _still() -> StillCapture:
    return StillCapture(image=JPEG, width=2560, height=1440, codec="hevc", stream_type=0, capture_ms=210, decode_ms=160)


class TestMainStreamIntent:
    """``stream="main"`` is a resolution contract, not a hint.

    Every stream-less single-shot leg (Web API ``GetSnapshot``, NetSDK
    ``CaptureJPEGData_V2``, the HTTP bridge) returns the IPC's configured
    snapshot stream — CIF/4CIF on the fleet, no resolution parameter exists
    (verified live 2026-08-21). A caller that asks for ``main`` must not get a
    CIF preview merely because that leg is cheapest; those legs become the
    fallback behind the frame sources that honour the request (RTSP main, the
    NetSDK live-preview keyframe).
    """

    def test_webapi_does_not_preempt_a_working_rtsp_main_leg(self):
        mgr = _manager()

        with (
            patch.object(
                DeviceManager,
                "_webapi_snapshot_attempt",
                side_effect=AssertionError("webapi must not run before the main-stream legs"),
            ),
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=True, rtsp_url=RTSP)),
            patch.object(xml_api, "rtsp_snapshot_attempt_bytes", return_value=xml_api.RtspFrameGrabResult(image=JPEG)),
        ):
            attempt = mgr.snapshot_attempt(channel=1, stream="main")

        assert attempt.method == "rtsp"
        assert attempt.image == JPEG

    def test_default_stream_is_main(self):
        """Callers that never passed ``stream`` asked for the main profile all along."""
        mgr = _manager()

        with (
            patch.object(DeviceManager, "_webapi_snapshot_attempt", side_effect=AssertionError("cheap leg ran first")),
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=True, rtsp_url=RTSP)),
            patch.object(xml_api, "rtsp_snapshot_attempt_bytes", return_value=xml_api.RtspFrameGrabResult(image=JPEG)),
        ):
            assert mgr.snapshot_attempt(channel=1).method == "rtsp"

    def test_keyframe_leg_beats_the_cif_single_shot_when_rtsp_fails(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_main_still.return_value = _still()
        session.capture_jpeg.side_effect = AssertionError("CIF single-shot must not pre-empt the keyframe")

        with (
            patch.object(DeviceManager, "_webapi_snapshot_attempt", side_effect=AssertionError("webapi ran")),
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False, error="no url")),
            patch.object(mgr, "_get_netsdk_session", return_value=session),
        ):
            attempt = mgr.snapshot_attempt(channel=2, stream="main")

        assert attempt.method == "netsdk_keyframe"
        assert attempt.image == JPEG
        assert (attempt.width, attempt.height) == (2560, 1440)
        session.capture_main_still.assert_called_once()
        assert session.capture_main_still.call_args.args == (2,)
        assert session.capture_main_still.call_args.kwargs["stream"] == 0

    def test_keyframe_unsupported_falls_back_to_webapi_then_single_shot(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_main_still.side_effect = NetSdkCapabilityError("no LivePlayEx")
        session.capture_jpeg.return_value = JPEG
        order: list[str] = []

        def _webapi(**_kwargs):
            order.append("webapi")
            return SnapshotAttempt(method="webapi", error="HTTP 404", error_kind="webapi_error")

        def _capture(channel):
            order.append("netsdk")
            return JPEG

        session.capture_jpeg.side_effect = _capture
        with (
            patch.object(DeviceManager, "_webapi_snapshot_attempt", side_effect=_webapi),
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False, error="no url")),
            patch.object(mgr, "_get_netsdk_session", return_value=session),
        ):
            attempt = mgr.snapshot_attempt(channel=0, stream="main")

        assert attempt.method == "netsdk"
        assert order == ["webapi", "netsdk"]

    def test_keyframe_decode_failure_is_reported_and_does_not_mask_nothing(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_main_still.side_effect = KeyframeDecodeError("decode_failed", "ffmpeg could not decode")
        session.capture_jpeg.side_effect = RuntimeError("Recorder returned nothing")

        with (
            patch.object(
                DeviceManager,
                "_webapi_snapshot_attempt",
                return_value=SnapshotAttempt(method="webapi", error="HTTP 404", error_kind="webapi_error"),
            ),
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False, error="no url")),
            patch.object(mgr, "_get_netsdk_session", return_value=session),
        ):
            attempt = mgr.snapshot_attempt(channel=0, stream="main")

        # The decode failure is the first specific failure in leg order.
        assert attempt.error_kind == "keyframe_error"
        assert "ffmpeg could not decode" in attempt.error

    def test_missing_ffmpeg_is_uninformative_and_yields_to_the_sdk_error(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_main_still.side_effect = KeyframeDecodeError("ffmpeg_unavailable", "ffmpeg is not installed")
        session.capture_jpeg.side_effect = RuntimeError("NET_SDK login failed (err=7)")

        with (
            patch.object(
                DeviceManager,
                "_webapi_snapshot_attempt",
                return_value=SnapshotAttempt(method="webapi", error="HTTP 404", error_kind="webapi_error"),
            ),
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False, error="no url")),
            patch.object(mgr, "_get_netsdk_session", return_value=session),
        ):
            attempt = mgr.snapshot_attempt(channel=0, stream="main")

        assert attempt.error_kind == "sdk_error"

    def test_deadline_skips_the_keyframe_leg_without_a_warm_session(self):
        """A fresh SDK login is unbounded; under a deadline only a held session may be used."""
        mgr = _manager()
        session = MagicMock()
        session.capture_main_still.return_value = _still()

        with (
            patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False, error="no url")),
            patch.object(mgr, "_get_netsdk_session", return_value=session) as get_session,
        ):
            attempt = mgr.snapshot_attempt(channel=0, stream="main", total_timeout=5)

        assert attempt.success is False
        get_session.assert_not_called()

    def test_deadline_uses_an_already_held_session_for_the_keyframe(self):
        mgr = _manager()
        session = MagicMock()
        session.capture_main_still.return_value = _still()
        mgr._netsdk_session = session

        with patch.object(mgr, "rtsp_url", return_value=RtspUrlResult(success=False, error="no url")):
            attempt = mgr.snapshot_attempt(channel=0, stream="main", total_timeout=5)

        assert attempt.method == "netsdk_keyframe"
        assert session.capture_main_still.call_args.kwargs["timeout"] <= 5

    def test_no_fallback_means_rtsp_only_for_main(self):
        mgr = _manager()

        with (
            patch.object(DeviceManager, "_webapi_snapshot_attempt", side_effect=AssertionError("webapi ran")),
            patch.object(mgr, "_get_netsdk_session", side_effect=AssertionError("SDK leg ran")),
            patch.object(mgr, "_http_rtsp_url", return_value=None),
        ):
            attempt = mgr.snapshot_attempt(channel=0, stream="main", allow_fallback=False)

        assert attempt.error_kind == "no_stream_url"

    def test_sub_keeps_the_cheapest_leg_first(self):
        mgr = _manager()

        with (
            patch.object(
                DeviceManager, "_webapi_snapshot_attempt", return_value=SnapshotAttempt(image=JPEG, method="webapi")
            ),
            patch.object(mgr, "rtsp_url", side_effect=AssertionError("rtsp ran before webapi for sub")),
        ):
            assert mgr.snapshot_attempt(channel=0, stream="sub").method == "webapi"

    def test_sdk_http_backend_main_tries_webapi_before_the_bridge(self):
        mgr = _manager(backend=Backend.SDK_HTTP)
        order: list[str] = []

        def _webapi(**_kwargs):
            order.append("webapi")
            return SnapshotAttempt(method="webapi", error="HTTP 404", error_kind="webapi_error")

        http = MagicMock()
        http.snapshot.side_effect = lambda *a, **k: order.append("http") or JPEG
        with (
            patch.object(DeviceManager, "_webapi_snapshot_attempt", side_effect=_webapi),
            patch.object(mgr, "_get_http", return_value=http),
        ):
            attempt = mgr.snapshot_attempt(channel=0, stream="main", prefer_rtsp=False)

        assert attempt.method == "http"
        assert order == ["webapi", "http"]
