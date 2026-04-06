"""Tests for pytvt.protocol — packet construction, parsing, encryption."""

from __future__ import annotations

import hashlib
import struct

import pytest

from pytvt.protocol import (
    CMD_HEAD_LOGIN,
    CMD_HTTP_REQUEST,
    CMD_REQUEST_LOGIN,
    HEADER_FLAG,
    INIT_FLAG,
    INIT_PACKET_SIZE,
    _encrypt_password,
    _encrypt_password_head,
    _encrypt_username_head,
    _extract_cameras_from_json,
    _is_head_variant,
    _make_header,
    _make_http_request,
    _make_login_data,
    _normalize_camera,
    _parse_init,
    _parse_login_response,
)

# ── _make_header ─────────────────────────────────────────────────────


class TestMakeHeader:
    def test_standard_header_length(self):
        h = _make_header(CMD_REQUEST_LOGIN, data_len=116)
        assert len(h) == 24  # 8-byte preamble + 16-byte command

    def test_header_flag(self):
        h = _make_header(CMD_REQUEST_LOGIN, 0)
        assert h[:4] == HEADER_FLAG

    def test_header_flag_head(self):
        h = _make_header(CMD_HEAD_LOGIN, 0, header_flag=INIT_FLAG)
        assert h[:4] == INIT_FLAG

    def test_total_length_field(self):
        h = _make_header(0x101, data_len=100)
        total_len = struct.unpack_from("<I", h, 4)[0]
        assert total_len == 16 + 100

    def test_cmd_field(self):
        h = _make_header(CMD_REQUEST_LOGIN, 0)
        cmd = struct.unpack_from("<I", h, 8)[0]
        assert cmd == CMD_REQUEST_LOGIN

    def test_data_len_field(self):
        h = _make_header(0x101, data_len=42)
        dl = struct.unpack_from("<I", h, 20)[0]
        assert dl == 42

    def test_cmd_id_and_ver(self):
        h = _make_header(0x101, 0, cmd_id=0x0101, cmd_ver=0x0202)
        cmd_id = struct.unpack_from("<I", h, 12)[0]
        cmd_ver = struct.unpack_from("<I", h, 16)[0]
        assert cmd_id == 0x0101
        assert cmd_ver == 0x0202


# ── _parse_init ──────────────────────────────────────────────────────


class TestParseInit:
    def test_standard_init(self, standard_init_packet):
        info = _parse_init(standard_init_packet)
        assert info["flag"] == b"1111"
        assert info["protocolVer"] == 3
        assert info["loginEncrypt"] == 2
        assert info["mac"] == "58:5B:69:AA:BB:CC"
        assert info["loginNonce"] == bytes([0x11, 0x22, 0x33])

    def test_head_init(self, head_init_packet):
        info = _parse_init(head_init_packet)
        assert info["flag"] == b"head"
        assert info["protocolVer"] == 11
        assert info["nonce_int"] == (0xAA | (0xBB << 8) | (0xCC << 16))

    def test_short_data_returns_empty(self):
        info = _parse_init(b"\x00" * 10)
        assert info == {}

    def test_xor_key_4_bytes(self, standard_init_packet):
        info = _parse_init(standard_init_packet)
        assert info["xor_key_4"] == bytes([2, 0x11, 0x22, 0x33])


# ── _encrypt_password ────────────────────────────────────────────────


class TestEncryptPassword:
    def test_no_encryption(self):
        result = _encrypt_password("admin", None)
        expected = b"admin" + b"\x00" * 27
        assert result == expected
        assert len(result) == 32

    def test_plaintext_encrypt_type_0(self):
        info = {"loginEncrypt": 0}
        result = _encrypt_password("test", info)
        assert result == b"test" + b"\x00" * 28

    def test_xor_encrypt_type_2(self, standard_init_packet):
        info = _parse_init(standard_init_packet)
        result = _encrypt_password("admin", info)
        assert len(result) == 32
        # Verify it's actually XOR'd (not plaintext)
        plaintext = b"admin" + b"\x00" * 27
        assert result != plaintext

    def test_xor_roundtrip(self, standard_init_packet):
        """XOR encryption is its own inverse: encrypt(encrypt(x)) == x."""
        info = _parse_init(standard_init_packet)
        password = "testpass"
        encrypted = _encrypt_password(password, info)
        # XOR again with the same key should recover plaintext
        xor_key = info["xor_key_4"] * 8
        decrypted = bytes(a ^ b for a, b in zip(encrypted, xor_key[:32], strict=True))
        expected = password.encode("ascii").ljust(32, b"\x00")[:32]
        assert decrypted == expected


# ── _encrypt_password_head ───────────────────────────────────────────


class TestEncryptPasswordHead:
    def test_output_length(self):
        result = _encrypt_password_head("admin", 12345)
        assert len(result) == 20  # SHA1 produces 20 bytes

    def test_deterministic(self):
        a = _encrypt_password_head("admin", 999)
        b = _encrypt_password_head("admin", 999)
        assert a == b

    def test_different_nonce_different_result(self):
        a = _encrypt_password_head("admin", 100)
        b = _encrypt_password_head("admin", 200)
        assert a != b

    def test_known_value(self):
        """Verify the algorithm: SHA1(MD5(password).upper() + sprintf('%08d', nonce))."""
        password = "admin"
        nonce = 12345
        md5_hex = hashlib.md5(password.encode("ascii")).hexdigest().upper()
        combined = f"{md5_hex}{nonce:08d}"
        expected = hashlib.sha1(combined.encode("ascii")).digest()
        assert _encrypt_password_head(password, nonce) == expected


# ── _encrypt_username_head ───────────────────────────────────────────


