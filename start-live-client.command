#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR/albiondata-client"
echo "=========================================================="
echo " ⚔️ Starting Albion Data Client (Live Market Capture)"
echo " Note: macOS requires your Mac admin password to permit"
echo " network packet sniffing (/dev/bpf) for Albion Online."
echo "=========================================================="
sudo ./albiondata-client-executable
