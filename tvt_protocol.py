"""
TVT NVR Protocol Client - Pure Python implementation.

Speaks the TVT binary protocol on port 6036 (Server Port) to:
1. Handshake (CMD_INIT) — receives encryption params
2. Login (CMD_REQUEST_LOGIN) — with XOR-encrypted password if required
3. Get device info from login response
4. Get IPC/camera channel list (via HTTP tunnel CMD_HTTP_REQUEST)
5. Logout

Based on reverse engineering from https://github.com/2BAD/tvt
"""

import json
import os
import socket
import struct

# Protocol constants
HEADER_FLAG = b"1111"
INIT_FLAG = b"head"
INIT_PACKET_SIZE = 64  # CMD_INIT is exactly 64 bytes

# Command IDs (from valuestrings.lua)
CMD_REQUEST_LOGIN = 0x101
CMD_REQUEST_LOGOUT = 0x102
CMD_REPLY_LOGIN_SUCC = 0x1000101
CMD_REPLY_LOGIN_FAIL = 0x1000102
CMD_HTTP_REQUEST = 0x1010D00
CMD_HTTP_REPLY = 0x1010D01

# Protocol version
PROTOCOL_VER = 3  # NVR_VER_N9000


def _make_header(cmd: int, data_len: int, cmd_id: int = 0, cmd_ver: int = 0) -> bytes:
    """Build a TVT protocol header (20 bytes).

    Format:
      flag(4) + cmdLen(4) + cmd(4) + cmdId(4) + cmdVer(4) + dataLen(4)
    Total header = 8 bytes preamble + 16 bytes command
    """
    # Preamble: "1111" + total length (little-endian uint32)
    # Command part: cmd(4) + cmdId(4) + cmdVer(4) + dataLen(4) = 16 bytes
    total_len = 16 + data_len
    preamble = HEADER_FLAG + struct.pack("<I", total_len)
    command = struct.pack("<I", cmd)
    command += struct.pack("<I", cmd_id)
    command += struct.pack("<I", cmd_ver)
    command += struct.pack("<I", data_len)
    return preamble + command


def _make_login_data(username: str, password: str, init_info: dict = None) -> bytes:
    """Build CMD_REQUEST_LOGIN payload.

    Format from CMD_REQUEST_LOGIN.lua:
      connectType(4) + username(32) + password(32) + computerName(28)
      + ip(8) + mac(6) + productType(1) + reserved(1) + protocolVer(4)
    Total = 116 bytes

    If loginEncrypt > 0 in init_info, the password is XOR-encrypted.
    """
    # Encrypt password if NVR requires it
    password_bytes = _encrypt_password(password, init_info)

    data = struct.pack("<I", 1)  # connectType = 1 (normal)
    data += username.encode("ascii").ljust(32, b"\x00")[:32]
    data += password_bytes
    data += b"NVRScanner".ljust(28, b"\x00")[:28]  # computerName
    data += b"\x00" * 8  # ip
    data += b"\x00" * 6  # mac
    data += b"\x00"       # productType
    data += b"\x00"       # reserved
    data += struct.pack("<I", PROTOCOL_VER)
    return data


def _parse_init(data: bytes) -> dict:
    """Parse CMD_INIT packet (64 bytes) to extract device and encryption info.

    CMD_INIT.lua layout:
      flag(4) + devType(4) + initProductType(4) + protocolVer(4) + configVer(4) +
      id(4) + encryptType(4) + encryptParam(4) + mac(8) + initSoftwareVer(4) +
      loginEncrypt(1) + loginNonce(3) + supportSoftEncrypt(4) +
      transportEncryptType(1) + reserved(3) + reserved(8)
    """
    info = {}
    if len(data) < INIT_PACKET_SIZE:
        return info

    info["flag"] = data[0:4]
    info["devType"] = struct.unpack_from("<I", data, 4)[0]
    info["protocolVer"] = struct.unpack_from("<I", data, 12)[0]
    info["encryptType"] = struct.unpack_from("<I", data, 24)[0]
    info["encryptParam"] = struct.unpack_from("<I", data, 28)[0]
    mac_bytes = data[32:38]
    info["mac"] = ":".join(f"{b:02X}" for b in mac_bytes)
    info["loginEncrypt"] = data[44]
    info["loginNonce"] = data[45:48]
    # The 4-byte XOR key is loginEncrypt(1) + loginNonce(3) read as contiguous bytes
    info["xor_key_4"] = data[44:48]
    return info


