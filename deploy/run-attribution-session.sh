#!/usr/bin/env bash
# Run scripts/audit_attribution.py only inside the East Money board-K window.
#
# EM push2his often blocks board bars outside the A-share session, including on
# VPS IPs. Cache is incremental, so a few spaced runs per trading day fill gaps.
#
# Intended for: systemd oneshot (market-desk-attribution.service).
# Exit 0 on skip so the timer does not flap as failed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export TZ="${TZ:-Asia/Shanghai}"
export PYTHONUNBUFFERED=1

PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "skip: missing ${PY}"
  exit 0
fi

if ! "$PY" -c 'from market_desk.calendar import is_trading_day; raise SystemExit(0 if is_trading_day() else 1)'; then
  echo "skip: not an A-share trading day"
  exit 0
fi

# Minutes since midnight: 09:35–11:30 or 13:00–14:55 (leave a buffer at the open).
MINUTE="$("$PY" -c 'from datetime import datetime; n=datetime.now(); print(n.hour*60+n.minute)')"
IN_AM=0
IN_PM=0
if [[ "$MINUTE" -ge $((9 * 60 + 35)) && "$MINUTE" -le $((11 * 60 + 30)) ]]; then
  IN_AM=1
fi
if [[ "$MINUTE" -ge $((13 * 60)) && "$MINUTE" -le $((14 * 60 + 55)) ]]; then
  IN_PM=1
fi
if [[ "$IN_AM" -eq 0 && "$IN_PM" -eq 0 ]]; then
  echo "skip: outside EM board-K window (now minute=${MINUTE})"
  exit 0
fi

echo "==> audit_attribution @ $(date '+%F %T %Z') (minute=${MINUTE})"
"$PY" scripts/audit_attribution.py
status=$?
"$PY" - <<'PY'
from datetime import datetime
try:
    from market_desk.db import save_setting
    save_setting(
        "attribution_last",
        {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ok": True},
    )
except Exception as exc:
    print(f"stamp attribution_last failed: {exc}")
PY
exit "$status"
