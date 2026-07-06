"""AES + hash primitives for the TVT NVMS management-server web handshake.

Pure functions, no network. The management web-login flow (distinct from the
device/NVR flows) uses **AES-128-ECB with PKCS7 padding** and an
``md5 -> sha256`` password-hash chain; ciphertext is carried as base64 over
UTF-8. These helpers encode that vendor convention once so the web
transport/session layer (TVT-2+) can consume them without re-deriving the
crypto. Only the protocol-mandated primitives live here — key management and
transport stay in the calling layer.
"""

from __future__ import annotations

import base64
import hashlib

from pytvt._crypto import aes_ecb_raw

_BLOCK = 16


def _pkcs7_pad(data: bytes, block: int = _BLOCK) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes, block: int = _BLOCK) -> bytes:
    if not data or len(data) % block != 0:
        raise ValueError("PKCS7: data is empty or not block-aligned")
    pad = data[-1]
    if not 1 <= pad <= block or data[-pad:] != bytes([pad]) * pad:
        raise ValueError("PKCS7: invalid padding")
    return data[:-pad]


def _key16(key: str | bytes) -> bytes:
    raw = key.encode("utf-8") if isinstance(key, str) else bytes(key)
    if len(raw) != _BLOCK:
        raise ValueError(f"AES-128 key must be {_BLOCK} bytes, got {len(raw)}")
    return raw


def _md5_hex(text: str) -> str:
    # MD5 is mandated by the vendor web-login protocol; not a security choice.
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def aes_ecb_pkcs7_encrypt(plaintext: str | bytes, key: str | bytes) -> str:
    """PKCS7-pad, AES-128-ECB encrypt, and base64-encode (ascii)."""
    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else bytes(plaintext)
    ciphertext = aes_ecb_raw(_pkcs7_pad(data), _key16(key), decrypt=False)
    return base64.b64encode(ciphertext).decode("ascii")


def aes_ecb_pkcs7_decrypt(b64: str | bytes, key: str | bytes) -> str:
    """base64-decode, AES-128-ECB decrypt, PKCS7-unpad, and UTF-8 decode."""
    ciphertext = base64.b64decode(b64)
    plaintext = aes_ecb_raw(ciphertext, _key16(key), decrypt=True)
    return _pkcs7_unpad(plaintext).decode("utf-8")


def login_password_hash(password: str, nonce: str) -> str:
    """Web-login password proof: ``sha256_hex(md5_hex(password) + nonce)``."""
    return login_password_hash_from_md5(_md5_hex(password), nonce)


def login_password_hash_from_md5(password_md5_hex: str, nonce: str) -> str:
    """Web-login password proof from the pre-computed ``md5_hex(password)``.

    The session layer keeps only this digest (never the plaintext) so it can
    re-run the handshake on session expiry.
    """
    return hashlib.sha256((password_md5_hex + nonce).encode("utf-8")).hexdigest()


def encrypt_username(username: str, nonce: str) -> str:
    """Encrypt the username under the first 16 bytes of the server nonce."""
    return aes_ecb_pkcs7_encrypt(username, nonce[:_BLOCK])


def decrypt_auth_id(b64: str | bytes, password: str) -> str:
    """Decrypt the server-issued auth id under ``md5_hex(password)[:16]``."""
    return decrypt_auth_id_from_md5(b64, _md5_hex(password))


def decrypt_auth_id_from_md5(b64: str | bytes, password_md5_hex: str) -> str:
    """Decrypt the server-issued auth id from the pre-computed ``md5_hex(password)``."""
    return aes_ecb_pkcs7_decrypt(b64, password_md5_hex[:_BLOCK])
