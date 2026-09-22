#!/usr/bin/env bash
# Sync a git checkout into the systemd install dir and restart the service.
# Intended to run ON the VPS (called by GitHub Actions over SSH, or manually).
#
# Env (optional):
#   REPO_DIR      git clone path          (default: ~/github/market-desk or $PWD)
#   INSTALL_DIR   runtime path           (default: /opt/market-desk)
#   SERVICE_NAME  systemd unit           (default: market-desk)
#   HEALTH_URL    post-restart probe     (default: http://127.0.0.1:8765/api/health)
#   SKIP_PULL=1   skip git fetch/reset
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="${REPO_DIR:-$DEFAULT_REPO}"
INSTALL_DIR="${INSTALL_DIR:-/opt/market-desk}"
SERVICE_NAME="${SERVICE_NAME:-market-desk}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8765/api/health}"
SKIP_PULL="${SKIP_PULL:-0}"

run_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

echo "==> repo:    $REPO_DIR"
echo "==> install: $INSTALL_DIR"
echo "==> service: $SERVICE_NAME"

cd "$REPO_DIR"

if [[ "$SKIP_PULL" != "1" ]]; then
  echo "==> git fetch + reset to origin/main"
  git fetch --prune origin
  git checkout main
  git reset --hard origin/main
fi

if [[ ! -x "$REPO_DIR/deploy/remote-update.sh" ]]; then
  echo "remote-update.sh missing after pull; abort."
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync required. apt install -y rsync"
  exit 1
fi

if [[ "$REPO_DIR" != "$INSTALL_DIR" ]]; then
  echo "==> rsync -> $INSTALL_DIR (keep .venv + data)"
  run_root mkdir -p "$INSTALL_DIR/data"
  run_root rsync -a --delete \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'data/' \
    --exclude '.git/' \
    "$REPO_DIR/" "$INSTALL_DIR/"
else
  echo "==> repo is install dir; skip rsync"
  mkdir -p "$INSTALL_DIR/data"
fi

echo "==> ensure venv + pip install"
if [[ -d "$INSTALL_DIR/.venv" ]]; then
  if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]] \
    || ! "$INSTALL_DIR/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "Removing broken/pip-less .venv"
    run_root rm -rf "$INSTALL_DIR/.venv"
  fi
fi

run_root bash -lc "
  set -euo pipefail
  cd '$INSTALL_DIR'
  if [[ ! -x .venv/bin/python ]]; then
    rm -rf .venv
    python3 -m venv .venv
  fi
  if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    .venv/bin/python -m ensurepip --upgrade || true
  fi
  if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    .venv/bin/python /tmp/get-pip.py
    rm -f /tmp/get-pip.py
  fi
  .venv/bin/python -m pip install -q -U pip
  .venv/bin/python -m pip install -q -r requirements.txt
"

echo "==> restart $SERVICE_NAME"
run_root systemctl restart "$SERVICE_NAME"

# Lifespan waits for the first East Money refresh (can be 20–60s on a remote VPS).
# Do not fail while systemd is still "activating".
echo "==> wait until systemd active"
ACTIVE_OK=0
for i in $(seq 1 60); do
  state="$(run_root systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
  if [[ "$state" == "active" ]]; then
    echo "systemd: active (${i}s)"
    ACTIVE_OK=1
    break
  fi
  if [[ "$state" == "failed" ]]; then
    echo "systemd: failed"
    run_root journalctl -u "$SERVICE_NAME" -n 60 --no-pager || true
    exit 1
  fi
  sleep 1
done
if [[ "$ACTIVE_OK" != "1" ]]; then
  echo "systemd did not become active in time: $(run_root systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
  run_root journalctl -u "$SERVICE_NAME" -n 60 --no-pager || true
  exit 1
fi

if command -v curl >/dev/null 2>&1; then
  echo "==> health check $HEALTH_URL (up to ~90s)"
  for i in $(seq 1 45); do
    if body="$(curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null)"; then
      echo "$body"
      echo "==> apply daily_snapshot patches via localhost API (force)"
      PATCH_URL="http://127.0.0.1:8765/api/internal/daily-patches/apply?force=true"
      patch_body="$(curl -fsS --max-time 30 -X POST "$PATCH_URL")" || {
        echo "patch API failed"
        echo "$patch_body"
        exit 1
      }
      echo "$patch_body"
      if ! echo "$patch_body" | grep -q '"ups": 2631' && ! echo "$patch_body" | grep -q '"ups":2631'; then
        echo "ERROR: 2026-09-21 ups not 2631 after patch; deploy aborted"
        exit 1
      fi
      echo "Deploy OK"
      exit 0
    fi
    sleep 2
  done
  echo "Service active but health check failed: $HEALTH_URL"
  run_root journalctl -u "$SERVICE_NAME" -n 60 --no-pager || true
  exit 1
fi

echo "Deploy OK (curl not installed; skipped health check)"
