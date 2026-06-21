"""Tests for pytvt.platform_sdk.web_crypto (TVT management web handshake).

Pure-function tests, no network. Crypto correctness is anchored on a published
NIST FIPS-197 AES-128-ECB known-answer vector plus round-trip invariants.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from pytvt import _crypto
from pytvt.platform_sdk import web_crypto

KEY16 = "0123456789abcdef"  # exactly 16 bytes


class TestAesEcbRawKnownAnswer:
    # NIST FIPS-197 Appendix C.1 — AES-128 single-block ECB vector.
    KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    PT = bytes.fromhex("00112233445566778899aabbccddeeff")
    CT = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_encrypt_matches_nist_vector(self):
        assert _crypto.aes_ecb_raw(self.PT, self.KEY, decrypt=False) == self.CT

    def test_decrypt_matches_nist_vector(self):
        assert _crypto.aes_ecb_raw(self.CT, self.KEY, decrypt=True) == self.PT

    def test_rejects_bad_key_length(self):
        with pytest.raises(ValueError, match="16, 24, or 32"):
            _crypto.aes_ecb_raw(self.PT, b"shortkey", decrypt=False)

    def test_rejects_unaligned_data(self):
        with pytest.raises(ValueError, match="multiple of 16"):
            _crypto.aes_ecb_raw(b"abc", self.KEY, decrypt=False)


class TestPkcs7:
    def test_pad_partial_block(self):
        out = web_crypto._pkcs7_pad(b"abc")  # needs 13 bytes of padding
        assert out == b"abc" + bytes([13]) * 13
        assert len(out) == 16

    def test_pad_adds_full_block_when_aligned(self):
        out = web_crypto._pkcs7_pad(b"x" * 16)
        assert out == b"x" * 16 + bytes([16]) * 16
        assert len(out) == 32

    def test_pad_empty(self):
        assert web_crypto._pkcs7_pad(b"") == bytes([16]) * 16

    def test_pad_unpad_roundtrip(self):
        for n in range(0, 40):
            data = bytes((i % 251) for i in range(n))
            assert web_crypto._pkcs7_unpad(web_crypto._pkcs7_pad(data)) == data

    def test_unpad_rejects_unaligned(self):
        with pytest.raises(ValueError, match="block-aligned"):
            web_crypto._pkcs7_unpad(b"abc")

    def test_unpad_rejects_zero_pad_byte(self):
        with pytest.raises(ValueError, match="invalid padding"):
            web_crypto._pkcs7_unpad(b"\x00" * 16)

    def test_unpad_rejects_inconsistent_padding(self):
        # last byte says 5, but the final 5 bytes are not all 0x05
        bad = b"\x00" * 11 + bytes([1, 2, 3, 4, 5])
        with pytest.raises(ValueError, match="invalid padding"):
            web_crypto._pkcs7_unpad(bad)


class TestAesEcbPkcs7:
    def test_roundtrip_various_lengths(self):
        for text in ["", "a", "exactly-16-bytes", "spanning two blocks of data!!", "üñîçødé ✓"]:
            ct = web_crypto.aes_ecb_pkcs7_encrypt(text, KEY16)
            assert web_crypto.aes_ecb_pkcs7_decrypt(ct, KEY16) == text

    def test_encrypt_returns_base64_block_multiple(self):
        ct = web_crypto.aes_ecb_pkcs7_encrypt("hello", KEY16)  # 5 bytes -> 1 block
        assert len(base64.b64decode(ct)) == 16

    def test_accepts_bytes_plaintext_and_key(self):
        ct = web_crypto.aes_ecb_pkcs7_encrypt(b"raw-bytes", KEY16.encode("utf-8"))
        assert web_crypto.aes_ecb_pkcs7_decrypt(ct, KEY16) == "raw-bytes"

    def test_rejects_bad_key_length(self):
        with pytest.raises(ValueError, match="16 bytes"):
            web_crypto.aes_ecb_pkcs7_encrypt("x", "short")


class TestLoginHash:
    def test_md5_step_matches_known_vector(self):
        # md5("password") is a well-known constant.
        assert web_crypto._md5_hex("password") == "5f4dcc3b5aa765d61d8327deb882cf99"

    def test_login_password_hash_formula(self):
        pw, nonce = "secret", "NONCE123"
        md5_hex = hashlib.md5(pw.encode("utf-8"), usedforsecurity=False).hexdigest()
        expected = hashlib.sha256((md5_hex + nonce).encode("utf-8")).hexdigest()
        assert web_crypto.login_password_hash(pw, nonce) == expected

    def test_hash_is_64_hex_chars(self):
        out = web_crypto.login_password_hash("pw", "nonce")
        assert len(out) == 64 and all(c in "0123456789abcdef" for c in out)


class TestUsernameAndAuthId:
    def test_encrypt_username_uses_nonce_prefix(self):
        nonce = "ABCDEFGHIJKLMNOP_TRAILING"  # >= 16 chars
        enc = web_crypto.encrypt_username("operator", nonce)
        assert web_crypto.aes_ecb_pkcs7_decrypt(enc, nonce[:16]) == "operator"

    def test_decrypt_auth_id_inverts_encrypt_under_md5_key(self):
        password = "hunter2"
        key = web_crypto._md5_hex(password)[:16]
        token = web_crypto.aes_ecb_pkcs7_encrypt("auth-9f3", key)
        assert web_crypto.decrypt_auth_id(token, password) == "auth-9f3"


class TestBackendFallback:
    def test_falls_back_through_backends(self, monkeypatch):
        calls: list[str] = []

        def fail_c(data: bytes, key: bytes, *, decrypt: bool) -> bytes:
            calls.append("cryptography")
            raise RuntimeError("no cryptography")

        def fail_p(data: bytes, key: bytes, *, decrypt: bool) -> bytes:
            calls.append("pycryptodome")
            raise RuntimeError("no pycryptodome")

        def ok_o(data: bytes, key: bytes, *, decrypt: bool) -> bytes:
            calls.append("openssl")
            return b"\x00" * 16

        monkeypatch.setattr(_crypto, "_aes_ecb_raw_cryptography", fail_c)
        monkeypatch.setattr(_crypto, "_aes_ecb_raw_pycryptodome", fail_p)
        monkeypatch.setattr(_crypto, "_aes_ecb_raw_openssl", ok_o)

        out = _crypto.aes_ecb_raw(b"\x00" * 16, b"k" * 16, decrypt=False)
        assert out == b"\x00" * 16
        assert calls == ["cryptography", "pycryptodome", "openssl"]

    def test_raises_when_all_backends_fail(self, monkeypatch):
        def fail(data: bytes, key: bytes, *, decrypt: bool) -> bytes:
            raise RuntimeError("backend unavailable")

        for name in (
            "_aes_ecb_raw_cryptography",
            "_aes_ecb_raw_pycryptodome",
            "_aes_ecb_raw_openssl",
        ):
            monkeypatch.setattr(_crypto, name, fail)

        with pytest.raises(RuntimeError, match="backend unavailable"):
            _crypto.aes_ecb_raw(b"\x00" * 16, b"k" * 16, decrypt=False)
