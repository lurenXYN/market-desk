"""Does the long-term price position of an MA-fan hit matter (low vs high)?

``stage`` (初期/中期/后期) only measures the move away from the sticky zone, so a
name grinding up for months (e.g. 中国银行 5.40 → 6.87) still reads 初期. This
replays the MA-fan hits from ``validate_ma_fan.py`` (same bars cache, next-open
entry, excess over CSI 1000) and buckets them by position measured on the scan
day with no look-ahead:

    pos120  - (close − 120-day low) / (120-day high − 120-day low), 0..1
    rise120 - close / 120-day low − 1, percent
    pos250  - same over 250 sessions (only scan days with 250 bars of history)
    ret60   - close / close 60 sessions earlier − 1, percent

Day-clustered stats use ``stats_tools.safe_t``; "paired" compares the two
buckets on the same days, which removes the shared market move.

Usage: ``python scripts/research_ma_fan_position.py [--n 800] [--refresh]``
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import validate_ma_fan as vm  # noqa: E402
from market_desk.ma_fan.job import MA_FAN_SCORE_BARS  # noqa: E402
from market_desk.ma_fan.pattern import score_pattern  # noqa: E402
from market_desk.stats_tools import day_means, safe_t  # noqa: E402
from market_desk.tencent import tencent_symbol  # noqa: E402

HORIZONS = (5, 10)
WINDOWS = (20, 40, 60, 120, 250)


def _position(bars: list[dict[str, Any]], i: int, n: int) -> tuple[float, float] | None:
    """Return ``(pos 0..1, rise % from low)`` over the ``n`` sessions ending at ``i``."""
    if i < n - 1:
        return None
    win = bars[i - n + 1 : i + 1]
    hi = max(float(b.get("high") or b["close"]) for b in win)
    lo = min(float(b.get("low") or b["close"]) for b in win)
    c = float(bars[i]["close"])
    if lo <= 0 or hi <= lo:
        return None
    return (c - lo) / (hi - lo), (c / lo - 1.0) * 100.0


def _replay(cache: dict[str, Any]) -> list[dict[str, Any]]:
    """Replay v8 hits (failed fans excluded, as listed on the page) with position features."""
    bench = cache["bars"].get(vm.BENCH) or []
    bench_idx = {str(b["date"]): j for j, b in enumerate(bench)}
    rows: list[dict[str, Any]] = []
    t0 = time.time()
    for code in cache["names"]:
        bars = cache["bars"].get(tencent_symbol(code)) or []
        if len(bars) < MA_FAN_SCORE_BARS + 12:
            continue
        for i in range(MA_FAN_SCORE_BARS - 1, len(bars) - 1):
            got = score_pattern(bars[i - MA_FAN_SCORE_BARS + 1 : i + 1])
            if not got:
                continue
            day = str(bars[i]["date"])
            j = bench_idx.get(day)
            p120 = _position(bars, i, 120)
            if j is None or p120 is None:
                continue
            p250 = _position(bars, i, 250)
            c = float(bars[i]["close"])
            row: dict[str, Any] = {
                "code": code,
                "day": day,
                "stage": got["stage"],
                "pos120": p120[0],
                "rise120": p120[1],
                "pos250": p250[0] if p250 else None,
                "ret60": (c / float(bars[i - 60]["close"]) - 1.0) * 100.0,
            }
            for n in WINDOWS:
                if n not in (120, 250):
                    pn = _position(bars, i, n)
                    row[f"pos{n}"] = pn[0] if pn else None
            for h in HORIZONS:
                r, b = vm._fwd(bars, i, h), vm._fwd(bench, j, h)
                if r is not None and b is not None:
                    row[f"x{h}"] = r - b
            rows.append(row)
    print(f"回放 {len(rows)} 个命中日（已剔除 v8 失效），{time.time() - t0:.0f}s")
    return rows


def _cell(rows: list[dict[str, Any]], key: str) -> str:
    """Card mean, win rate, positive days and day-clustered t for one horizon."""
    rows = [r for r in rows if key in r]
    if not rows:
        return f"{'-':>24}"
    daily = [m for _, m in day_means(rows, lambda r: r["day"], lambda r: r[key])]
    tv = safe_t(daily)
    win = sum(1 for r in rows if r[key] > 0) * 100 / len(rows)
    avg = sum(r[key] for r in rows) / len(rows)
    t = f"t{tv:+.1f}" if tv is not None else ""
    return f"{avg:>+7.2f}{win:>5.0f}%{t:>8}"


def _paired(a: list[dict[str, Any]], b: list[dict[str, Any]], key: str) -> str:
    """Mean same-day difference (a − b) and its conservative t over days with both."""
    def by_day(rows: list[dict[str, Any]]) -> dict[str, float]:
        acc: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            if key in r:
                acc[r["day"]].append(r[key])
        return {d: sum(v) / len(v) for d, v in acc.items()}

    da, db = by_day(a), by_day(b)
    diffs = [da[d] - db[d] for d in sorted(da) if d in db]
    tv = safe_t(diffs)
    if tv is None:
        return "-"
    return f"{sum(diffs) / len(diffs):+.2f}(t{tv:+.1f},{len(diffs)}天)"


def _table(title: str, groups: list[tuple[str, list[dict[str, Any]]]]) -> None:
    print(f"\n== {title} ==")
    head = "".join(f"{f'{h}日超额':>8}{'胜率':>6}{'t':>8}" for h in HORIZONS)
    print(f"{'分组':<18}{'命中日':>7}{'天':>5}{head}")
    for name, rows in groups:
        if rows:
            days = len({r["day"] for r in rows})
            print(f"{name:<18}{len(rows):>7}{days:>5}" + "".join(_cell(rows, f"x{h}") for h in HORIZONS))


def _buckets(rows: list[dict[str, Any]], key: str, cuts: list[float], fmt: Callable[[float], str]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Split ``rows`` on ``key`` at ``cuts`` (ascending) into labeled buckets."""
    rows = [r for r in rows if r.get(key) is not None]
    edges = [float("-inf")] + cuts + [float("inf")]
    out = []
    for lo, hi in zip(edges, edges[1:]):
        lab = f"<{fmt(hi)}" if lo == float("-inf") else (f">={fmt(lo)}" if hi == float("inf") else f"{fmt(lo)}-{fmt(hi)}")
        out.append((f"{key} {lab}", [r for r in rows if lo <= r[key] < hi]))
    return out


