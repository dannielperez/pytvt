"""Main-stream keyframe → JPEG helpers shared by the NetSDK live-preview grab.

The stream-less TVT snapshot APIs (NetSDK ``CaptureJPEGData_V2``, Web API
``GetSnapshot``, ONVIF ``GetSnapshotUri``) all return the IPC's configured
*snapshot stream* (CIF/4CIF on the fleet). The only full-resolution still is a
decoded main-stream keyframe. ``DeviceSession.capture_keyframe`` obtains that
keyframe through the recorder's NetSDK preview (no RTSP, no GOP wait); this
module turns the raw Annex-B elementary stream into a JPEG with ``ffmpeg``,
which is already a pytvt dependency for the RTSP frame grab.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

# FourCC carried at offset 16 of the DD_FRAME_TYPE_VIDEO_FORMAT header frame.
_FOURCC_CODECS = {
    b"HEVC": "hevc",
    b"H265": "hevc",
    b"H264": "h264",
    b"AVC1": "h264",
}
_FOURCC_OFFSET = 16
_DEFAULT_JPEG_QUALITY = 2  # ffmpeg -q:v scale (2 = visually lossless, matches the RTSP leg)
_DEFAULT_DECODE_TIMEOUT_SECONDS = 10.0


class KeyframeDecodeError(RuntimeError):
    """A captured keyframe could not be turned into a JPEG.

    ``kind`` is a stable machine token: ``ffmpeg_unavailable``,
    ``unknown_codec``, ``decode_failed``, ``decode_timeout``, ``empty_frame``.
    """

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class KeyframeCapture:
    """One raw keyframe from a live stream, as the recorder delivered it."""

    data: bytes  # Annex-B elementary stream: parameter sets + one I/IDR frame
    codec: str  # "hevc" | "h264" | "" when the format frame was not seen
    width: int
    height: int
    stream_type: int  # NET_SDK_STREAM_TYPE (0 main, 1 sub, ...)
    capture_ms: int  # LivePlayEx call → keyframe in hand
    frame_time_us: int = 0  # recorder's absolute frame time, microseconds since epoch


@dataclass(frozen=True, slots=True)
class StillCapture:
    """A decoded full-resolution still plus the timing that produced it."""

    image: bytes  # JPEG
    width: int
    height: int
    codec: str
    stream_type: int
    capture_ms: int
    decode_ms: int


def codec_from_format_frame(payload: bytes) -> str:
    """Return the ffmpeg demuxer name from a VIDEO_FORMAT frame payload, or ``""``."""
    if len(payload) < _FOURCC_OFFSET + 4:
        return ""
    return _FOURCC_CODECS.get(payload[_FOURCC_OFFSET : _FOURCC_OFFSET + 4].upper(), "")


def sniff_codec(elementary_stream: bytes) -> str:
    """Best-effort codec detection from the first Annex-B NAL unit header.

    Used only when the recorder did not send a format frame first. H.265 NAL
    headers are two bytes (type in bits 1-6 of byte 0, forbidden bit and
    layer-id high bit clear); H.264 headers are one byte (type in bits 0-4).
    """
    idx = elementary_stream.find(b"\x00\x00\x01")
    if idx < 0 or idx + 4 >= len(elementary_stream):
        return ""
    first = elementary_stream[idx + 3]
    hevc_type = (first >> 1) & 0x3F
    if first & 0x81 == 0 and hevc_type in {32, 33, 34, 19, 20, 21}:
        return "hevc"
    if first & 0x80 == 0 and first & 0x1F in {7, 8, 5}:
        return "h264"
    return ""


def decode_keyframe_to_jpeg(
    data: bytes,
    codec: str,
    *,
    quality: int = _DEFAULT_JPEG_QUALITY,
    timeout: float = _DEFAULT_DECODE_TIMEOUT_SECONDS,
    ffmpeg: str = "ffmpeg",
) -> bytes:
    """Decode one Annex-B keyframe to JPEG bytes with ffmpeg (stdin → stdout).

    Raises:
        KeyframeDecodeError: with a stable ``kind`` for every failure mode.
    """
    codec = codec or sniff_codec(data)
    if codec not in {"hevc", "h264"}:
        raise KeyframeDecodeError("unknown_codec", "Keyframe codec could not be determined.")
    if not data:
        raise KeyframeDecodeError("empty_frame", "Keyframe payload is empty.")
    if shutil.which(ffmpeg) is None:
        raise KeyframeDecodeError("ffmpeg_unavailable", "ffmpeg is not installed; keyframe stills cannot be decoded.")
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        codec,
        "-i",
        "pipe:0",
        "-an",
        "-frames:v",
        "1",
        "-q:v",
        str(quality),
        "-f",
        "image2",
        "pipe:1",
    ]
    try:
        result = subprocess.run(args, input=data, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise KeyframeDecodeError("decode_timeout", "ffmpeg keyframe decode timed out.") from exc
    except FileNotFoundError as exc:
        raise KeyframeDecodeError(
            "ffmpeg_unavailable", "ffmpeg is not installed; keyframe stills cannot be decoded."
        ) from exc
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        reason = detail[-1][:200] if detail else f"ffmpeg exit {result.returncode}"
        raise KeyframeDecodeError("decode_failed", f"ffmpeg could not decode the keyframe: {reason}")
    return result.stdout
