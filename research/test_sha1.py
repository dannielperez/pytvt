#!/usr/bin/env python3
"""Call PUB_SHA1Encrypt from the TVT SDK for comparison research.

RESEARCH / REFERENCE ONLY — not part of the pytvt runtime.
"""

from __future__ import annotations

import ctypes as ct
import os
from pathlib import Path


def _load_env_file(path: str = "/app/.env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _sdk_library_path() -> str:
    return os.environ.get("TVT_SDK_PATH") or "/app/tvt/bin/linux/libdvrnetsdk.so"


def _call_sha1(func, payload: bytes) -> tuple[int, bytes]:
    output = ct.create_string_buffer(64)
    ret = func(payload, len(payload), output)
    return ret, output.raw


def main() -> int:
    _load_env_file()
    password = os.environ.get("TVT_PASSWORD")
    if not password:
        print("TVT_PASSWORD not found in env or /app/.env")
        return 1

    lib = ct.CDLL(_sdk_library_path())
    sha1_encrypt = lib.PUB_SHA1Encrypt
    sha1_encrypt.restype = ct.c_int
    sha1_encrypt.argtypes = [ct.c_char_p, ct.c_int, ct.c_char_p]

    ret, output = _call_sha1(sha1_encrypt, password.encode("utf-8"))
    print(f"PUB_SHA1Encrypt return: {ret}")
    print(f"Output (first 20 bytes): {output[:20].hex()}")
    print(f"Output (first 32 bytes): {output[:32].hex()}")

    padded = password.encode("utf-8")[:32].ljust(32, b"\x00")
    ret, output = _call_sha1(sha1_encrypt, padded)
    print(f"\nPUB_SHA1Encrypt(pw32) return: {ret}")
    print(f"Output (first 20 bytes): {output[:20].hex()}")

    nonce = os.environ.get("TVT_NONCE")
    if nonce:
        ret, output = _call_sha1(sha1_encrypt, bytes.fromhex(nonce) + password.encode("utf-8"))
        print(f"\nPUB_SHA1Encrypt(nonce+pw) return: {ret}")
        print(f"Output: {output[:20].hex()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