def _encrypt_password(password: str, init_info: dict = None) -> bytes:
    """Encrypt password based on init handshake encryption params.

    loginEncrypt=0: plaintext
    loginEncrypt=1: (likely MD5, try plaintext as fallback)
    loginEncrypt=2: XOR with 4-byte key (loginEncrypt + loginNonce) repeated to 32 bytes
    """
    password_padded = password.encode("ascii").ljust(32, b"\x00")[:32]

    if not init_info or init_info.get("loginEncrypt", 0) == 0:
        return password_padded

    login_encrypt = init_info.get("loginEncrypt", 0)

    if login_encrypt in (1, 2):
        # XOR encryption: 4-byte key from loginEncrypt(1) + loginNonce(3), repeated to 32 bytes
        xor_key_4 = init_info.get("xor_key_4", b"\x00\x00\x00\x00")
        xor_key_32 = (xor_key_4 * 8)[:32]  # repeat to fill 32 bytes
        encrypted = bytes(a ^ b for a, b in zip(password_padded, xor_key_32))
        return encrypted

    # Unknown encryption type — send plaintext as fallback
    return password_padded


def _make_http_request(path: str, seq: int = 1) -> bytes:
    """Build CMD_HTTP_REQUEST payload to tunnel an HTTP GET.

    Format from CMD_HTTP_REQUEST.lua:
      httpContentLen(4) + httpSeq(4) + httpReverse(64) + httpContent(N) + endByte(1)
    """
    http_req = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: keep-alive\r\n\r\n"
    content = http_req.encode("ascii")
    data = struct.pack("<I", len(content))  # httpContentLen
    data += struct.pack("<I", seq)           # httpSeq
    data += b"\x00" * 64                     # httpReverse (reserved)
    data += content                          # httpContent
    data += b"\x00"                          # endByte
    return data


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from socket."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed while reading")
        buf += chunk
    return buf


def _recv_packet(sock: socket.socket, timeout: float = 10.0) -> tuple[int | None, bytes]:
    """Receive a single TVT protocol packet.

    Returns (cmd_type, payload_data) or (None, raw_data) for init packets.
    """
    sock.settimeout(timeout)

    # Read the flag first (4 bytes)
    flag = _recv_exact(sock, 4)

    if flag == INIT_FLAG:
        # Init packet: flag(4) + data(60) = 64 bytes total
        remaining = _recv_exact(sock, INIT_PACKET_SIZE - 4)
        return None, flag + remaining

    if flag == HEADER_FLAG:
        # Read cmdLen (4 bytes LE)
        len_buf = _recv_exact(sock, 4)
        cmd_len = struct.unpack("<I", len_buf)[0]

        if cmd_len == 0:
            # Empty "1111" + 0-length packet (keepalive/ack) — skip it
            return -1, b""

        # Read the command data (cmd_len bytes)
        remaining = _recv_exact(sock, cmd_len)

        # Parse command header: cmd(4) + cmdId(4) + cmdVer(4) + dataLen(4) = 16 bytes
        if len(remaining) >= 16:
            cmd_type = struct.unpack("<I", remaining[0:4])[0]
            data_len = struct.unpack("<I", remaining[12:16])[0]
            payload = remaining[16:16 + data_len] if data_len > 0 else b""
            return cmd_type, payload

    return None, flag


def _parse_login_response(data: bytes) -> dict:
    """Parse CMD_REPLY_LOGIN_SUCC payload to extract device info.

    Fields from CMD_REPLY_LOGIN_SUCC.lua (total ~200 bytes):
      ConfigDataLen(4) + PTZPresetNum(4) + PTZCruiseNum(4) +
      PTZPresetNumForCruise(4) + PresetNameMaxLen(4) + CruiseNameMaxLen(4) +
      bSupportPTZ(1) + videoFormat(1) + sensorInNum(1) + alarmOutNum(1) +
      ucStreamCount(1) + bSupportSnap(1) + notUsed(1) + ucLiveAudioStream(1) +
      ucTalkAudioStream(1) + audioEncodeType(1) + audioBitWidth(1) + audioChannel(1) +
      dwAudioSample(4) + UserRight(4) + softwareVer(4) + buildDate(4) +
      mac(6) + deviceName(34) + nCustomerID(4) + ...
    """
    info = {}
    if len(data) < 80:
        return info

    offset = 0
    info["config_data_len"] = struct.unpack_from("<I", data, offset)[0]; offset += 4
    offset += 20  # Skip PTZ fields (5 * 4 bytes)
    offset += 12  # Skip bSupportPTZ through audioChannel
    offset += 4   # dwAudioSample
    offset += 4   # UserRight
    info["software_ver"] = data[offset:offset+4].hex(); offset += 4
    info["build_date"] = struct.unpack_from("<I", data, offset)[0]; offset += 4
    mac_bytes = data[offset:offset+6]; offset += 6
    info["mac"] = ":".join(f"{b:02X}" for b in mac_bytes)
    device_name_raw = data[offset:offset+34]; offset += 34
    info["device_name"] = device_name_raw.split(b"\x00")[0].decode("ascii", errors="replace").strip()

    return info


