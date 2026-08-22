"""Main-stream keyframe still: ``DeviceSession.capture_keyframe`` and the decode helper.

Why this exists: every stream-less TVT snapshot API returns the IPC's CIF
snapshot stream. The live-preview keyframe is the only full-resolution still,
and its contract (first video callback is the I-frame, codec from the format
frame, preview stopped as soon as the frame is in hand) must be pinned without
hardware. The SDK library is a MagicMock whose ``NET_SDK_LivePlayEx`` invokes
the registered callback the way the recorder does.
"""

from __future__ import annotations

import ctypes as ct
import struct
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pytvt import keyframe as kf
from pytvt.device_sdk import bindings as sdk
from pytvt.device_sdk.client import (
    DeviceSession,
    NetSdkCapabilityError,
    NetSdkClient,
    NetSdkError,
)
from pytvt.device_sdk.constants import FrameType, StreamType
from pytvt.device_sdk.types import LIVE_DATA_CALLBACK_EX, NET_SDK_CLIENTINFO, NET_SDK_FRAME_INFO

HEVC_KEYFRAME = b"\x00\x00\x00\x01\x40\x01\x0c\x01\xff\xff" + b"\x00\x00\x00\x01\x42\x01" + b"\x11" * 64
H264_KEYFRAME = b"\x00\x00\x00\x01\x67\x64\x00\x1f" + b"\x00\x00\x00\x01\x65\x88" + b"\x22" * 64
FORMAT_HEVC = struct.pack("<IIII", 40, 0x0A00, 1440, 0x100001) + b"HEVC" + b"\x00" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"


def _frame(
    data_type: int,
    payload: bytes,
    *,
    key: int = 0,
    width: int = 2560,
    height: int = 1440,
    time_us: int = 1_700_000_000_000_000,
) -> bytes:
    header = struct.pack("<10I2q", 1, 0, data_type, len(payload), key, width, height, 0, 0x40, 0, time_us, 0)
    assert len(header) == ct.sizeof(NET_SDK_FRAME_INFO)
    return header + payload


@pytest.fixture
def mock_lib():
    lib = MagicMock()
    lib.NET_SDK_Init.return_value = True
    lib.NET_SDK_Cleanup.return_value = True
    lib.NET_SDK_SetConnectTime.return_value = True
    lib.NET_SDK_SetReconnect.return_value = True
    lib.NET_SDK_GetLastError.return_value = 0
    lib.NET_SDK_Logout.return_value = True
    lib.NET_SDK_StopLivePlay.return_value = True
    return lib


@pytest.fixture
def session(mock_lib):
    with patch("pytvt.device_sdk.client.load_sdk", return_value=mock_lib):
        client = NetSdkClient()
    sdk.bind(mock_lib)
    return DeviceSession(handle=1, client=client)


def _deliver(frames: list[bytes], handle: int = 7):
    """Make ``LivePlayEx`` push ``frames`` through the registered callback synchronously."""
    buffers = [ct.create_string_buffer(frame, len(frame)) for frame in frames]

    def _live_play(user_id, info, callback, user):
        for frame, buf in zip(frames, buffers, strict=True):
            data_type = struct.unpack_from("<I", frame, 8)[0]
            callback(handle, data_type, ct.addressof(buf), len(frame), None)
        return handle

    return _live_play


class TestStructLayout:
    def test_frame_info_matches_the_sdk_header(self):
        # 10 x UINT + 2 x LONGLONG under #pragma pack(4)
        assert ct.sizeof(NET_SDK_FRAME_INFO) == 56

    def test_clientinfo_matches_the_sdk_header(self):
        # LONG lChannel + LONG streamType + HWND + int under #pragma pack(4)
        assert ct.sizeof(NET_SDK_CLIENTINFO) == 28


