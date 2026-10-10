"""Replay the MA-fan pattern over past sessions and test candidate "still valid today" rules.

For each liquid name and each past session D, ``score_pattern`` runs on bars up to D
(no look-ahead). Every hit is tagged with features measured on D itself (price vs
MAs, drawdown since the fan bar, MACD, volume shape) and with forward returns from
the next open to the close 3 / 5 / 10 sessions later, in excess of CSI 1000.
Groups are compared with day-clustered t-stats, since hits on one day share the
market move.

Usage::

    .venv/Scripts/python.exe scripts/validate_ma_fan.py [--n 800] [--refresh]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from market_desk.filters import is_st, normalize_code  # noqa: E402
from market_desk.ma_fan.job import MA_FAN_SCORE_BARS  # noqa: E402
from market_desk.ma_fan.pattern import score_pattern  # noqa: E402
from market_desk.ma_fan.sources import load_universe  # noqa: E402
from market_desk.tencent import fetch_daily_bars_symbol, tencent_symbol  # noqa: E402

CACHE = Path(tempfile.gettempdir()) / "desk_mafan_bars.json"
BENCH = "sh000852"
HORIZONS = (3, 5, 10)


async def _load(n: int, refresh: bool) -> dict[str, Any]:
    """Load ``{"names": {code: name}, "bars": {sym: [bar...]}}`` with a temp-dir cache."""
    cache: dict[str, Any] = {"names": {}, "bars": {}}
    if CACHE.exists() and not refresh:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        if len(cache["names"]) < n:
            rows = await load_universe(client, boards="all", need=n, min_amount_yi=1.2)
            for r in rows[:n]:
                code = normalize_code(r.get("code")) or ""
                if code and not is_st(str(r.get("name") or "")):
                    cache["names"][code] = str(r.get("name") or "")
        syms = [tencent_symbol(c) for c in cache["names"]] + [BENCH]
        todo = [s for s in syms if not cache["bars"].get(s)]
        sem = asyncio.Semaphore(3)

        async def _one(sym: str) -> None:
            async with sem:
                bars = await fetch_daily_bars_symbol(client, sym, limit=320)
                if bars:
                    cache["bars"][sym] = [
                        {k: b.get(k) for k in ("date", "open", "close", "high", "low", "volume")} for b in bars
                    ]
                await asyncio.sleep(0.25)

        await asyncio.gather(*(_one(s) for s in todo))
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    miss = [s for s in syms if not cache["bars"].get(s)]
    print(f"股票 {len(cache['names'])} 只，日线缺 {len(miss)}")
    return cache


def _sma(xs: list[float], i: int, n: int) -> float | None:
    return sum(xs[i - n + 1 : i + 1]) / n if i >= n - 1 else None


def _ema(xs: list[float], n: int) -> list[float]:
    k = 2.0 / (n + 1)
    out = [xs[0]]
    for x in xs[1:]:
        out.append(out[-1] + k * (x - out[-1]))
    return out


def _features(bars: list[dict[str, Any]], i: int, fan_date: str) -> dict[str, Any]:
    """Measure the candidate rules on session ``i`` (the scan day)."""
    closes = [float(b["close"]) for b in bars[: i + 1]]
    vols = [float(b.get("volume") or 0) for b in bars[: i + 1]]
    c = closes[i]
    ma = {n: _sma(closes, i, n) for n in (5, 10, 20, 30, 60)}
    mas = [ma[n] for n in (5, 10, 20, 30, 60)]
    inv = sum(1 for t in range(4) if mas[t] < mas[t + 1] * 0.998)
    k = next((j for j in range(i, max(-1, i - 10), -1) if str(bars[j]["date"]) == fan_date), i)
    peak = max(closes[k : i + 1])
    dif = [a - b for a, b in zip(_ema(closes, 12), _ema(closes, 26))]
    dea = _ema(dif, 9)
    dead3 = any(dif[t - 1] >= dea[t - 1] and dif[t] < dea[t] for t in range(i - 2, i + 1))
    v5, v10, v20 = (_sma(vols, i, n) for n in (5, 10, 20))
    up = sum(vols[t] for t in range(i - 9, i + 1) if closes[t] > closes[t - 1])
    dn = sum(vols[t] for t in range(i - 9, i + 1) if closes[t] < closes[t - 1])
    ma60_prev = _sma(closes, i - 10, 60)
    return {
        "lag": i - k,
        "below_ma20": c < ma[20],
        "below_ma10": c < ma[10] * 0.97,
        "inv2": inv > 1,
        "dd": (1 - c / peak) * 100 if peak > 0 else 0.0,
        "macd_dead": dif[i] < dea[i],
        "macd_dead3": dead3,
        "macd_gold0": dif[i] > dea[i] and dif[i] > 0,
        "vol_inc": bool(v5 and v10 and v20 and v5 > v10 > v20),
        "udr": up / dn if dn > 0 else 9.9,
        "down3": closes[i] < closes[i - 1] < closes[i - 2] < closes[i - 3] and c < ma[5],
        "ma60_weak_below": bool(ma60_prev and ma[60] < ma60_prev * 0.995 and c < ma[60]),
    }


def _fwd(bars: list[dict[str, Any]], i: int, h: int) -> float | None:
    """Return % change from the next open to the close ``h`` sessions after ``i``."""
    if i + h >= len(bars):
        return None
    entry = float(bars[i + 1].get("open") or bars[i + 1]["close"])
    return (float(bars[i + h]["close"]) / entry - 1) * 100 if entry > 0 else None


def _replay(cache: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(hits, base)``: pattern hits, and every name-session as the no-pattern baseline."""
    bench = cache["bars"].get(BENCH) or []
    bench_idx = {str(b["date"]): j for j, b in enumerate(bench)}
    rows: list[dict[str, Any]] = []
    base: list[dict[str, Any]] = []
    t0 = time.time()
    for code in cache["names"]:
        bars = cache["bars"].get(tencent_symbol(code)) or []
        if len(bars) < MA_FAN_SCORE_BARS + 12:
            continue
        for i in range(MA_FAN_SCORE_BARS - 1, len(bars) - 1):
            day = str(bars[i]["date"])
            j = bench_idx.get(day)
            ref = {"code": code, "day": day}
            for h in HORIZONS:
                r = _fwd(bars, i, h)
                b = _fwd(bench, j, h) if j is not None else None
                if r is not None and b is not None:
                    ref[f"x{h}"] = r - b
            base.append(ref)
            got = score_pattern(bars[i - MA_FAN_SCORE_BARS + 1 : i + 1], include_failed=True)
            if not got:
                continue
            row = {"code": code, "day": day, "score": got["score"], **_features(bars, i, got["fan_date"])}
            row["dd"] = float(got["drawdown"])
            row["v8_failed"] = bool(got["failed"])
            for h in HORIZONS:
                r = _fwd(bars, i, h)
                b = _fwd(bench, j, h) if j is not None else None
                if r is not None and b is not None:
                    row[f"x{h}"] = r - b
            rows.append(row)
    print(f"回放 {len(rows)} 个命中日（{time.time() - t0:.0f}s）")
    return rows, base


