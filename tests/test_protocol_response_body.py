"""HTTP envelope extraction preserves bytes, including imperfect device replies."""

import struct

import pytest

from pytvt.protocol import _parse_http_response_body


@pytest.mark.parametrize("size", [0, 1, 71])
def test_short_response_is_returned_intact(size):
    payload = b"x" * size
    assert _parse_http_response_body(payload) == payload


@pytest.mark.parametrize("declared_length", [0, 1, 999])
@pytest.mark.parametrize("body", [b"", b'{"channels": []}', b"\xff\x00\xfe"])
def test_envelope_is_removed_without_truncating_or_decoding_body(declared_length, body):
    envelope = struct.pack("<II", declared_length, 7) + bytes(64)
    assert _parse_http_response_body(envelope + body) == body