class TestCaptureKeyframe:
    def test_returns_the_first_keyframe_with_codec_and_dimensions(self, session, mock_lib):
        mock_lib.NET_SDK_LivePlayEx.side_effect = _deliver(
            [
                _frame(FrameType.VIDEO_FORMAT, FORMAT_HEVC),
                _frame(FrameType.AUDIO_FORMAT, b"\x06\x00\x01\x00" + b"\x00" * 14),
                _frame(FrameType.VIDEO, HEVC_KEYFRAME, key=1),
                _frame(FrameType.VIDEO, b"\x00\x00\x01\x02\x01" + b"\x33" * 8, key=0),
            ]
        )

        capture = session.capture_keyframe(3, stream=StreamType.MAIN, timeout=1.0)

        assert capture.data == HEVC_KEYFRAME
        assert capture.codec == "hevc"
        assert (capture.width, capture.height) == (2560, 1440)
        assert capture.stream_type == 0
        assert capture.frame_time_us == 1_700_000_000_000_000
        # Preview was requested for the right channel/stream with no render window.
        _uid, info_ptr, callback, _user = mock_lib.NET_SDK_LivePlayEx.call_args.args
        info = info_ptr._obj
        assert (info.lChannel, info.streamType, info.hPlayWnd, info.bNoDecode) == (3, 0, None, 1)
        assert isinstance(callback, LIVE_DATA_CALLBACK_EX)
        mock_lib.NET_SDK_StopLivePlay.assert_called_once_with(7)

    def test_non_key_video_frames_are_skipped_until_the_keyframe(self, session, mock_lib):
        mock_lib.NET_SDK_LivePlayEx.side_effect = _deliver(
            [
                _frame(FrameType.VIDEO, b"\x00\x00\x01\x02\x01" + b"\x33" * 8, key=0),
                _frame(FrameType.VIDEO, HEVC_KEYFRAME, key=1),
            ]
        )

        capture = session.capture_keyframe(0, timeout=1.0)

        assert capture.data == HEVC_KEYFRAME
        # No format frame seen: codec is left for sniffing at decode time.
        assert capture.codec == ""

    def test_sub_stream_request_is_passed_through(self, session, mock_lib):
        mock_lib.NET_SDK_LivePlayEx.side_effect = _deliver(
            [_frame(FrameType.VIDEO, HEVC_KEYFRAME, key=1, width=352, height=240)]
        )

        capture = session.capture_keyframe(1, stream=StreamType.SUB, timeout=1.0)

        assert capture.stream_type == 1
        assert (capture.width, capture.height) == (352, 240)
        assert mock_lib.NET_SDK_LivePlayEx.call_args.args[1]._obj.streamType == 1

    def test_timeout_stops_the_preview_and_raises(self, session, mock_lib):
        mock_lib.NET_SDK_LivePlayEx.side_effect = _deliver([_frame(FrameType.VIDEO_FORMAT, FORMAT_HEVC)], handle=9)

        with pytest.raises(NetSdkError, match=r"no keyframe within 0\.05s"):
            session.capture_keyframe(0, timeout=0.05)

        mock_lib.NET_SDK_StopLivePlay.assert_called_once_with(9)

    def test_live_play_failure_raises_the_sdk_error(self, session, mock_lib):
        mock_lib.NET_SDK_LivePlayEx.return_value = -1
        mock_lib.NET_SDK_GetLastError.return_value = 7

        with pytest.raises(NetSdkError, match="LivePlayEx"):
            session.capture_keyframe(0, timeout=0.1)

        mock_lib.NET_SDK_StopLivePlay.assert_not_called()

    def test_oversized_keyframe_is_rejected(self, session, mock_lib):
        mock_lib.NET_SDK_LivePlayEx.side_effect = _deliver([_frame(FrameType.VIDEO, HEVC_KEYFRAME, key=1)])

        with pytest.raises(NetSdkError, match="exceeds the 16-byte limit"):
            session.capture_keyframe(0, timeout=1.0, max_bytes=16)

        mock_lib.NET_SDK_StopLivePlay.assert_called_once()

    def test_missing_preview_symbols_raise_a_capability_error(self, session, mock_lib):
        del mock_lib.NET_SDK_LivePlayEx

        with pytest.raises(NetSdkCapabilityError, match="NET_SDK_LivePlayEx"):
            session.capture_keyframe(0)


class TestCaptureMainStill:
    def test_composes_keyframe_and_decode(self, session, mock_lib):
        mock_lib.NET_SDK_LivePlayEx.side_effect = _deliver(
            [_frame(FrameType.VIDEO_FORMAT, FORMAT_HEVC), _frame(FrameType.VIDEO, HEVC_KEYFRAME, key=1)]
        )

        with patch("pytvt.device_sdk.client.decode_keyframe_to_jpeg", return_value=JPEG) as decode:
            still = session.capture_main_still(2, timeout=1.0, quality=3, decode_timeout=4.0)

        assert still.image == JPEG
        assert (still.width, still.height, still.codec, still.stream_type) == (2560, 1440, "hevc", 0)
        assert still.capture_ms >= 0 and still.decode_ms >= 0
        decode.assert_called_once_with(HEVC_KEYFRAME, "hevc", quality=3, timeout=4.0)

    def test_decode_errors_propagate(self, session, mock_lib):
        mock_lib.NET_SDK_LivePlayEx.side_effect = _deliver([_frame(FrameType.VIDEO, HEVC_KEYFRAME, key=1)])

        with (
            patch(
                "pytvt.device_sdk.client.decode_keyframe_to_jpeg",
                side_effect=kf.KeyframeDecodeError("decode_failed", "boom"),
            ),
            pytest.raises(kf.KeyframeDecodeError) as excinfo,
        ):
            session.capture_main_still(0, timeout=1.0)

        assert excinfo.value.kind == "decode_failed"