def _paired(yes: list[dict[str, Any]], no: list[dict[str, Any]], key: str) -> str:
    """Mean of per-day (yes − no) differences and its t-stat over days with both groups."""
    def by_day(rows: list[dict[str, Any]]) -> dict[str, float]:
        acc: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            if key in r:
                acc[r["day"]].append(r[key])
        return {d: sum(v) / len(v) for d, v in acc.items()}

    a, b = by_day(yes), by_day(no)
    diffs = [a[d] - b[d] for d in a if d in b]
    if len(diffs) < 3:
        return "-"
    mu = sum(diffs) / len(diffs)
    sd = (sum((x - mu) ** 2 for x in diffs) / (len(diffs) - 1)) ** 0.5
    return f"{mu:+.2f}(t{mu / (sd / len(diffs) ** 0.5):+.1f})" if sd else f"{mu:+.2f}"


def _stat(rows: list[dict[str, Any]], key: str) -> str:
    rows = [r for r in rows if key in r]
    if not rows:
        return f"{'-':>7}{'':>12}"
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r[key])
    means = [sum(v) / len(v) for v in by_day.values()]
    mu = sum(means) / len(means)
    t = ""
    if len(means) >= 3:
        sd = (sum((m - mu) ** 2 for m in means) / (len(means) - 1)) ** 0.5
        t = f"t{mu / (sd / len(means) ** 0.5):+.1f}" if sd else ""
    win = sum(1 for r in rows if r[key] > 0) / len(rows) * 100
    return f"{sum(r[key] for r in rows) / len(rows):>+7.2f}{f'{win:.0f}% {t}':>12}"


