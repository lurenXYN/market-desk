"""Replay the MA-fan screen over historical daily bars and measure forward returns.

For every liquid name (today's amount top-N) and every past day D with enough
history, run ``score_pattern`` on bars up to D. Hits are compared with all liquid
names on the same day (same-day excess removes the market-day effect).

Caveats: the universe is today's liquidity ranking (survivorship / selection
bias); entry is D's close (``d3o`` = from D+1 open is closer to real trading).

Usage:
    python scripts/backtest_ma_fan.py [--n 1000] [--refresh]
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

from market_desk.chip_volume import build_cv, fetch_bars_with_turnover, fetch_index_closes, index_pct_map
from market_desk.config import CV_IVOL_INDEX
from market_desk.filters import listing_board_label
from market_desk.ma_fan.job import MA_FAN_BARS_LIMIT, MA_FAN_SCORE_BARS
from market_desk.ma_fan.pattern import score_pattern
from market_desk.ma_fan.sources import load_universe
from market_desk.pick_score import build_history_stats, score_pick

CACHE = Path(tempfile.gettempdir()) / "md-mafan-bt-cache.json"
MIN_AMOUNT_YI = 1.2
FWD = (1, 3, 5)


def arg(name: str, default: int) -> int:
    if name in sys.argv:
        return int(sys.argv[sys.argv.index(name) + 1])
    return default


async def load_data(n: int, refresh: bool) -> tuple[dict[str, dict[str, Any]], list[tuple[str, float]]]:
    """Return ``{code: {name, bars}}`` and index closes (cached in TEMP)."""
    if CACHE.exists() and not refresh:
        blob = json.loads(CACHE.read_text(encoding="utf-8"))
        if len(blob.get("stocks") or {}) >= n * 0.9:
            return blob["stocks"], [tuple(x) for x in blob["index"]]
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), trust_env=False) as client:
        uni = await load_universe(client, boards="all", need=n, min_amount_yi=MIN_AMOUNT_YI)
        print(f"universe {len(uni)}", flush=True)
        stocks: dict[str, dict[str, Any]] = {}
        sem = asyncio.Semaphore(4)
        done = 0

        async def one(row: dict[str, Any]) -> None:
            nonlocal done
            async with sem:
                bars = await fetch_bars_with_turnover(client, row["code"], limit=MA_FAN_BARS_LIMIT)
                await asyncio.sleep(0.08)
            done += 1
            if done % 100 == 0:
                print(f"  bars {done}/{len(uni)}", flush=True)
            if bars:
                stocks[row["code"]] = {"name": row.get("name") or "", "bars": bars}

        await asyncio.gather(*(one(r) for r in uni))
        idx = await fetch_index_closes(client, CV_IVOL_INDEX, MA_FAN_BARS_LIMIT)
    CACHE.write_text(json.dumps({"stocks": stocks, "index": idx}, ensure_ascii=False), encoding="utf-8")
    return stocks, idx


def fwd(bars: list[dict[str, Any]], i: int) -> dict[str, float | None]:
    base = bars[i]["close"]
    out: dict[str, float | None] = {}
    for n in FWD:
        out[f"d{n}"] = (bars[i + n]["close"] / base - 1) * 100 if i + n < len(bars) else None
    o = bars[i + 1].get("open") if i + 1 < len(bars) else None
    out["d3o"] = (bars[i + 3]["close"] / o - 1) * 100 if o and i + 3 < len(bars) else None
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda k: v[k])
        r = [0.0] * len(v)
        for pos, k in enumerate(order):
            r[k] = float(pos)
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = mean(rx), mean(ry)
    sx, sy = pstdev(rx), pstdev(ry)
    if not sx or not sy:
        return None
    return mean([(a - mx) * (b - my) for a, b in zip(rx, ry)]) / (sx * sy)


def main() -> None:
    n = arg("--n", 1000)
    stocks, idx = asyncio.run(load_data(n, "--refresh" in sys.argv))
    print(f"stocks {len(stocks)}  index bars {len(idx)}", flush=True)
    empty_stats = build_history_stats([])
    base_by_day: dict[str, list[dict[str, float | None]]] = {}
    hits: list[dict[str, Any]] = []
    t0 = time.monotonic()
    for k, (code, s) in enumerate(stocks.items()):
        bars = [b for b in s["bars"] if b.get("close")]
        for i in range(MA_FAN_SCORE_BARS - 1, len(bars) - 1):
            b = bars[i]
            amount_yi = (b.get("volume") or 0) * 100 * b["close"] / 1e8
            if amount_yi < MIN_AMOUNT_YI:
                continue
            f = fwd(bars, i)
            if f["d1"] is None:
                continue
            base_by_day.setdefault(b["date"], []).append(f)
            got = score_pattern(bars[i - MA_FAN_SCORE_BARS + 1 : i + 1])
            if not got:
                continue
            hits.append({"code": code, "day": b["date"], "i": i, "pat": got, **f,
                         "mkt": listing_board_label(code), "pct": b.get("pct")})
        if (k + 1) % 200 == 0:
            print(f"  scored {k + 1}/{len(stocks)} · hits {len(hits)} · {time.monotonic() - t0:.0f}s", flush=True)
    # Pick score replay (no holders / YTD limit-ups historically).
    for h in hits:
        bars = [b for b in stocks[h["code"]]["bars"] if b.get("close")]
        upto = bars[: h["i"] + 1]
        cv = build_cv(upto, upto[-1]["close"], index_pct_map([x for x in idx if x[0] <= h["day"]]))
        res = score_pick({"code": h["code"], "kind": "stock", "live_pct": h["pct"], "ma_fan": True, "cv": cv}, empty_stats)
        h["pick"], h["grade"] = res["score"], res["grade"]
    day_mean = {d: {k: mean([x[k] for x in v if x[k] is not None]) for k in ("d1", "d3", "d5", "d3o")
                    if any(x[k] is not None for x in v)} for d, v in base_by_day.items()}
    day_win = {d: {k: sum(1 for x in v if (x[k] or 0) > 0) / max(1, sum(1 for x in v if x[k] is not None))
                   for k in ("d1", "d3")} for d, v in base_by_day.items()}

    def line(lab: str, sub: list[dict[str, Any]]) -> None:
        s3 = [h for h in sub if h["d3"] is not None]
        if not s3:
            print(f"  {lab:<22}{len(sub):>6}")
            return
        d1 = [h["d1"] for h in sub]
        d3 = [h["d3"] for h in s3]
        d3o = [h["d3o"] for h in s3 if h["d3o"] is not None]
        ex3 = [h["d3"] - day_mean[h["day"]]["d3"] for h in s3 if "d3" in day_mean[h["day"]]]
        wx3 = [(1 if h["d3"] > 0 else 0) - day_win[h["day"]]["d3"] for h in s3]
        days = len({h["day"] for h in s3})
        print(f"  {lab:<22}{len(s3):>6}{days:>5}"
              f"{100 * sum(x > 0 for x in d1) / len(d1):>7.1f}{mean(d1):>+7.2f}"
              f"{100 * sum(x > 0 for x in d3) / len(d3):>7.1f}{mean(d3):>+7.2f}"
              f"{(mean(d3o) if d3o else float('nan')):>+8.2f}"
              f"{100 * mean(wx3):>+8.1f}{mean(ex3):>+8.2f}")

    head = f"  {'分组':<22}{'n':>6}{'天':>5}{'次日胜':>7}{'次日均':>7}{'三日胜':>7}{'三日均':>7}{'次开三日':>8}{'同日胜差':>8}{'同日超额':>8}"
    all_base = [x for v in base_by_day.values() for x in v if x["d3"] is not None]
    print(f"\n######## 发散回放：{len(stocks)} 只 × {len(base_by_day)} 天，命中 {len(hits)} 次")
    print(f"  对照（全部流动性达标票）：n={len(all_base)} 三日胜 {100 * sum(x['d3'] > 0 for x in all_base) / len(all_base):.1f}%"
          f" 三日均 {mean([x['d3'] for x in all_base]):+.2f}%")
    print(head)
    line("全部命中", hits)
    for key, lab in (("stage", "阶段"), ("freshness", "新鲜度")):
        for v in sorted({str(h["pat"].get(key) or "") for h in hits}):
            line(f"{lab}={v or '—'}", [h for h in hits if str(h["pat"].get(key) or "") == v])
    line("渐进发散", [h for h in hits if h["pat"].get("progressive")])
    line("MA60偏弱", [h for h in hits if not h["pat"].get("ma60_ok", True)])
    for v in ("沪A", "深A", "创业", "科创"):
        line(f"上市板={v}", [h for h in hits if h["mkt"] == v])
    for lo, hi in ((0, 60), (60, 70), (70, 80), (80, 101)):
        line(f"形态分 {lo}-{hi}", [h for h in hits if lo <= float(h["pat"]["score"]) < hi])
    for g in ("优先", "可以考虑", "谨慎", "放弃"):
        line(f"打分={g}", [h for h in hits if h["grade"] == g])
    by_day: dict[str, list[dict[str, Any]]] = {}
    for h in hits:
        by_day.setdefault(h["day"], []).append(h)
    top10 = [h for v in by_day.values() for h in sorted(v, key=lambda x: -float(x["pat"]["score"]))[:10]]
    line("每日形态分前10", top10)
    top10p = [h for v in by_day.values() for h in sorted(v, key=lambda x: -x["pick"])[:10]]
    line("每日打分前10", top10p)
    print("\n  按月：")
    for m in sorted({h["day"][:7] for h in hits}):
        line(f"  {m}", [h for h in hits if h["day"][:7] == m])
    ics_pat, ics_pick = [], []
    for v in by_day.values():
        v3 = [h for h in v if h["d3"] is not None]
        if len(v3) >= 8:
            ex = [h["d3"] for h in v3]
            a = spearman([float(h["pat"]["score"]) for h in v3], ex)
            b = spearman([float(h["pick"]) for h in v3], ex)
            if a is not None:
                ics_pat.append(a)
            if b is not None:
                ics_pick.append(b)
    for lab, ics in (("形态分", ics_pat), ("打分", ics_pick)):
        if ics:
            t = mean(ics) / (pstdev(ics) / len(ics) ** 0.5) if pstdev(ics) else float("nan")
            print(f"  命中票内 {lab} 与三日涨跌的日内秩相关 IC 均值 {mean(ics):+.3f}（{len(ics)} 天，t={t:+.1f}）")


main()
