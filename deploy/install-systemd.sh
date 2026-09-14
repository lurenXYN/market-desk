#!/usr/bin/env bash
# Install market-desk as a systemd user/system service on Linux.
# Usage (from repo root, as a user with sudo):
#   ./deploy/install-systemd.sh
# Optional:
#   INSTALL_DIR=/opt/market-desk HOST=0.0.0.0 PORT=8765 ./deploy/install-systemd.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/market-desk}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
UNIT_NAME="market-desk.service"
UNIT_DST="/etc/systemd/system/${UNIT_NAME}"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run this script as a normal user with sudo; do not run as root directly."
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found. Use ./start.sh for foreground runs."
  exit 1
fi

echo "Install dir : ${INSTALL_DIR}"
echo "Service user: ${SERVICE_USER}"
echo "Bind        : ${HOST}:${PORT}"

if [[ "$ROOT" != "$INSTALL_DIR" ]]; then
  echo "Syncing source -> ${INSTALL_DIR} (excludes .venv / data / caches)..."
  sudo mkdir -p "$INSTALL_DIR"
  sudo rsync -a --delete \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'data/' \
    --exclude '.git/' \
    "$ROOT/" "$INSTALL_DIR/"
  sudo mkdir -p "$INSTALL_DIR/data"
  sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"
else
  echo "Using current tree as install dir."
  mkdir -p "$INSTALL_DIR/data"
fi

echo "Creating venv + installing deps..."
sudo -u "$SERVICE_USER" bash -lc "
  set -euo pipefail
  cd '$INSTALL_DIR'
  if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
  fi
  .venv/bin/python -m pip install -q -U pip
  .venv/bin/python -m pip install -q -r requirements.txt
"

TMP_UNIT="$(mktemp)"
sed \
  -e "s|REPLACE_USER|${SERVICE_USER}|g" \
  -e "s|/opt/market-desk|${INSTALL_DIR}|g" \
  -e "s|--host 0.0.0.0 --port 8765|--host ${HOST} --port ${PORT}|g" \
  "$ROOT/deploy/market-desk.service" > "$TMP_UNIT"

echo "Installing unit -> ${UNIT_DST}"
sudo cp "$TMP_UNIT" "$UNIT_DST"
rm -f "$TMP_UNIT"

sudo systemctl daemon-reload
sudo systemctl enable --now "$UNIT_NAME"
sudo systemctl --no-pager --full status "$UNIT_NAME" || true

echo
echo "Done. Open: http://127.0.0.1:${PORT}/"
echo "Logs:     journalctl -u ${UNIT_NAME} -f"
echo "Stop:     sudo systemctl stop ${UNIT_NAME}"
echo "Restart:  sudo systemctl restart ${UNIT_NAME}"