def main() -> None:
    """Replay MA-fan hits and print position buckets with same-day paired differences."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=800, help="liquid names by amount rank")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    rows = _replay(asyncio.run(vm._load(args.n, args.refresh)))
    if not rows:
        return
    days = sorted({r["day"] for r in rows})
    print(f"区间 {days[0]} ~ {days[-1]}，基准 {vm.BENCH}，进场=次日开盘")
    pct = lambda v: f"{v:.0%}"  # noqa: E731
    num = lambda v: f"{v:.0f}%"  # noqa: E731
    _table("全部命中 / 按现有 stage", [("全部", rows)] + [(f"stage {s}", [r for r in rows if r["stage"] == s]) for s in ("初期", "中期", "后期")])
    _table("120 日位置（0=区间最低，1=最高）", _buckets(rows, "pos120", [0.5, 0.8, 0.95], pct))
    _table("离 120 日最低涨了多少", _buckets(rows, "rise120", [20.0, 40.0, 70.0], num))
    _table("250 日位置（有 250 日历史的扫描日）", _buckets(rows, "pos250", [0.5, 0.8, 0.95], pct))
    _table("近 60 日涨幅", _buckets(rows, "ret60", [0.0, 15.0, 35.0], num))

    print("\n== 同日配对差（前者 − 后者，按天平均，t 取保守口径）==")
    tests = [
        ("pos120 低(<50%) − 高(>=80%)", lambda r: r["pos120"] < 0.5, lambda r: r["pos120"] >= 0.8),
        ("pos120 低(<50%) − 顶(>=95%)", lambda r: r["pos120"] < 0.5, lambda r: r["pos120"] >= 0.95),
        ("rise120 <20% − >=40%", lambda r: r["rise120"] < 20, lambda r: r["rise120"] >= 40),
        ("ret60 <0% − >=35%", lambda r: r["ret60"] < 0, lambda r: r["ret60"] >= 35),
        ("pos250 低(<50%) − 高(>=80%)", lambda r: (r["pos250"] or 0) < 0.5 and r["pos250"] is not None,
         lambda r: (r["pos250"] or 0) >= 0.8),
    ]
    has250 = {r["day"] for r in rows if r["pos250"] is not None}
    mid = days[len(days) // 2]
    for n in WINDOWS:
        key = f"pos{n}"
        _table(f"{n} 日位置", _buckets(rows, key, [0.5, 0.8], pct))
    print("\n== 各窗口：低(<50%) − 高(>=80%) 同日配对，分时段 ==")
    print(f"{'窗口':<10}{'时段':<16}" + "".join(f"{f'{h}日':>22}" for h in HORIZONS) + f"{'低/中/高占比':>18}")
    for n in WINDOWS:
        key = f"pos{n}"
        for pname, pred in (
            ("全部", lambda r: True),
            ("前半", lambda r: r["day"] < mid),
            ("后半", lambda r: r["day"] >= mid),
            ("最近80天", lambda r: r["day"] in has250),
        ):
            sub = [r for r in rows if pred(r) and r.get(key) is not None]
            if not sub:
                continue
            lo = [r for r in sub if r[key] < 0.5]
            hi = [r for r in sub if r[key] >= 0.8]
            share = f"{len(lo) * 100 // len(sub)}/{(len(sub) - len(lo) - len(hi)) * 100 // len(sub)}/{len(hi) * 100 // len(sub)}%"
            print(f"{f'{n}日':<10}{pname:<16}" + "".join(f"{_paired(lo, hi, f'x{h}'):>22}" for h in HORIZONS) + f"{share:>18}")
    periods = [
        ("全部", rows),
        (f"前半 <{mid}", [r for r in rows if r["day"] < mid]),
        (f"后半 >={mid}", [r for r in rows if r["day"] >= mid]),
        ("有250日的那段", [r for r in rows if r["day"] in has250]),
    ]
    for pname, prow in periods:
        print(f"\n-- {pname}（{len({r['day'] for r in prow})} 天）--")
        print(f"{'对比':<28}" + "".join(f"{f'{h}日':>22}" for h in HORIZONS))
        for name, fa, fb in tests:
            a = [r for r in prow if fa(r)]
            b = [r for r in prow if fb(r)]
            print(f"{name:<28}" + "".join(f"{_paired(a, b, f'x{h}'):>22}" for h in HORIZONS))


if __name__ == "__main__":
    main()
