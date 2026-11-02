"""Offline as-of replay of env_gate buckets on CSI1000 (or any) daily closes.

Does not write the desk DB. Use to check whether hostile/friendly days cluster
before considering env_gate_live.

Usage:
  python scripts/replay_env_bucket.py [--code sh000852] [--since 2026-09-01]
  python scripts/replay_env_bucket.py --compare-lit   # join buy-card lit rates
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_closes(code: str, limit: int = 400) -> list[dict[str, Any]]:
    from market_desk.db import load_daily_bars

    rows = load_daily_bars(code, limit=limit)
    return rows or []


def main() -> int:
    ap = argparse.ArgumentParser(description="As-of env_bucket replay on daily bars")
    ap.add_argument("--code", default="sh000852", help="benchmark code (default CSI1000)")
    ap.add_argument("--since", default="2026-09-01")
    ap.add_argument("--until", default="")
    ap.add_argument("--compare-lit", action="store_true", help="join buy-card lit rates by bucket")
    ap.add_argument("--limit", type=int, default=400)
    args = ap.parse_args()

    from market_desk.env_gate import replay_env_buckets_from_closes

    bars = _load_closes(args.code, limit=int(args.limit))
    if not bars:
        print(f"no bars for {args.code}; is desk.db bar cache warm?")
        return 1
    out = replay_env_buckets_from_closes(
        [(b.get("date") or b.get("trade_date"), b.get("close")) for b in bars],
        bars=bars,
        since=args.since or None,
        until=args.until or None,
    )
    print(f"ok={out.get('ok')} n={out.get('n')} by_bucket={out.get('by_bucket')}")
    for row in (out.get("days") or [])[-8:]:
        print(
            f"  {row.get('date')}  {row.get('bucket'):8}  "
            f"score={row.get('score')}  side={row.get('regime_side')}  "
            f"gap={row.get('gap_pct')}"
        )

    if args.compare_lit and out.get("ok"):
        from market_desk.db import load_signals
        from market_desk.review.signals import is_buy_signal

        bucket_by_day = {d["date"]: d["bucket"] for d in out.get("days") or []}
        cells: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "lit": 0})
        for row in load_signals(limit=3000):
            if not is_buy_signal(row.get("signal_type")):
                continue
            day = str(row.get("trade_date") or "")[:10]
            b = bucket_by_day.get(day)
            if not b:
                continue
            p = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            lit = bool(p.get("ever_ready") or row.get("ready"))
            cells[b]["n"] += 1
            if lit:
                cells[b]["lit"] += 1
        print("lit_rate by replayed bucket (cards on that day):")
        for b, c in sorted(cells.items()):
            n = c["n"]
            rate = round(100.0 * c["lit"] / n, 1) if n else None
            print(f"  {b:8}  n={n}  lit={c['lit']}  lit_rate={rate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