def _parse_http_response_body(data: bytes) -> bytes:
    """Extract HTTP body from CMD_HTTP_REPLY payload.

    Format: httpContentLen(4) + httpSeq(4) + httpReverse(64) + httpContent(rest)
    """
    if len(data) < 72:
        return data

    content_len = struct.unpack_from("<I", data, 0)[0]
    content = data[72:]  # Skip contentLen(4) + seq(4) + reverse(64)
    return content


def scan_nvr(ip: str, port: int = 6036, username: str = "admin",
             password: str = "", timeout: float = 10.0,
             debug: bool = False) -> dict:
    """Connect to a TVT NVR and retrieve camera/IPC channel info.

    Returns a dict with device info and camera list.
    """
    result = {
        "nvr_ip": ip,
        "nvr_port": port,
        "success": False,
        "device_name": "",
        "device_info": {},
        "cameras": [],
        "error": None,
    }

    sock = None
    try:
        # Connect
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))

        if debug:
            print(f"[DEBUG] Connected to {ip}:{port}", flush=True)

        # Step 1: Receive INIT packet from NVR (64 bytes)
        cmd_type, init_data = _recv_packet(sock, timeout)

        if not init_data or len(init_data) < INIT_PACKET_SIZE:
            result["error"] = f"No init packet received (got {len(init_data)} bytes)"
            return result

        init_info = _parse_init(init_data)
        if debug:
            print(f"[DEBUG] Init: mac={init_info.get('mac')}, loginEncrypt={init_info.get('loginEncrypt')}, "
                  f"nonce={init_info.get('loginNonce', b'').hex()}, protocolVer={init_info.get('protocolVer')}", flush=True)

        # Consume the empty "1111"+0 ack packet that follows init
        ack_type, _ = _recv_packet(sock, timeout=2.0)
        if debug:
            print(f"[DEBUG] Post-init ack: cmd_type={ack_type}", flush=True)

        # Step 2: Send login request (with encrypted password if required)
        login_data = _make_login_data(username, password, init_info)
        login_packet = _make_header(CMD_REQUEST_LOGIN, len(login_data)) + login_data

        if debug:
            print(f"[DEBUG] Sending login: {len(login_packet)} bytes, password encrypted={init_info.get('loginEncrypt', 0) > 0}", flush=True)

        sock.sendall(login_packet)

        # Step 3: Receive login response (may be preceded by another ack)
        cmd_type, login_resp = _recv_packet(sock, timeout)
        # Skip empty ack packets
        while cmd_type == -1:
            cmd_type, login_resp = _recv_packet(sock, timeout)

        if debug:
            print(f"[DEBUG] Login response: cmd_type={hex(cmd_type) if isinstance(cmd_type, int) and cmd_type >= 0 else cmd_type}, "
                  f"len={len(login_resp)}", flush=True)

        if cmd_type == CMD_REPLY_LOGIN_FAIL:
            result["error"] = "Login failed: invalid credentials"
            return result

        if cmd_type == CMD_REPLY_LOGIN_SUCC:
            device_info = _parse_login_response(login_resp)
            result["device_name"] = device_info.get("device_name", "")
            result["device_info"] = device_info

        # Step 4: Request camera config via HTTP tunnel
        # TVT NVRs serve their web UI config through the same port
        # Try common TVT CGI paths for camera/channel config
        camera_paths = [
            "/queryIPCInfo",
            "/LAPI/V1.0/Channel/Table",
            "/LAPI/V1.0/System/DeviceInfo",
        ]

        for i, path in enumerate(camera_paths):
            http_data = _make_http_request(path, seq=i + 1)
            http_packet = _make_header(CMD_HTTP_REQUEST, len(http_data)) + http_data
            sock.sendall(http_packet)

            cmd_type, resp_data = _recv_packet(sock, timeout=5.0)

            if debug:
                print(f"[DEBUG] HTTP tunnel {path}: cmd_type={hex(cmd_type) if cmd_type else None}, resp_len={len(resp_data)}, first_128={resp_data[:128].hex() if resp_data else 'empty'}", flush=True)
                if resp_data:
                    body = _parse_http_response_body(resp_data)
                    print(f"[DEBUG] HTTP body preview: {body[:500]}", flush=True)

            if cmd_type == CMD_HTTP_REPLY and resp_data:
                body = _parse_http_response_body(resp_data)
                body_str = body.decode("utf-8", errors="replace")

                # Try to parse as JSON
                # Find JSON start
                json_start = -1
                for marker in [b"{", b"["]:
                    idx = body.find(marker)
                    if idx >= 0 and (json_start < 0 or idx < json_start):
                        json_start = idx

                if json_start >= 0:
                    try:
                        json_data = json.loads(body[json_start:])
                        result["http_response_" + path.replace("/", "_")] = json_data

                        # Try to extract camera info from response
                        cameras = _extract_cameras_from_json(json_data)
                        if cameras:
                            result["cameras"] = cameras
                            result["success"] = True
                    except json.JSONDecodeError:
                        pass

        # If HTTP tunnel didn't yield cameras, the login response itself
        # tells us channel count, which is still useful
        if not result["cameras"] and result["device_info"]:
            result["success"] = True
            result["error"] = "Connected but could not retrieve camera details via HTTP tunnel"

        # Step 5: Logout
        try:
            logout_packet = _make_header(CMD_REQUEST_LOGOUT, 0)
            sock.sendall(logout_packet)
        except Exception:
            pass

    except socket.timeout:
        result["error"] = f"Connection timed out ({timeout}s)"
    except ConnectionRefusedError:
        result["error"] = "Connection refused"
    except OSError as e:
        result["error"] = f"Network error: {e}"
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    return result


