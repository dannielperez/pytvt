"""Internal crypto helpers for vendor-compatible NVR web requests."""

from __future__ import annotations

import subprocess


def _zero_pad(data: bytes, block_size: int = 16) -> bytes:
    return data + (b"\x00" * ((block_size - len(data) % block_size) % block_size))


def _aes_ecb_cryptography(data: bytes, key: str, *, decrypt: bool) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - exercised via fallback tests
        raise RuntimeError("cryptography backend unavailable") from exc

    cipher = Cipher(algorithms.AES(key.encode("utf-8")), modes.ECB())
    if decrypt:
        return cipher.decryptor().update(data) + cipher.decryptor().finalize()
    padded = _zero_pad(data)
    return cipher.encryptor().update(padded) + cipher.encryptor().finalize()


def _aes_ecb_pycryptodome(data: bytes, key: str, *, decrypt: bool) -> bytes:
    cipher_cls = None
    try:
        from Cryptodome.Cipher import AES as cipher_cls  # type: ignore[assignment]
    except ImportError:
        try:
            from Crypto.Cipher import AES as cipher_cls  # type: ignore[assignment]
        except ImportError as exc:  # pragma: no cover - exercised via fallback tests
            raise RuntimeError("pycryptodome backend unavailable") from exc

    cipher = cipher_cls.new(key.encode("utf-8"), cipher_cls.MODE_ECB)
    return cipher.decrypt(data) if decrypt else cipher.encrypt(_zero_pad(data))


def _aes_ecb_openssl(data: bytes, key: str, *, decrypt: bool) -> bytes:
    key_hex = key.encode("utf-8").hex()
    cmd = [
        "openssl",
        "enc",
        "-aes-256-ecb",
        "-nosalt",
        "-nopad",
        "-K",
        key_hex,
    ]
    payload = data if decrypt else _zero_pad(data)
    if decrypt:
        cmd.insert(3, "-d")
    proc = subprocess.run(cmd, input=payload, capture_output=True, check=False)
    if proc.returncode != 0:  # pragma: no cover - hard to force portably
        stderr = proc.stderr.decode("utf-8", "ignore").strip()
        raise RuntimeError(stderr or "OpenSSL AES helper failed")
    return proc.stdout


def aes_ecb_zeropad(data: bytes, key: str, *, decrypt: bool) -> bytes:
    """Encrypt/decrypt using the NVR web UI's AES-ECB zero-padding convention."""
    backends = (
        _aes_ecb_cryptography,
        _aes_ecb_pycryptodome,
        _aes_ecb_openssl,
    )
    last_error: Exception | None = None
    for backend in backends:
        try:
            return backend(data, key, decrypt=decrypt)
        except RuntimeError as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "No AES backend available"))


# ---------------------------------------------------------------------------
# Generic AES-ECB block primitive (no padding; raw byte key).
#
# Distinct from aes_ecb_zeropad above (AES-256 + zero padding, the NVR web-UI
# convention): this operates on already-padded, block-aligned data with a raw
# byte key and selects the AES variant by key length (16/24/32 -> 128/192/256).
# It is the primitive behind the management-server web handshake
# (platform_sdk.web_crypto, which layers PKCS7 on top).
# ---------------------------------------------------------------------------


def _aes_ecb_raw_cryptography(data: bytes, key: bytes, *, decrypt: bool) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - exercised via fallback tests
        raise RuntimeError("cryptography backend unavailable") from exc

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    ctx = cipher.decryptor() if decrypt else cipher.encryptor()
    return ctx.update(data) + ctx.finalize()


def _aes_ecb_raw_pycryptodome(data: bytes, key: bytes, *, decrypt: bool) -> bytes:
    cipher_cls = None
    try:
        from Cryptodome.Cipher import AES as cipher_cls  # type: ignore[assignment]
    except ImportError:
        try:
            from Crypto.Cipher import AES as cipher_cls  # type: ignore[assignment]
        except ImportError as exc:  # pragma: no cover - exercised via fallback tests
            raise RuntimeError("pycryptodome backend unavailable") from exc

    cipher = cipher_cls.new(key, cipher_cls.MODE_ECB)
    return cipher.decrypt(data) if decrypt else cipher.encrypt(data)


def _aes_ecb_raw_openssl(data: bytes, key: bytes, *, decrypt: bool) -> bytes:
    bits = len(key) * 8
    cmd = [
        "openssl",
        "enc",
        f"-aes-{bits}-ecb",
        "-nosalt",
        "-nopad",
        "-K",
        key.hex(),
    ]
    if decrypt:
        cmd.insert(3, "-d")
    proc = subprocess.run(cmd, input=data, capture_output=True, check=False)
    if proc.returncode != 0:  # pragma: no cover - hard to force portably
        stderr = proc.stderr.decode("utf-8", "ignore").strip()
        raise RuntimeError(stderr or "OpenSSL AES helper failed")
    return proc.stdout


def aes_ecb_raw(data: bytes, key: bytes, *, decrypt: bool) -> bytes:
    """AES-ECB on block-aligned ``data`` with a raw byte ``key`` (no padding).

    The AES variant is chosen by key length (16/24/32 bytes -> 128/192/256).
    ``data`` must be a multiple of 16 bytes; padding is the caller's job. Tries
    the cryptography and pycryptodome Python backends, then the openssl CLI.
    """
    if len(key) not in (16, 24, 32):
        raise ValueError(f"AES key must be 16, 24, or 32 bytes, got {len(key)}")
    if len(data) % 16 != 0:
        raise ValueError(f"data must be a multiple of 16 bytes, got {len(data)}")
    backends = (
        _aes_ecb_raw_cryptography,
        _aes_ecb_raw_pycryptodome,
        _aes_ecb_raw_openssl,
    )
    last_error: Exception | None = None
    for backend in backends:
        try:
            return backend(data, key, decrypt=decrypt)
        except RuntimeError as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "No AES backend available"))
