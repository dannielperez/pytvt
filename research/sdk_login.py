#!/usr/bin/env python3
"""Trigger a native SDK login for traffic-capture research.

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


def main() -> int:
    _load_env_file()

    host = os.environ.get("TVT_HOST", "192.168.1.100")
    port = int(os.environ.get("TVT_PORT", "6036"))
    user = os.environ.get("TVT_USERNAME", "admin")
    password = os.environ.get("TVT_PASSWORD")
    if not password:
        print("TVT_PASSWORD not set (env or /app/.env)")
        return 1

    lib = ct.CDLL(_sdk_library_path())
    net_sdk_init = lib.NET_SDK_Init
    net_sdk_init.restype = ct.c_int
    net_sdk_init.argtypes = []

    net_sdk_cleanup = lib.NET_SDK_Cleanup
    net_sdk_cleanup.restype = ct.c_int
    net_sdk_cleanup.argtypes = []

    net_sdk_login = lib.NET_SDK_Login
    net_sdk_login.restype = ct.c_int
    net_sdk_login.argtypes = [ct.c_char_p, ct.c_int, ct.c_char_p, ct.c_char_p, ct.POINTER(ct.c_ubyte)]

    net_sdk_logout = lib.NET_SDK_Logout
    net_sdk_logout.restype = ct.c_int
    net_sdk_logout.argtypes = [ct.c_int]

    net_sdk_init()
    device_info = (ct.c_ubyte * 256)()
    print(f"Attempting login to {host}:{port} as {user}...")
    handle = net_sdk_login(
        host.encode("utf-8"),
        port,
        user.encode("utf-8"),
        password.encode("utf-8"),
        device_info,
    )
    print(f"Handle: {handle}")
    if handle > 0:
        net_sdk_logout(handle)
        print("Logged out")
    net_sdk_cleanup()
    print("Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
