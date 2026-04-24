#!/bin/bash
# ┌──────────────────────────────────────────────────────────────┐
# │  RESEARCH / REFERENCE ONLY — not part of the pytvt runtime  │
# └──────────────────────────────────────────────────────────────┘
#
# Capture SDK login traffic via tcpdump inside the tvt-api Docker container.
# Used during protocol reverse engineering to record the binary handshake
# between the native SDK (libdvrnetsdk.so) and an NVR on port 6036.
# The captured pcap can then be analyzed with parse_pcap.py / verify_capture.py.
#
# Usage: docker exec tvt-api bash /tmp/capture_sdk.sh [HOST]
#   HOST defaults to TVT_HOST env var or 192.168.1.100
#   Credentials loaded from /app/.env (TVT_USERNAME, TVT_PASSWORD)
#
# See research/README.md for context.
cd /app

export TVT_HOST="${1:-${TVT_HOST:-192.168.1.100}}"

# Remove old capture
rm -f /tmp/capture.pcap

# Start tcpdump in background
tcpdump -i any port 6036 -w /tmp/capture.pcap &
TCPD_PID=$!
sleep 2

# Run SDK login
python3 /tmp/sdk_login.py 2>/dev/null

sleep 3

# Stop tcpdump
kill $TCPD_PID 2>/dev/null
wait $TCPD_PID 2>/dev/null

echo "--- Capture file ---"
ls -la /tmp/capture.pcap

echo "--- Packet dump ---"
tcpdump -r /tmp/capture.pcap -X -nn 2>/dev/null
