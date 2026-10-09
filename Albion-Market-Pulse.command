#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

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
