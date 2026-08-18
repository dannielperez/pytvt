"""Shared JPEG framing helpers for vendor capture buffers."""

from __future__ import annotations

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


def strip_jpeg_nul_padding(image: bytes) -> bytes:
    """Drop C-buffer NUL padding that follows a complete JPEG.

    TVT NetSDK capture buffers (both ``CaptureJPEGData_V2`` and the file variant)
    can report a length that includes one or more trailing NUL bytes after the
    JPEG EOI marker. Strict consumers reject such a payload as unframed even
    though the image is complete. Only NUL bytes that immediately follow an EOI
    marker are removed; every other payload is returned unchanged so callers
    keep their existing validation behavior.
    """

    if not image.endswith(b"\x00"):
        return image
    stripped = image.rstrip(b"\x00")
    if stripped.startswith(JPEG_SOI) and stripped.endswith(JPEG_EOI):
        return stripped
    return image
