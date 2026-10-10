#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Launch Albion Data Client if present and not running
if ! pgrep -f "albiondata-client" >/dev/null 2>&1; then
    if [ -f "$DIR/albiondata-client/albiondata-client-executable" ]; then
        osascript << APPLESCRIPT
tell application "Terminal"
    do script "cd \"$DIR/albiondata-client\" && echo '⚔️  Starting Albion Data Client (0s Direct Ingest)...' && sudo ./albiondata-client-executable -i \"https+pow://pow.europe.albion-online-data.com,http://127.0.0.1:8765/api/ingest\""
end tell
APPLESCRIPT
    fi
fi

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi

if [ ! -f "config.json" ] && [ -f "config.example.json" ]; then
    cp config.example.json config.json
fi

echo "=========================================================="
echo " ⚔️  Starting Albion Market Pulse (Web Dashboard & Scans)"
echo " 👉 Web Dashboard: http://localhost:8765"
echo "=========================================================="

(sleep 2 && open "http://localhost:8765") &
.venv/bin/python -m albion_flips.cli --watch --web
