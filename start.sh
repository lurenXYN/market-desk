#!/usr/bin/env bash
# Foreground launcher for Linux/macOS (mirrors start.bat).
# For always-on servers, prefer: sudo systemctl enable --now market-desk
# See deploy/market-desk.service
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON_BIN=""
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  echo "Creating venv with python3..."
  if ! python3 -m venv .venv; then
    py_ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    echo "venv failed. On Debian/Ubuntu: apt install -y python${py_ver}-venv"
    rm -rf .venv
    exit 1
  fi
  PYTHON_BIN="$ROOT/.venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  echo "Creating venv with python..."
  python -m venv .venv
  PYTHON_BIN="$ROOT/.venv/bin/python"
else
  echo "Python 3 not found. Install python3 + python3-venv first."
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Failed to create .venv"
  exit 1
fi

echo "Installing dependencies..."
"$PYTHON_BIN" -m pip install -q -U pip
"$PYTHON_BIN" -m pip install -q -r requirements.txt

PORT="${MARKET_DESK_PORT:-8765}"
HOST_HINT="${MARKET_DESK_HOST:-}"

# Best-effort free the listen port (Linux).
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
  sleep 1
elif command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -t -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${PIDS}" ]]; then
    echo "Stopping old PID(s) on :${PORT}: ${PIDS}"
    # shellcheck disable=SC2086
    kill ${PIDS} >/dev/null 2>&1 || true
    sleep 1
  fi
fi

echo "Starting market-desk (bind host comes from market_desk/config.py)..."
echo "Local:  http://127.0.0.1:${PORT}/"
if [[ -n "${HOST_HINT}" ]]; then
  echo "Hint:  http://${HOST_HINT}:${PORT}/"
fi
echo "Long-running tip: install deploy/market-desk.service via systemd."
echo "Ctrl+C stops this process."

exec "$PYTHON_BIN" -m market_desk