def _extract_cameras_from_json(data) -> list[dict]:
    """Try to extract camera info from various JSON response formats."""
    cameras = []

    if isinstance(data, dict):
        # Check for common TVT response structures
        for key in ["IPCInfo", "ChannelInfo", "channels", "Channel", "data", "rows"]:
            if key in data:
                items = data[key]
                if isinstance(items, list):
                    for item in items:
                        cam = _normalize_camera(item)
                        if cam:
                            cameras.append(cam)
                    if cameras:
                        return cameras

        # Recurse into nested dicts
        for value in data.values():
            cams = _extract_cameras_from_json(value)
            if cams:
                return cams

    elif isinstance(data, list):
        for item in data:
            cam = _normalize_camera(item) if isinstance(item, dict) else None
            if cam:
                cameras.append(cam)

    return cameras


def _normalize_camera(item: dict) -> dict | None:
    """Normalize a single camera dict from various JSON formats."""
    if not isinstance(item, dict):
        return None

    # Map various field name conventions
    name = (item.get("szChlname") or item.get("ChannelName") or
            item.get("channelName") or item.get("name") or
            item.get("CameraName") or item.get("cameraName") or "")
    address = (item.get("szServer") or item.get("Address") or
               item.get("address") or item.get("IP") or
               item.get("ip") or item.get("IpAddr") or "")
    port = (item.get("nPort") or item.get("Port") or
            item.get("port") or 0)
    status_val = item.get("status") or item.get("Status") or item.get("OnlineStatus") or ""
    channel = item.get("channel") or item.get("Channel") or item.get("ChannelNo") or 0
    model = (item.get("productModel") or item.get("Model") or
             item.get("model") or item.get("ProductModel") or "")
    protocol = (item.get("manufacturerName") or item.get("Protocol") or
                item.get("protocol") or "")

    if not name and not address:
        return None

    if isinstance(status_val, int):
        status = "Online" if status_val == 1 else "Offline"
    elif isinstance(status_val, str):
        status = status_val
    else:
        status = "Unknown"

    return {
        "channel": channel,
        "name": str(name).strip(),
        "address": str(address).strip(),
        "port": int(port) if port else 0,
        "status": status,
        "protocol": str(protocol).strip(),
        "model": str(model).strip(),
    }


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()

    ip = sys.argv[1] if len(sys.argv) > 1 else "10.0.1.250"
    user = os.environ.get("TVT_USERNAME", "admin")
    pwd = os.environ.get("TVT_PASSWORD", "")
    result = scan_nvr(ip, username=user, password=pwd, debug=True)
    print(json.dumps(result, indent=2))
