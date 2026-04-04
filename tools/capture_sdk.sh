#!/bin/bash
# Capture SDK login traffic via tcpdump.
# Usage: docker exec tvt-api bash /tmp/capture_sdk.sh [HOST]
#   HOST defaults to TVT_HOST env var or 192.168.1.100
#   Credentials loaded from /app/.env (TVT_USERNAME, TVT_PASSWORD)
cd /app

export TVT_HOST="${1:-${TVT_HOST:-192.168.1.100}}"

# Remove old capture
rm -f /tmp/capture.pcap

# Start tcpdump in background
tcpdump -i any port 6036 -w /tmp/capture.pcap &
TCPD_PID=$!
sleep 2

# Run SDK login
NODE_PATH=/app/tvt/node_modules node /tmp/sdk_login.cjs 2>/dev/null

sleep 3

# Stop tcpdump
kill $TCPD_PID 2>/dev/null
wait $TCPD_PID 2>/dev/null

echo "--- Capture file ---"
ls -la /tmp/capture.pcap

echo "--- Packet dump ---"
tcpdump -r /tmp/capture.pcap -X -nn 2>/dev/null
