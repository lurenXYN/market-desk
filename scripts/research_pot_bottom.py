"""Replay the pot-bottom (锅底) detector and test whether its lift-offs carry excess return.

Uses the ``validate_ma_fan.py`` bars cache (liquid names by current amount rank,
~1 year). For every name and session D, ``score_pot_bottom`` runs on bars up to
D only; the first hit of each base is kept (later days of the same lift-off are
not new signals). Entry is the next open; excess is versus CSI 1000 over 3 / 5 /
10 sessions. Statistics are per trading day with ``stats_tools.safe_t``; the
baseline is every liquid name on the same days, and hits are also compared with
same-day MA-fan list hits.

Also prints the 华是科技 (301218) days the detector fires on, as a sanity check.

Usage: ``python scripts/research_pot_bottom.py [--n 800] [--refresh]``
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import validate_ma_fan as vm  # noqa: E402
from market_desk.ma_fan.job import MA_FAN_SCORE_BARS  # noqa: E402
from market_desk.ma_fan.pattern import score_pattern  # noqa: E402
from market_desk.pot_bottom import score_pot_bottom  # noqa: E402
from market_desk.stats_tools import day_means, safe_t  # noqa: E402
from market_desk.tencent import tencent_symbol  # noqa: E402

HORIZONS = (3, 5, 10)
WINDOW = 130
CHECK = ("301218", "华是科技")


def _cell(rows: list[dict[str, Any]], key: str) -> str:
    rows = [r for r in rows if key in r]
    if not rows:
        return f"{'-':>22}"
    daily = [m for _, m in day_means(rows, lambda r: r["day"], lambda r: r[key])]
    tv = safe_t(daily)
    win = sum(1 for r in rows if r[key] > 0) * 100 / len(rows)
    t = f"t{tv:+.1f}" if tv is not None else ""
    return f"{sum(r[key] for r in rows) / len(rows):>+7.2f}{win:>5.0f}%{t:>8}"


def _table(title: str, groups: list[tuple[str, list[dict[str, Any]]]]) -> None:
    print(f"\n== {title} ==")
    print(f"{'分组':<20}{'次数':>7}{'天':>5}" + "".join(f"{f'{h}日超额':>9}{'胜率':>5}{'t':>8}" for h in HORIZONS))
    for name, rows in groups:
        if rows:
            print(f"{name:<20}{len(rows):>7}{len({r['day'] for r in rows}):>5}" + "".join(_cell(rows, f"x{h}") for h in HORIZONS))


def _paired(a: list[dict[str, Any]], b: list[dict[str, Any]], key: str) -> str:
    def by_day(rows: list[dict[str, Any]]) -> dict[str, float]:
        acc: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            if key in r:
                acc[r["day"]].append(r[key])
        return {d: sum(v) / len(v) for d, v in acc.items()}

    da, db = by_day(a), by_day(b)
    diffs = [da[d] - db[d] for d in sorted(da) if d in db]
    tv = safe_t(diffs)
    return "-" if tv is None else f"{sum(diffs) / len(diffs):+.2f}(t{tv:+.1f},{len(diffs)}天)"


def main() -> None:
    """Replay pot-bottom hits and print excess tables."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    cache = asyncio.run(vm._load(args.n, args.refresh))
    bench = cache["bars"].get(vm.BENCH) or []
    bench_idx = {str(b["date"]): j for j, b in enumerate(bench)}
    hits: list[dict[str, Any]] = []
    base: list[dict[str, Any]] = []
    fan: list[dict[str, Any]] = []
    t0 = time.time()
    for code in cache["names"]:
        bars = cache["bars"].get(tencent_symbol(code)) or []
        if len(bars) < WINDOW + 12:
            continue
        last_base = ""
        for i in range(WINDOW - 1, len(bars) - 1):
            day = str(bars[i]["date"])
            j = bench_idx.get(day)
            if j is None:
                continue
            fx = {}
            for h in HORIZONS:
                r, b = vm._fwd(bars, i, h), vm._fwd(bench, j, h)
                if r is not None and b is not None:
                    fx[f"x{h}"] = r - b
            base.append({"code": code, "day": day, **fx})
            got = score_pot_bottom(bars[i - WINDOW + 1 : i + 1])
            if code == CHECK[0] and day >= "2026-09-01":
                print(f"{CHECK[1]} {day} {'命中 ' + ' · '.join(got['tags']) + ' 平台' + got['base_start'] + '~' + got['base_end'] if got else '-'}")
            if got and got["base_end"] != last_base:
                last_base = got["base_end"]
                hits.append({"code": code, "day": day, **got, **fx})
            if i >= MA_FAN_SCORE_BARS - 1 and day >= "2025-11-24":
                f = score_pattern(bars[i - MA_FAN_SCORE_BARS + 1 : i + 1])
                if f:
                    fan.append({"code": code, "day": day, **fx})
    print(f"回放 {time.time() - t0:.0f}s，锅底命中 {len(hits)} 次（每个平台只记第一天），发散命中 {len(fan)} 次")
    if not hits:
        return
    days = sorted({h["day"] for h in hits})
    lo, hi = days[0], days[-1]
    base = [r for r in base if lo <= r["day"] <= hi]
    fan = [r for r in fan if lo <= r["day"] <= hi]
    mid = days[len(days) // 2]
    fan_keys = {(r["code"], r["day"]) for r in fan}
    _table(
        f"锅底命中 vs 基线（{lo} ~ {hi}，次日开盘进场，相对中证 1000）",
        [
            ("全体股票(基线)", base),
            ("发散名单", fan),
            ("锅底全部", hits),
            ("锅底·初期", [h for h in hits if h["stage"] == "初期"]),
            ("锅底·已拉开", [h for h in hits if h["stage"] != "初期"]),
            ("锅底·同日也在发散", [h for h in hits if (h["code"], h["day"]) in fan_keys]),
            ("锅底·不在发散", [h for h in hits if (h["code"], h["day"]) not in fan_keys]),
            (f"锅底·前半 <{mid}", [h for h in hits if h["day"] < mid]),
            (f"锅底·后半 >={mid}", [h for h in hits if h["day"] >= mid]),
        ],
    )
    _table(
        "锅底分项",
        [
            ("平台 8-12 日", [h for h in hits if h["base_len"] <= 12]),
            ("平台 13-20 日", [h for h in hits if 13 <= h["base_len"] <= 20]),
            ("平台 >20 日", [h for h in hits if h["base_len"] > 20]),
            ("前跌 15-25%", [h for h in hits if h["drop_pct"] < 25]),
            ("前跌 >=25%", [h for h in hits if h["drop_pct"] >= 25]),
            ("放量 1.2-2×", [h for h in hits if h["lift_vol"] < 2]),
            ("放量 2-3.5×", [h for h in hits if h["lift_vol"] >= 2]),
            ("缩量 <0.6", [h for h in hits if h["vol_shrink"] < 0.6]),
            ("缩量 0.6-0.8", [h for h in hits if h["vol_shrink"] >= 0.6]),
        ],
    )
    print("\n== 同日配对（锅底 − 同日全体 / 同日发散），按天平均 ==")
    for h in HORIZONS:
        k = f"x{h}"
        print(f"{h}日：锅底−全体 {_paired(hits, base, k)}   锅底−发散 {_paired(hits, fan, k)}")
    print(f"\n命中占比：每天平均 {len(hits) / len(days):.1f} 次，覆盖 {len(days)} 天，{len({h['code'] for h in hits})} 只票")


if __name__ == "__main__":
    main()
