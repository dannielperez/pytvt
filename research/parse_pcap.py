#!/usr/bin/env python3
"""
RESEARCH / REFERENCE ONLY — not part of the pytvt runtime.

Parse a pcap file to extract TVT init and login packets for the same connection.
Designed to run inside the Docker container after capture_sdk.sh.

This script was used during protocol reverse engineering to understand the TVT
binary protocol handshake (init nonce exchange, login packet structure, password
encryption scheme). The findings from this analysis are implemented in
src/pytvt/protocol.py as production code.

See research/README.md for context.
"""
import struct
import sys

PCAP_FILE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/capture.pcap"

with open(PCAP_FILE, "rb") as f:
    # Read pcap global header (24 bytes)
    magic = f.read(4)
    if magic == b"\xd4\xc3\xb2\xa1":
        endian = "<"
    elif magic == b"\xa1\xb2\xc3\xd4":
        endian = ">"
    else:
        print(f"Unknown pcap magic: {magic.hex()}")
        sys.exit(1)
    hdr = f.read(20)
    ver_major, ver_minor, _, _, snaplen, linktype = struct.unpack(endian + "HHiiII", hdr)
    print(f"pcap: version={ver_major}.{ver_minor} snaplen={snaplen} linktype={linktype}")
    # linktype 113 = Linux cooked (SLL), 276 = SLL2

    pkt_num = 0
    tcp_payloads = []

    while True:
        pkt_hdr = f.read(16)
        if len(pkt_hdr) < 16:
            break
        ts_sec, ts_usec, incl_len, orig_len = struct.unpack(endian + "IIII", pkt_hdr)
        pkt_data = f.read(incl_len)
        if len(pkt_data) < incl_len:
            break
        pkt_num += 1

        # Parse link layer
        if linktype == 276:  # SLL2
            # 20-byte header: proto(2), reserved(2), ifindex(4), arphrd(2), pkttype(1), addrlen(1), addr(8)
            if len(pkt_data) < 20:
                continue
            proto = struct.unpack(">H", pkt_data[0:2])[0]
            ip_start = 20
        elif linktype == 113:  # SLL
            if len(pkt_data) < 16:
                continue
            proto = struct.unpack(">H", pkt_data[14:16])[0]
            ip_start = 16
        elif linktype == 1:  # Ethernet
            if len(pkt_data) < 14:
                continue
            proto = struct.unpack(">H", pkt_data[12:14])[0]
            ip_start = 14
        else:
            continue

        if proto != 0x0800:  # Not IPv4
            continue

        ip = pkt_data[ip_start:]
        if len(ip) < 20:
            continue
        ihl = (ip[0] & 0x0F) * 4
        ip_proto = ip[9]
        if ip_proto != 6:  # Not TCP
            continue
        src_ip = f"{ip[12]}.{ip[13]}.{ip[14]}.{ip[15]}"
        dst_ip = f"{ip[16]}.{ip[17]}.{ip[18]}.{ip[19]}"

        tcp = ip[ihl:]
        if len(tcp) < 20:
            continue
        src_port = struct.unpack(">H", tcp[0:2])[0]
        dst_port = struct.unpack(">H", tcp[2:4])[0]
        tcp_hdr_len = ((tcp[12] >> 4) & 0x0F) * 4
        payload = tcp[tcp_hdr_len:]

        if not payload:
            continue

        direction = "NVR->SDK" if src_port == 6036 else "SDK->NVR"
        tcp_payloads.append((pkt_num, direction, src_ip, src_port, dst_ip, dst_port, payload))

    print(f"\nTotal packets: {pkt_num}, TCP with payload: {len(tcp_payloads)}")
    print()

    init_nonce = None
    for i, (num, direction, sip, sport, dip, dport, payload) in enumerate(tcp_payloads):
        print(f"--- Packet #{num} {direction} ({sip}:{sport} -> {dip}:{dport}) len={len(payload)} ---")

        if direction == "NVR->SDK" and len(payload) >= 64 and payload[:4] in (b"head", b"\x11\x11\x00\x00"):
            print("  ** INIT PACKET **")
            flag = payload[:4]
            pv = struct.unpack_from("<I", payload, 12)[0]
            le = payload[44]
            nonce = payload[45:48]
            te = payload[52]
            init_nonce = int.from_bytes(nonce, "little")
            print(f"  flag={flag} protocolVer={pv} loginEncrypt={le}")
            print(f"  nonce bytes={nonce.hex()} nonce_LE={init_nonce}")
            print(f"  transportEncryptType={te}")
            print(f"  Full init hex: {payload[:64].hex()}")

        elif direction == "SDK->NVR" and len(payload) >= 16:
            # Check for 1111 prefix (4 bytes: 31 31 31 31)
            off = 0
            if payload[:4] == b"1111":
                off = 4  # skip the flag prefix

            # Header: cmd(4) cmdVer(4) cmdId(2) unused(2) dataLen(4)
            # But the SDK actually uses: flag(2) pad(1) ver(1) cmd(4) cmdVer(4) dataLen(4)
            # Let me just dump the raw hex and manually decode
            print(f"  Raw first 64 bytes: {payload[:min(64, len(payload))].hex()}")

            # Re-examine: with 1111 prefix stripped, the header is:
            # The captured login (offset after 1111):
            # fc000000 04000001 01010000 ec000000
            # This looks like: 0xfc=252 (dataLen?), 0x00010004 (cmd), 0x00000101 (cmdId?), 0xec=236 (data payload len)
            # Actually from the SDK analysis: the packet is built as 0xfc=252 byte payload total
            #   offset 0: cmd  = 0x0004 (LE u16)
            #   offset 2: pad  = 0x00
            #   offset 3: ver  = 0x01
            #   offset 4: cmdVer = 0x01000004 (LE u32) = cmd 0x00010004
            # 
            # Actually, let me re-read from the previous disassembly analysis.
            # The login buffer built at r14 (252 bytes = 0xfc):
            #   r14[0:2]  = 0x0004 (cmd low = 4)
            #   r14[2]    = 0x00
            #   r14[3]    = 0x01 (version)
            #   r14[4:8]  = 0x00000101 (cmd combined?)
            #   r14[8:12] = CreateCmdID result
            #   r14[12:16] = 0x000000ec (236 = data len)
            #   r14[16:16+236] = login data (zeroed, then filled)
            #
            # With 1111 prefix, total = 4 + 252 = 256, but we got 260 bytes. 
            # Let me just look at the first 4+16+36 bytes:
            
            hdr = payload[off:off+16]
            if len(hdr) >= 16:
                # Try to parse as: cmd_low(2) pad(1) ver(1) cmd_high(4) cmdVer(4) dataLen(4)
                cmd_low = struct.unpack_from("<H", hdr, 0)[0]
                ver = hdr[3]
                cmd_hi = struct.unpack_from("<I", hdr, 4)[0]
                cmd_ver = struct.unpack_from("<I", hdr, 8)[0]
                data_len = struct.unpack_from("<I", hdr, 12)[0]
                print(f"  Header: cmd_low=0x{cmd_low:04x} ver={ver} cmd_hi=0x{cmd_hi:08x} cmdVer={cmd_ver} dataLen={data_len}")
                
                login_data = payload[off+16:]
                if cmd_low == 0x0004 or cmd_hi == 0x00000101:
                    print("  ** LOGIN PACKET **")
                    if len(login_data) >= 120:
                        # connectType at offset 0
                        conn_type = struct.unpack_from("<I", login_data, 0)[0]
                        # Username at offset 36 (0x24), 64 bytes
                        username_raw = login_data[36:100]
                        # Password at offset 100 (0x64), claimed 20 bytes for SHA1
                        password_raw = login_data[100:120]
                        
                        print(f"  connectType={conn_type}")
                        print(f"  username raw @36: {username_raw[:32].hex()}...")
                        print(f"  password raw @100 (20 bytes): {password_raw.hex()}")
                        
                        # Also dump some key offsets
                        if len(login_data) >= 208:
                            rand_field = struct.unpack_from("<I", login_data, 204)[0]
                            print(f"  rand field @204: {rand_field}")
                        
                        # Full hex dump of the data portion
                        print(f"  Login data hex ({len(login_data)} bytes):")
                        for i in range(0, min(len(login_data), 240), 16):
                            chunk = login_data[i:i+16]
                            hexstr = ' '.join(f'{b:02x}' for b in chunk)
                            ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                            print(f"    {i:04x}: {hexstr:<48s} {ascii_str}")

                        # Verify password encryption if we have init nonce
                        if init_nonce is not None:
                            import hashlib, os
                            pw = os.environ.get("TVT_PASSWORD", "")
                            md5_upper = hashlib.md5(pw.encode()).hexdigest().upper()
                            print(f"\n  Verification (password='{pw}'):")
                            print(f"  MD5 upper: {md5_upper}")
                            for fmt_name, combined in [
                                ("%08d", md5_upper + f"{init_nonce:08d}"),
                                ("%d",   md5_upper + str(init_nonce)),
                                ("%07d", md5_upper + f"{init_nonce:07d}"),
                            ]:
                                sha1 = hashlib.sha1(combined.encode()).digest()
                                match = " <<< MATCH!" if sha1 == password_raw else ""
                                print(f"    SHA1(MD5upper+nonce{fmt_name}) = {sha1.hex()}{match}")

        else:
            print(f"  First 32 bytes: {payload[:min(32, len(payload))].hex()}")