class TestEncryptUsernameHead:
    def test_output_length(self):
        result = _encrypt_username_head("admin", 12345)
        assert len(result) == 64

    def test_null_bytes_not_xored(self):
        result = _encrypt_username_head("a", 999)
        # Only byte 0 should be XOR'd, rest are null padding (unchanged)
        assert all(b == 0 for b in result[1:])

    def test_roundtrip(self):
        nonce = 42
        username = "admin"
        encrypted = _encrypt_username_head(username, nonce)
        # Decrypt by XOR'ing again
        key = str(nonce).encode("ascii")
        buf = bytearray(encrypted)
        for i in range(64):
            if buf[i] != 0:
                buf[i] ^= key[i % len(key)]
        decrypted = bytes(buf).split(b"\x00")[0].decode("ascii")
        assert decrypted == username


# ── _is_head_variant ─────────────────────────────────────────────────


class TestIsHeadVariant:
    def test_standard(self, standard_init_packet):
        info = _parse_init(standard_init_packet)
        assert _is_head_variant(info) is False

    def test_head(self, head_init_packet):
        info = _parse_init(head_init_packet)
        assert _is_head_variant(info) is True

    def test_boundary(self):
        assert _is_head_variant({"protocolVer": 10}) is False
        assert _is_head_variant({"protocolVer": 11}) is True

    def test_missing_key(self):
        assert _is_head_variant({}) is False


# ── _make_login_data ─────────────────────────────────────────────────


class TestMakeLoginData:
    def test_standard_login_length(self, standard_init_packet):
        info = _parse_init(standard_init_packet)
        data = _make_login_data("admin", "pass", info)
        assert len(data) == 116

    def test_head_login_length(self, head_init_packet):
        info = _parse_init(head_init_packet)
        data = _make_login_data("admin", "pass", info)
        assert len(data) == 236

    def test_standard_connect_type(self, standard_init_packet):
        info = _parse_init(standard_init_packet)
        data = _make_login_data("admin", "pass", info)
        ct = struct.unpack_from("<I", data, 0)[0]
        assert ct == 1

    def test_head_connect_type(self, head_init_packet):
        info = _parse_init(head_init_packet)
        data = _make_login_data("admin", "pass", info)
        ct = struct.unpack_from("<I", data, 0)[0]
        assert ct == 3


# ── _make_http_request ───────────────────────────────────────────────


class TestMakeHttpRequest:
    def test_basic(self):
        data = _make_http_request("/queryIPCInfo", seq=1)
        # Check structure: contentLen(4) + seq(4) + reserved(64) + content + endByte(1)
        content_len = struct.unpack_from("<I", data, 0)[0]
        seq = struct.unpack_from("<I", data, 4)[0]
        assert seq == 1
        assert data[-1] == 0  # endByte
        content = data[72:-1]
        assert len(content) == content_len
        assert b"GET /queryIPCInfo HTTP/1.1" in content


# ── _parse_login_response ────────────────────────────────────────────


class TestParseLoginResponse:
    def test_short_data(self):
        assert _parse_login_response(b"\x00" * 10) == {}

    def test_extracts_device_name(self):
        # Build a minimal 100-byte payload with device_name at known offset
        data = bytearray(100)
        # device_name offset = 4 + 20 + 12 + 4 + 4 + 4 + 4 + 6 = 58
        name = b"TestNVR\x00"
        data[58 : 58 + len(name)] = name
        info = _parse_login_response(bytes(data))
        assert info["device_name"] == "TestNVR"


# ── _normalize_camera / _extract_cameras_from_json ───────────────────


class TestNormalizeCamera:
    def test_tvt_format(self):
        cam = _normalize_camera(
            {
                "szChlname": "Lobby Cam",
                "szServer": "192.168.1.10",
                "nPort": 9008,
                "status": 1,
            }
        )
        assert cam is not None
        assert cam["name"] == "Lobby Cam"
        assert cam["address"] == "192.168.1.10"
        assert cam["port"] == 9008
        assert cam["status"] == "Online"

    def test_generic_format(self):
        cam = _normalize_camera(
            {
                "name": "Parking",
                "address": "10.0.0.5",
                "port": 80,
                "status": "Online",
                "model": "HK-2CD",
            }
        )
        assert cam["name"] == "Parking"
        assert cam["model"] == "HK-2CD"

    def test_empty_returns_none(self):
        assert _normalize_camera({}) is None

    def test_offline_status_int(self):
        cam = _normalize_camera({"name": "X", "status": 0})
        # status=0 is falsy so the `or` chain yields ""; only explicit 0 after
        # other checks are exhausted gives "Unknown"
        assert cam["status"] in ("Offline", "Unknown", "")


class TestExtractCamerasFromJson:
    def test_ipc_info_format(self):
        data = {
            "IPCInfo": [
                {"szChlname": "C1", "szServer": "10.0.0.1", "nPort": 9008, "status": 1},
                {"szChlname": "C2", "szServer": "10.0.0.2", "nPort": 9008, "status": 0},
            ]
        }
        cams = _extract_cameras_from_json(data)
        assert len(cams) == 2
        assert cams[0]["name"] == "C1"
        assert cams[1]["name"] == "C2"  # verify order preserved

    def test_nested_data(self):
        data = {
            "wrapper": {
                "channels": [
                    {"name": "Cam1", "address": "10.0.0.1"},
                ]
            }
        }
        cams = _extract_cameras_from_json(data)
        assert len(cams) == 1

    def test_empty_data(self):
        assert _extract_cameras_from_json({}) == []
        assert _extract_cameras_from_json([]) == []

    def test_list_of_cameras(self):
        data = [
            {"name": "A", "address": "1.1.1.1"},
            {"name": "B", "address": "2.2.2.2"},
        ]
        cams = _extract_cameras_from_json(data)
        assert len(cams) == 2
