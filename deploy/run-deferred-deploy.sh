#!/usr/bin/env bash
# Apply a deploy that was staged during the A-share session quiet window.
# Invoked by market-desk-deferred-deploy.timer (15:35 Asia/Shanghai / 07:35 UTC).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/market-desk}"
PENDING="${INSTALL_DIR}/data/deploy-pending"

if [[ ! -f "$PENDING" ]]; then
  echo "==> deferred deploy: no pending stamp at $PENDING (skip)"
  exit 0
fi

echo "==> deferred deploy: found pending"
cat "$PENDING" || true

REPO_FROM_PENDING="$(awk -F= '/^repo_dir=/{print $2; exit}' "$PENDING" || true)"
if [[ -n "${REPO_FROM_PENDING}" ]]; then
  REPO_DIR="$REPO_FROM_PENDING"
fi
REPO_DIR="${REPO_DIR:-$HOME/github/market-desk}"

# Clear before apply so a failed run can be re-staged by the next push.
rm -f "$PENDING"

export REPO_DIR INSTALL_DIR
export SKIP_PULL="${SKIP_PULL:-1}"
export FORCE_RESTART=1

if [[ ! -x "${REPO_DIR}/deploy/remote-update.sh" ]]; then
  echo "missing ${REPO_DIR}/deploy/remote-update.sh"
  exit 1
fi
chmod +x "${REPO_DIR}/deploy/remote-update.sh" "${REPO_DIR}/deploy/run-deferred-deploy.sh" || true
bash "${REPO_DIR}/deploy/remote-update.sh"
