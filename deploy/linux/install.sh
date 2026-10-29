#!/usr/bin/env bash
# Installs or updates Albion Market Pulse as a systemd service (Debian/Ubuntu).
# Run as root from a checkout of the linux-server branch:  sudo ./deploy/linux/install.sh
# Re-running updates the code and keeps config and data.
set -euo pipefail

APP=/opt/albion-pulse
ETC=/etc/albion-pulse
DATA=/var/lib/albion-pulse
SVC_USER=albion-pulse
SRC="$(cd "$(dirname "$0")/../.." && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Please run as root (sudo)."; exit 1; }

if ! python3 -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
  echo "Python 3.11 or newer is required."; exit 1
fi
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  echo "Installing python3-venv ..."
  apt-get update -qq && apt-get install -y -qq python3-venv
fi

id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$SVC_USER"
install -d -m 755 "$APP"
install -d -m 750 -o root -g "$SVC_USER" "$ETC"
install -d -m 750 -o "$SVC_USER" -g "$SVC_USER" "$DATA"

echo "Copying code to $APP ..."
rm -rf "$APP/albion_flips" "$APP/data"
cp -a "$SRC/albion_flips" "$SRC/data" "$SRC/requirements-server.txt" "$APP/"
find "$APP/albion_flips" -name '__pycache__' -prune -exec rm -rf {} +

if [ ! -x "$APP/venv/bin/python" ]; then
  python3 -m venv "$APP/venv"
fi
"$APP/venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements-server.txt"

if [ ! -f "$ETC/config.json" ]; then
  echo "Creating $ETC/config.json with new keys ..."
  cp "$SRC/deploy/linux/config.server.example.json" "$ETC/config.json"
  (cd "$APP" && "$APP/venv/bin/python" -m albion_flips.server --config "$ETC/config.json" --init-keys --write)
fi
chown root:"$SVC_USER" "$ETC/config.json"
chmod 640 "$ETC/config.json"

install -m 644 "$SRC/deploy/linux/albion-pulse.service" /etc/systemd/system/albion-pulse.service
systemctl daemon-reload
systemctl enable albion-pulse >/dev/null
systemctl restart albion-pulse
sleep 3
systemctl --no-pager --lines=5 status albion-pulse || true

echo
echo "Done. Local check:  curl -s http://127.0.0.1:8765/healthz"
echo "Keys are in $ETC/config.json (service.access_key / service.ingest_key)."
echo "Set service.public_url there, then print your links with:"
echo "  cd $APP && sudo -u $SVC_USER $APP/venv/bin/python -m albion_flips.server --config $ETC/config.json --show-links"
echo "Next: put a reverse proxy with HTTPS in front (deploy/linux/Caddyfile.example)."
