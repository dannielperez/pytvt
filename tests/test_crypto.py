"""Tests for pytvt._crypto."""

from __future__ import annotations

from pytvt import _crypto


class TestAesEcbZeroPad:
    def test_tries_python_backends_before_openssl(self, monkeypatch):
        calls: list[str] = []

        def fail_cryptography(data: bytes, key: str, *, decrypt: bool) -> bytes:
            calls.append("cryptography")
            raise RuntimeError("no cryptography")

        def fail_pycryptodome(data: bytes, key: str, *, decrypt: bool) -> bytes:
            calls.append("pycryptodome")
            raise RuntimeError("no pycryptodome")

        def ok_openssl(data: bytes, key: str, *, decrypt: bool) -> bytes:
            calls.append("openssl")
            return b"done"

        monkeypatch.setattr(_crypto, "_aes_ecb_cryptography", fail_cryptography)
        monkeypatch.setattr(_crypto, "_aes_ecb_pycryptodome", fail_pycryptodome)
        monkeypatch.setattr(_crypto, "_aes_ecb_openssl", ok_openssl)

        out = _crypto.aes_ecb_zeropad(b"abc", "k" * 32, decrypt=False)

        assert out == b"done"
        assert calls == ["cryptography", "pycryptodome", "openssl"]

    def test_raises_when_all_backends_fail(self, monkeypatch):
        def fail(data: bytes, key: str, *, decrypt: bool) -> bytes:
            raise RuntimeError("backend unavailable")

        monkeypatch.setattr(_crypto, "_aes_ecb_cryptography", fail)
        monkeypatch.setattr(_crypto, "_aes_ecb_pycryptodome", fail)
        monkeypatch.setattr(_crypto, "_aes_ecb_openssl", fail)

        try:
            _crypto.aes_ecb_zeropad(b"abc", "k" * 32, decrypt=False)
        except RuntimeError as exc:
            assert "backend unavailable" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError")