class TestCodecDetection:
    def test_format_frame_fourcc(self):
        assert kf.codec_from_format_frame(FORMAT_HEVC) == "hevc"
        assert kf.codec_from_format_frame(FORMAT_HEVC[:16] + b"H264" + b"\x00" * 20) == "h264"
        assert kf.codec_from_format_frame(FORMAT_HEVC[:16] + b"MJPG" + b"\x00" * 20) == ""
        assert kf.codec_from_format_frame(b"\x00" * 10) == ""

    def test_sniff_hevc_and_h264_annex_b(self):
        assert kf.sniff_codec(HEVC_KEYFRAME) == "hevc"
        assert kf.sniff_codec(H264_KEYFRAME) == "h264"
        assert kf.sniff_codec(b"\x00\x00\x00\x01\xff\xff") == ""
        assert kf.sniff_codec(b"no start code") == ""


class TestDecodeKeyframeToJpeg:
    def test_runs_ffmpeg_on_stdin_and_returns_stdout(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=JPEG, stderr=b"")
        with (
            patch("pytvt.keyframe.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("pytvt.keyframe.subprocess.run", return_value=completed) as run,
        ):
            assert kf.decode_keyframe_to_jpeg(HEVC_KEYFRAME, "hevc", quality=2, timeout=3.0) == JPEG

        args = run.call_args.args[0]
        assert args[0] == "ffmpeg"
        assert args[args.index("-f") + 1] == "hevc"
        assert args[args.index("-i") + 1] == "pipe:0"
        assert args[-1] == "pipe:1"
        assert "-frames:v" in args and args[args.index("-q:v") + 1] == "2"
        assert run.call_args.kwargs["input"] == HEVC_KEYFRAME
        assert run.call_args.kwargs["timeout"] == 3.0

    def test_unknown_codec_is_sniffed_then_rejected(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=JPEG, stderr=b"")
        with (
            patch("pytvt.keyframe.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("pytvt.keyframe.subprocess.run", return_value=completed) as run,
        ):
            kf.decode_keyframe_to_jpeg(H264_KEYFRAME, "")
        assert run.call_args.args[0][run.call_args.args[0].index("-f") + 1] == "h264"

        with pytest.raises(kf.KeyframeDecodeError) as excinfo:
            kf.decode_keyframe_to_jpeg(b"\x00\x00\x00\x01\xff\xff", "")
        assert excinfo.value.kind == "unknown_codec"

    def test_failure_kinds(self):
        with patch("pytvt.keyframe.shutil.which", return_value=None), pytest.raises(kf.KeyframeDecodeError) as excinfo:
            kf.decode_keyframe_to_jpeg(HEVC_KEYFRAME, "hevc")
        assert excinfo.value.kind == "ffmpeg_unavailable"

        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"Error: bad stream\n")
        with (
            patch("pytvt.keyframe.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("pytvt.keyframe.subprocess.run", return_value=failed),
            pytest.raises(kf.KeyframeDecodeError) as excinfo,
        ):
            kf.decode_keyframe_to_jpeg(HEVC_KEYFRAME, "hevc")
        assert excinfo.value.kind == "decode_failed"
        assert "bad stream" in str(excinfo.value)

        with (
            patch("pytvt.keyframe.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("pytvt.keyframe.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)),
            pytest.raises(kf.KeyframeDecodeError) as excinfo,
        ):
            kf.decode_keyframe_to_jpeg(HEVC_KEYFRAME, "hevc", timeout=1)
        assert excinfo.value.kind == "decode_timeout"

    def test_empty_payload(self):
        with (
            patch("pytvt.keyframe.shutil.which", return_value="/usr/bin/ffmpeg"),
            pytest.raises(kf.KeyframeDecodeError) as excinfo,
        ):
            kf.decode_keyframe_to_jpeg(b"", "hevc")
        assert excinfo.value.kind == "empty_frame"


class TestCallbackLifetime:
    def test_thunk_is_retained_after_the_preview_is_stopped(self, session, mock_lib):
        """A late callback after StopLivePlay must hit a live trampoline, not freed memory."""
        mock_lib.NET_SDK_LivePlayEx.side_effect = _deliver([_frame(FrameType.VIDEO, HEVC_KEYFRAME, key=1)])

        session.capture_keyframe(0, timeout=1.0)

        thunk = mock_lib.NET_SDK_LivePlayEx.call_args.args[2]
        assert thunk in session._live_thunks
        # Retention is bounded: the deque caps how many trampolines stay alive.
        for _ in range(12):
            session.capture_keyframe(0, timeout=1.0)
        assert len(session._live_thunks) <= 8
