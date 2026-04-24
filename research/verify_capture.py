#!/usr/bin/env python3
"""
RESEARCH / REFERENCE ONLY — not part of the pytvt runtime.

Extract password from captured login packet and verify encryption scheme.

This script reads a pcap capture of SDK ↔ NVR traffic, locates the init and
login packets, and brute-forces nonce/hash combinations to confirm the
password encryption algorithm. Used together with capture_sdk.sh and
sdk_login.py to validate protocol findings.

See research/README.md for context.
"""
import hashlib
import os
import struct
import sys

# Load .env
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

password = os.environ.get("TVT_PASSWORD", "")
if not password:
    print("TVT_PASSWORD not set")
    sys.exit(1)

pcap_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/capture.pcap"

with open(pcap_file, "rb") as f:
    magic = f.read(4)
    endian = "<" if magic == b"\xd4\xc3\xb2\xa1" else ">"
    hdr = f.read(20)
    _, _, _, _, snaplen, linktype = struct.unpack(endian + "HHiiII", hdr)

    init_nonce_le = None
    login_pw = None

    while True:
        pkt_hdr = f.read(16)
        if len(pkt_hdr) < 16:
            break
        ts_sec, ts_usec, incl_len, orig_len = struct.unpack(endian + "IIII", pkt_hdr)
        pkt_data = f.read(incl_len)
        if len(pkt_data) < incl_len:
            break

        # Parse link layer
        if linktype == 276:  # SLL2
            proto = struct.unpack(">H", pkt_data[0:2])[0]
            ip_start = 20
        elif linktype == 113:
            proto = struct.unpack(">H", pkt_data[14:16])[0]
            ip_start = 16
        elif linktype == 1:
            proto = struct.unpack(">H", pkt_data[12:14])[0]
            ip_start = 14
        else:
            continue
        if proto != 0x0800:
            continue

        ip = pkt_data[ip_start:]
        if len(ip) < 20:
            continue
        ihl = (ip[0] & 0x0F) * 4
        if ip[9] != 6:
            continue

        src_port = struct.unpack(">H", ip[ihl:ihl+2])[0]
        dst_port = struct.unpack(">H", ip[ihl+2:ihl+4])[0]
        tcp_hdr_len = ((ip[ihl+12] >> 4) & 0x0F) * 4
        payload = ip[ihl+tcp_hdr_len:]
        if not payload:
            continue

        # NVR -> SDK: look for init packet
        if src_port == 6036 and len(payload) >= 64 and payload[:4] == b"head":
            init_nonce_bytes = payload[45:48]
            init_nonce_le = int.from_bytes(init_nonce_bytes, "little")
            login_encrypt = payload[44]
            pv = struct.unpack_from("<I", payload, 12)[0]
            print(f"INIT: protocolVer={pv} loginEncrypt={login_encrypt} nonce={init_nonce_bytes.hex()} nonce_LE={init_nonce_le}")

        # SDK -> NVR: look for login packet
        elif dst_port == 6036 and len(payload) >= 20 and payload[:4] == b"1111":
            # Skip 1111(4) + header(16) = data at offset 20
            data = payload[20:]
            # Check if this looks like a login (connectType at offset 0, username area at offset 36)
            if len(data) >= 120:
                conn_type = struct.unpack_from("<I", data, 0)[0]
                username_enc = data[36:100]
                login_pw = data[100:120]

                # Verify username via XOR
                if init_nonce_le is not None:
                    key_str = str(init_nonce_le).encode()
                    dec_user = bytes(b ^ key_str[i % len(key_str)] for i, b in enumerate(username_enc[:10]))
                    if dec_user[:5] == b"admin":
                        print(f"LOGIN: connectType={conn_type}")
                        print(f"  Username XOR decrypted: {dec_user}")
                        print(f"  Encrypted password (20 bytes): {login_pw.hex()}")

                        # Now verify the password encryption scheme
                        md5_upper = hashlib.md5(password.encode()).hexdigest().upper()
                        print(f"\n  --- Verification ---")
                        print(f"  Password: {password}")
                        print(f"  MD5(pw).upper(): {md5_upper}")
                        print(f"  Nonce LE int:    {init_nonce_le}")

                        tests = [
                            ("%08d",  md5_upper + f"{init_nonce_le:08d}"),
                            ("%d",    md5_upper + str(init_nonce_le)),
                            ("%07d",  md5_upper + f"{init_nonce_le:07d}"),
                            ("BE %08d", md5_upper + f"{int.from_bytes(init_nonce_bytes, 'big'):08d}"),
                        ]

                        for fmt_name, combined in tests:
                            sha1 = hashlib.sha1(combined.encode()).digest()
                            match = " <<< MATCH!" if sha1 == login_pw else ""
                            print(f"  SHA1(md5+nonce{fmt_name:>8s}) = {sha1.hex()}{match}")
                            if match:
                                print(f"\n  *** PASSWORD ENCRYPTION SCHEME CONFIRMED ***")
                                print(f"  Algorithm: SHA1( MD5(password).hexdigest().upper() + sprintf('{fmt_name}', nonce_LE_uint24) )")

                        # If none matched, try more combos
                        if all(hashlib.sha1(c.encode()).digest() != login_pw for _, c in tests):
                            print(f"\n  No match. Trying more combinations...")

                            # Try with the 4-byte loginEncrypt+nonce
                            nonce4_le = int.from_bytes(payload[64:68], "little") if len(payload) > 68 else None

                            # Try XOR of SHA1 with nonce
                            sha1_plain = hashlib.sha1(password.encode()).digest()
                            xor_nonce = bytes(a ^ b for a, b in zip(sha1_plain, (key_str * 3)[:20]))
                            print(f"  SHA1(pw) XOR nonce_str: {xor_nonce.hex()} {'MATCH!' if xor_nonce == login_pw else ''}")

                            # Try MD5 binary + nonce -> SHA1
                            md5_bin = hashlib.md5(password.encode()).digest()
                            for n_str in [f"{init_nonce_le:08d}", str(init_nonce_le)]:
                                combined = md5_bin + n_str.encode()
                                sha1 = hashlib.sha1(combined).digest()
                                print(f"  SHA1(md5_bin+'{n_str}'): {sha1.hex()} {'MATCH!' if sha1 == login_pw else ''}")

                            # Try swapped: nonce + MD5
                            for n_str in [f"{init_nonce_le:08d}", str(init_nonce_le)]:
                                combined = n_str + md5_upper
                                sha1 = hashlib.sha1(combined.encode()).digest()
                                print(f"  SHA1('{n_str}'+md5upper): {sha1.hex()} {'MATCH!' if sha1 == login_pw else ''}")

                        break  # Found and processed login

print("\nDone.")