def _table(title: str, groups: list[tuple[str, list[dict[str, Any]]]]) -> None:
    print(f"\n== {title} ==")
    head = "".join(f"{f'{h}日超额':>8}{'胜率 t':>11}" for h in HORIZONS)
    print(f"{'分组':<16}{'命中日':>7}{'只':>6}{'天':>5} {head}")
    for name, rows in groups:
        if not rows:
            continue
        cells = " ".join(_stat(rows, f"x{h}") for h in HORIZONS)
        codes = len({r["code"] for r in rows})
        days = len({r["day"] for r in rows})
        print(f"{name:<16}{len(rows):>7}{codes:>6}{days:>5} {cells}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=800, help="liquid names by amount rank")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    cache = asyncio.run(_load(args.n, args.refresh))
    rows, base = _replay(cache)
    if not rows:
        return
    pairs: list[tuple[str, str, str]] = []

    def split(name: str, pred) -> list[tuple[str, list[dict[str, Any]]]]:
        yes = [r for r in rows if pred(r)]
        no = [r for r in rows if not pred(r)]
        pairs.append((name, _paired(yes, no, "x5"), _paired(yes, no, "x10")))
        return [(f"{name}·是", yes), (f"{name}·否", no)]

    def r1_fail(r: dict[str, Any]) -> bool:
        return r["below_ma20"] or r["below_ma10"] or r["inv2"] or r["dd"] > 8

    days = sorted({r["day"] for r in rows})
    print(f"区间 {days[0]} ~ {days[-1]}，基准 {BENCH}，进场=次日开盘")
    lo_day, hi_day = days[0], days[-1]
    base = [r for r in base if lo_day <= r["day"] <= hi_day]
    _table(
        "全部与「今天仍成立」",
        [("全体股票(基线)", base), ("全部命中", rows)]
        + split("v8判失效", lambda r: r["v8_failed"])
        + split("今天失效(合并)", r1_fail),
    )
    _table(
        "今天失效的各条",
        split("收盘<MA20", lambda r: r["below_ma20"])
        + split("收盘<0.97MA10", lambda r: r["below_ma10"])
        + split("均线倒置>1", lambda r: r["inv2"])
        + split("回撤>8%", lambda r: r["dd"] > 8)
        + split("回撤>5%", lambda r: r["dd"] > 5)
        + split("发散在今天", lambda r: r["lag"] == 0),
    )
    kept = [r for r in rows if not r1_fail(r)]
    print(f"\n以下只看「今天仍成立」的 {len(kept)} 个命中日")
    rows_all, rows = rows, kept
    _table(
        "MACD",
        split("DIF<DEA(死叉态)", lambda r: r["macd_dead"])
        + split("3日内死叉", lambda r: r["macd_dead3"])
        + split("零轴上金叉态", lambda r: r["macd_gold0"]),
    )
    _table(
        "量价与其他",
        split("量能递增", lambda r: r["vol_inc"])
        + [("量价比<0.8", [r for r in rows if r["udr"] < 0.8]),
           ("量价比0.8-1.3", [r for r in rows if 0.8 <= r["udr"] <= 1.3]),
           ("量价比>1.3", [r for r in rows if r["udr"] > 1.3])]
        + split("3日连跌破MA5", lambda r: r["down3"])
        + split("MA60弱且在下方", lambda r: r["ma60_weak_below"]),
    )
    ss = sorted(r["score"] for r in rows)
    lo, hi = ss[len(ss) // 3], ss[2 * len(ss) // 3]
    _table(
        "现有分数三分位（仅成立组）",
        [(f"低 <{lo:.0f}", [r for r in rows if r["score"] < lo]),
         (f"中 {lo:.0f}-{hi:.0f}", [r for r in rows if lo <= r["score"] < hi]),
         (f"高 >={hi:.0f}", [r for r in rows if r["score"] >= hi])],
    )
    rows = rows_all
    print("\n== 同日配对差（是 − 否，按天平均，t 按天）==")
    print(f"{'条件':<16}{'5日':>16}{'10日':>16}")
    for name, d5, d10 in pairs:
        print(f"{name:<16}{d5:>16}{d10:>16}")


if __name__ == "__main__":
    main()
