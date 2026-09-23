"""CLI wrapper around ``market_desk.ma_fan`` for ad-hoc scans.

Usage (from apps/market-desk)::

    .venv\\Scripts\\python.exe scripts/screen_ma_fan.py --limit 400 --top 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_desk.ma_fan import run_ma_fan_scan  # noqa: E402


def main() -> None:
    """Run one MA-fan scan and print / optionally write JSON."""
    ap = argparse.ArgumentParser(description="Screen MA stickiness → upward fan")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-amount", type=float, default=1.2)
    ap.add_argument("--boards", choices=("main", "growth", "all"), default="all")
    ap.add_argument("--persist", action="store_true", help="write ma_fan_day row")
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()
    day = datetime.now().strftime("%Y-%m-%d")
    payload = asyncio.run(
        run_ma_fan_scan(
            trade_date=day,
            limit=int(args.limit),
            top=int(args.top),
            min_amount_yi=float(args.min_amount),
            boards=str(args.boards),
            persist=bool(args.persist),
        )
    )
    items = payload.get("items") or []
    print(f"hits={len(items)} scanned={payload.get('scanned')} day={day}")
    for h in items:
        print(
            f"{h.get('code')} {h.get('name')} score={h.get('score')} "
            f"volx={h.get('vol_ratio')} {h.get('note')}"
        )
    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("wrote", args.out)


if __name__ == "__main__":
    main()
