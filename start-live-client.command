#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR/albiondata-client"
echo "=========================================================="
echo " ⚔️ Starting Albion Data Client (0-Second Live Market Capture)"
echo " Note: macOS requires your Mac admin password to permit"
echo " network packet sniffing (/dev/bpf) for Albion Online."
echo "=========================================================="
sudo ./albiondata-client-executable -i "https+pow://pow.europe.albion-online-data.com,http://127.0.0.1:8765/api/ingest"

