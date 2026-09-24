"""CLI wrapper around ``market_desk.ma_fan`` for ad-hoc scans.

Usage (from apps/market-desk)::

    .venv\\Scripts\\python.exe scripts/screen_ma_fan.py --slice 0-400 --persist
    .venv\\Scripts\\python.exe scripts/screen_ma_fan.py --all --persist
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

from market_desk.ma_fan import (  # noqa: E402
    MA_FAN_SCHEDULE,
    run_ma_fan_all_due_slices,
    run_ma_fan_scan,
)


def main() -> None:
    """Run one MA-fan slice (or all) and print / optionally write JSON."""
    ap = argparse.ArgumentParser(description="Screen MA stickiness → upward fan")
    ap.add_argument("--slice", choices=[k for *_, k in MA_FAN_SCHEDULE], default="0-400")
    ap.add_argument("--all", action="store_true", help="run 0-400 + 400-800 + 800-1000")
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--min-amount", type=float, default=1.2)
    ap.add_argument("--boards", choices=("main", "growth", "all"), default="all")
    ap.add_argument("--persist", action="store_true")
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()
    day = datetime.now().strftime("%Y-%m-%d")
    if args.all:
        payload = asyncio.run(
            run_ma_fan_all_due_slices(
                trade_date=day,
                minutes=22 * 60,
                top=int(args.top),
                min_amount_yi=float(args.min_amount),
                boards=str(args.boards),
                force_all=True,
            )
        )
    else:
        offset, count, key = next(
            (o, c, k) for _m, o, c, k in MA_FAN_SCHEDULE if k == args.slice
        )
        payload = asyncio.run(
            run_ma_fan_scan(
                trade_date=day,
                offset=offset,
                limit=count,
                slice_key=key,
                top=int(args.top),
                min_amount_yi=float(args.min_amount),
                boards=str(args.boards),
                persist=bool(args.persist),
                replace=False,
            )
        )
    items = payload.get("items") or []
    print(
        f"hits={len(items)} scanned={payload.get('scanned')} "
        f"slices={payload.get('slices_done')} day={day}"
    )
    for h in items[:20]:
        print(
            f"{h.get('code')} {h.get('name')} {h.get('stage')} "
            f"score={h.get('score')} volx={h.get('vol_ratio')} {h.get('note')}"
        )
    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("wrote", args.out)


if __name__ == "__main__":
    main()
