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
