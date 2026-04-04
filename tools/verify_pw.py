#!/usr/bin/env python3
"""
Connect to an NVR, capture the init packet nonce, then verify the
password encryption scheme discovered from SDK disassembly:
  SHA1( MD5(password).upper() + sprintf("%08d", nonce_LE_uint24) )
"""
import hashlib
import os
import socket
import struct
import sys

# Load .env if present
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

host = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TVT_HOST", "192.168.1.100")
port = int(os.environ.get("TVT_PORT", "6036"))
password = os.environ.get("TVT_PASSWORD")
if not password:
    print("TVT_PASSWORD not set"); sys.exit(1)

print(f"Connecting to {host}:{port} ...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(10)
sock.connect((host, port))

# Read the 64-byte init packet
data = sock.recv(64)
sock.close()

if len(data) < 64:
    print(f"Short init packet: {len(data)} bytes"); sys.exit(1)

flag = data[:4]
protocol_ver = struct.unpack_from("<I", data, 12)[0]
login_encrypt = data[44]
nonce_bytes = data[45:48]
nonce_le = int.from_bytes(nonce_bytes, "little")
transport_encrypt = data[52]

print(f"Init packet: flag={flag} protocolVer={protocol_ver} loginEncrypt={login_encrypt}")
print(f"  nonce bytes: {nonce_bytes.hex()}")
print(f"  nonce LE uint24: {nonce_le}")
print(f"  transportEncryptType: {transport_encrypt}")
print()

# Step 1: MD5 uppercase hex
md5_upper = hashlib.md5(password.encode()).hexdigest().upper()
print(f"MD5(password).upper() = {md5_upper}")

# Step 2: Concatenate with nonce using %08d format
combined = md5_upper + f"{nonce_le:08d}"
print(f"Combined ({len(combined)} chars): {combined}")

# Step 3: SHA1 of combined string
sha1_result = hashlib.sha1(combined.encode()).digest()
print(f"SHA1 (20 bytes hex): {sha1_result.hex()}")
print()

# Also try alternative interpretations to be thorough
print("=== Alternative nonce interpretations ===")
for name, n in [
    ("LE uint24", nonce_le),
    ("BE uint24", int.from_bytes(nonce_bytes, "big")),
]:
    for fmt in ["%08d", "%d", "%07d"]:
        c = md5_upper + (fmt % n)
        s = hashlib.sha1(c.encode()).digest()
        print(f"  {name} fmt={fmt:5s} nonce={n:>10d}: SHA1={s.hex()}")

# Try with nonce including loginEncrypt byte (4-byte value)
nonce_4 = data[44:48]
n4 = int.from_bytes(nonce_4, "little")
c4 = md5_upper + f"{n4:08d}"
s4 = hashlib.sha1(c4.encode()).digest()
print(f"  4-byte LE      fmt=%08d nonce={n4:>10d}: SHA1={s4.hex()}")

# Try lowercase MD5
md5_lower = hashlib.md5(password.encode()).hexdigest()
c_lower = md5_lower + f"{nonce_le:08d}"
s_lower = hashlib.sha1(c_lower.encode()).digest()
print(f"  md5_lower+%08d            nonce={nonce_le:>10d}: SHA1={s_lower.hex()}")

print()
print("To verify: capture tcpdump of SDK login and compare the 20 password bytes")
print("at offset 100 (0x64) in the login payload against the SHA1 above.")
