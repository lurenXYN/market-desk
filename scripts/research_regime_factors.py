"""Research borrowed from quant practice: short-term A-share factors and market regime.

Part E — stock factors known from the literature (short-term reversal / MAX /
idiosyncratic volatility / abnormal turnover), tested on the ~2-year
uptrend-pullback pseudo events of ``optimize_pick_score.py`` and on stored buy
signals, the latter both within the same day and within the same day + theme.

Part F — regime: does a day-level emotion reading (universe limit-ups, failed
limit-up rate, streak height, breadth, index vs MA20) predict the average 3-day
outcome of that day's pullback buys? Day-level Spearman with a t-stat on
non-overlapping days, plus quintiles of days.

The universe is every code the desk has signaled (theme-heavy), so regime
readings are a proxy for the market's short-term emotion, not official stats.

Usage: ``python scripts/research_regime_factors.py [--refresh]`` (reads data/desk.db read-only)
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import optimize_pick_score as ops  # noqa: E402
from optimize_pick_score import aps, af, fmt, mean, stdev  # noqa: E402
from market_desk.pick_score import build_history_stats, score_pick  # noqa: E402

INDEX_CACHE = Path(tempfile.gettempdir()) / "md-opt-index-cache.json"
INDEXES = ("sh000001", "sz399006", "sh000852")


def limit_pct(code: str) -> float:
    """Daily limit in percent by board (ST not detectable from bars; ignored)."""
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("8", "4", "92")):
        return 30.0
    return 10.0


def series(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean bars and attach daily pct."""
    out, prev = [], None
    for b in bars:
        if not (b.get("close") and b.get("high") and b.get("low")):
            continue
        b = dict(b)
        b["pct"] = None if not prev else (b["close"] / prev - 1.0) * 100.0
        b["prev"] = prev
        out.append(b)
        prev = b["close"]
    return out


# ---------------------------------------------------------------- universe tables

def universe_tables(universe: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, float], dict[str, dict[str, float]]]:
    """Return cleaned bars per stock, equal-weight market return per date, and regime readings per date."""
    stocks = {c: series(b) for c, b in universe.items() if not c.startswith(("1", "5")) and b}
    by_date: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for code, bars in stocks.items():
        for b in bars:
            if b["pct"] is not None:
                by_date.setdefault(b["date"], []).append((code, b))
    mkt = {d: sum(b["pct"] for _, b in v) / len(v) for d, v in by_date.items() if len(v) >= 30}
    streak: dict[str, int] = {}
    regime: dict[str, dict[str, float]] = {}
    for d in sorted(by_date):
        v = by_date[d]
        if len(v) < 30:
            continue
        zt = touched = failed = up = 0
        for code, b in v:
            lim = limit_pct(code)
            hit_close = b["pct"] >= lim - 0.3
            hit_high = b["prev"] and (b["high"] / b["prev"] - 1.0) * 100.0 >= lim - 0.3
            if hit_close:
                zt += 1
                streak[code] = streak.get(code, 0) + 1
            else:
                streak[code] = 0
            if hit_high:
                touched += 1
                if not hit_close:
                    failed += 1
            up += b["pct"] > 0
        regime[d] = {
            "zt_share": 100.0 * zt / len(v),
            "fail_rate": 100.0 * failed / touched if touched else 0.0,
            "height": float(max((streak.get(c, 0) for c, _ in v), default=0)),
            "breadth": 100.0 * up / len(v),
            "mkt_ret": mkt.get(d, 0.0),
        }
    days = sorted(regime)
    for i, d in enumerate(days):
        if i >= 5:
            regime[d]["mkt_ret5"] = sum(regime[x]["mkt_ret"] for x in days[i - 4: i + 1])
            regime[d]["zt_chg"] = regime[d]["zt_share"] - mean([regime[x]["zt_share"] for x in days[i - 5: i]])
    return stocks, mkt, regime


async def fetch_indexes(refresh: bool) -> dict[str, list[dict[str, Any]]]:
    cache: dict[str, list[dict[str, Any]]] = {}
    if INDEX_CACHE.exists() and not refresh:
        try:
            cache = json.loads(INDEX_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    need = [s for s in INDEXES if not cache.get(s)]
    if need:
        async with httpx.AsyncClient() as client:
            for sym in need:
                url = (
                    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
                    f"?param={sym},day,,,{ops.UNIVERSE_BARS},qfq"
                )
                try:
                    resp = await client.get(url, timeout=10.0)
                    node = (resp.json().get("data") or {}).get(sym) or {}
                    rows = node.get("qfqday") or node.get("day") or []
                    cache[sym] = [{"date": str(p[0])[:10], "close": af._f(p[2])} for p in rows if len(p) >= 3]
                except Exception:
                    cache[sym] = []
        INDEX_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return cache


# ---------------------------------------------------------------- Part E

def stock_feats(bars: list[dict[str, Any]], t: int, mkt: dict[str, float]) -> dict[str, float | None]:
    """Literature factors at day t from bars strictly before t (t's own pct separately)."""
    win = bars[t - 20:t]
    rets = [b["pct"] for b in win if b["pct"] is not None]
    resid = [b["pct"] - mkt[b["date"]] for b in win if b["pct"] is not None and b["date"] in mkt]
    turns = [b.get("turnover") for b in bars[t - 21:t - 1] if b.get("turnover") is not None]
    t_prev = bars[t - 1].get("turnover")
    return {
        "vol20": stdev(rets) if len(rets) >= 15 else None,
        "ivol20": stdev(resid) if len(resid) >= 15 else None,
        "max20": max(rets) if len(rets) >= 15 else None,
        "min20": min(rets) if len(rets) >= 15 else None,
        "abn_turn": (t_prev / mean(turns)) if t_prev is not None and turns and mean(turns) else None,
        "ret20": (bars[t - 1]["close"] / bars[t - 21]["close"] - 1.0) * 100.0,
    }


E_FEATS = (
    ("vol20", "20日波动率"), ("ivol20", "特质波动率(去市场)"), ("max20", "20日最大单日涨幅"),
    ("min20", "20日最大单日跌幅"), ("abn_turn", "异常换手(昨/前20日均)"), ("ret20", "20日涨幅"),
)


def part_e_universe(stocks: dict[str, list[dict[str, Any]]], mkt: dict[str, float]) -> list[dict[str, Any]]:
    evs: list[dict[str, Any]] = []
    for code, bars in stocks.items():
        closes = [b["close"] for b in bars]
        for t in range(ops.WARMUP, len(bars) - ops.HOLD):
            c0, c = closes[t - 1], closes[t]
            pct = (c / c0 - 1.0) * 100.0
            ma20 = sum(closes[t - 20:t]) / 20.0
            ret20 = (c0 / closes[t - 21] - 1.0) * 100.0
            if not (c > ma20 and ret20 > 5.0 and -4.0 <= pct <= 1.0):
                continue
            ev = {"code": code, "day": bars[t]["date"], "fwd3": (closes[t + ops.HOLD] / c - 1.0) * 100.0}
            ev.update(stock_feats(bars, t, mkt))
            evs.append(ev)
    days = sorted({e["day"] for e in evs})
    print(f"\n######## E1. 成熟短线因子 · 扩样本 {len(evs)} 个回踩事件 · {len(days)} 天")
    print(f"{'因子':<24}{'天':>5}{'IC均值':>8}{'t':>7}{'IC>0':>7}{'前半':>8}{'后半':>8}   Q1..Q5 同日超额%")
    for key, lab in E_FEATS:
        s = ops.ic_summary(ops.daily_ic(evs, lambda e, k=key: e.get(k), "fwd3", min_n=10))
        qs = ops.quintiles(evs, key)
        print(
            f"{lab:<24}{s['n_days']:>5}{fmt(s['ic'], '+8.3f')}{fmt(s['t'], '+7.1f')}"
            f"{fmt(None if s['pos'] is None else 100 * s['pos'], '7.0f')}{fmt(s['ic_h1'], '+8.3f')}{fmt(s['ic_h2'], '+8.3f')}   "
            + " ".join(fmt(x, "+.2f") for x in qs)
        )
    # Win-rate view per quintile (desk cares about win rate, not mean).
    print("\n  同日五分组的三日胜率差（相对当天全部事件，pp）：Q1 低 … Q5 高")
    for key, lab in E_FEATS:
        by: dict[str, list[dict[str, Any]]] = {}
        for e in evs:
            if e.get(key) is not None:
                by.setdefault(e["day"], []).append(e)
        q: list[list[float]] = [[] for _ in range(5)]
        for rows in by.values():
            if len(rows) < 10:
                continue
            w = sum(1 for r in rows if r["fwd3"] > 0) / len(rows)
            for r, x in zip(rows, ops.ranks01([float(r[key]) for r in rows])):
                q[min(4, int((x + 0.5) * 5))].append((1.0 if r["fwd3"] > 0 else 0.0) - w)
        print(f"  {lab:<24}" + " ".join(fmt(None if not v else 100 * mean(v), "+6.1f") for v in q))
    return evs


def load_mainline() -> dict[int, str]:
    conn = sqlite3.connect(f"file:{aps.DB}?mode=ro", uri=True)
    out = {int(i): str(m or "") for i, m in conn.execute("SELECT id, mainline FROM signals WHERE signal_type LIKE 'buy%'")}
    conn.close()
    return out


def group_ic(rows: list[dict[str, Any]], key: str, group: str, min_n: int) -> tuple[float | None, int, float | None]:
    """Mean Spearman IC over groups, the number of groups and share positive."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get(key) is not None:
            by.setdefault(r[group], []).append(r)
    ics = []
    for g in by.values():
        if len(g) < min_n:
            continue
        rho = aps.spearman([float(r[key]) for r in g], [r["d3"] for r in g])
        if rho is not None:
            ics.append(rho)
    return mean(ics), len(ics), (sum(1 for x in ics if x > 0) / len(ics) if ics else None)


def part_e_signals(mkt: dict[str, float]) -> None:
    rows = [r for r in aps.load_rows() if r["entry"]]
    codes = sorted({r["code"] for r in rows})
    cache = asyncio.run(af.fetch_all(codes, refresh=False, need_flow=False))
    ml = load_mainline()
    empty = build_history_stats([])
    sig = []
    for r in rows:
        bars = series((cache.get(r["code"]) or {}).get("bars") or [])
        t = next((i for i, b in enumerate(bars) if b["date"] >= r["trade_date"]), len(bars))
        if t < 25:
            continue
        r["cand"] = aps.candidate(r, bars)
        f = stock_feats(bars, t, mkt)
        f.update({
            "d3": float(r["outcome_day3_pct"]), "day": r["trade_date"],
            "theme": r["trade_date"] + "|" + (ml.get(int(r["id"])) or "-"),
            "score": float(score_pick(r["cand"], empty)["score"]),
            "pct": af._f(r["payload"].get("pct")),
        })
        sig.append(f)
    n_theme = len({s["theme"] for s in sig})
    print(f"\n######## E2. 真实买点 {len(sig)} 条：同日 vs 同日同题材（{n_theme} 个 日×题材 组）")
    print(f"{'因子':<24}{'同日IC':>9}{'组数':>5}{'>0占比':>8}{'同题材IC':>10}{'组数':>5}{'>0占比':>8}")
    for key, lab in E_FEATS + (("pct", "当日涨跌%"), ("score", "现行打分")):
        a = group_ic(sig, key, "day", 6)
        b = group_ic(sig, key, "theme", 4)
        print(
            f"{lab:<24}{fmt(a[0], '+9.3f')}{a[1]:>5}{fmt(None if a[2] is None else 100 * a[2], '8.0f')}"
            f"{fmt(b[0], '+10.3f')}{b[1]:>5}{fmt(None if b[2] is None else 100 * b[2], '8.0f')}"
        )


# ---------------------------------------------------------------- Part F

def part_f(evs: list[dict[str, Any]], regime: dict[str, dict[str, float]], idx: dict[str, list[dict[str, Any]]]) -> None:
    for sym, bars in idx.items():
        closes = [(b["date"], b["close"]) for b in bars if b.get("close")]
        for i in range(20, len(closes)):
            d = closes[i][0]
            if d in regime:
                ma = sum(c for _, c in closes[i - 20:i]) / 20.0
                regime[d][f"{sym}_ma20"] = (closes[i][1] / ma - 1.0) * 100.0
    by: dict[str, list[float]] = {}
    for e in evs:
        by.setdefault(e["day"], []).append(e["fwd3"])
    day_y = {d: (mean(v), sum(1 for x in v if x > 0) / len(v)) for d, v in by.items() if len(v) >= 5 and d in regime}
    days = sorted(day_y)
    feats = (
        ("zt_share", "涨停占比%"), ("zt_chg", "涨停占比较前5日变化"), ("fail_rate", "炸板率%"),
        ("height", "连板高度"), ("breadth", "上涨家数占比%"), ("mkt_ret", "当日等权涨跌%"),
        ("mkt_ret5", "5日等权涨跌%"), ("sh000001_ma20", "上证距MA20%"), ("sz399006_ma20", "创业板距MA20%"),
    )
    print(f"\n######## F. 情绪周期 → 当天回踩买点的平均三日收益（{len(days)} 天，每天 ≥5 个事件）")
    print(f"  基准：日均三日收益 {mean([day_y[d][0] for d in days]):+.2f}% · 日均胜率 {100 * mean([day_y[d][1] for d in days]):.1f}%")
    print(f"{'情绪指标':<22}{'秩相关':>8}{'t(隔3天)':>9}{'前半':>8}{'后半':>8}   五档(低→高) 平均三日收益% / 胜率%")
    half = days[len(days) // 2]
    for key, lab in feats:
        pts = [(regime[d][key], day_y[d][0], day_y[d][1], d) for d in days if key in regime[d]]
        if len(pts) < 30:
            continue
        rho = aps.spearman([p[0] for p in pts], [p[1] for p in pts])
        thin = pts[::ops.HOLD]
        rho_t = aps.spearman([p[0] for p in thin], [p[1] for p in thin])
        t = rho_t * math.sqrt((len(thin) - 2) / max(1e-9, 1 - rho_t ** 2)) if rho_t is not None else None
        h1 = aps.spearman([p[0] for p in pts if p[3] < half], [p[1] for p in pts if p[3] < half])
        h2 = aps.spearman([p[0] for p in pts if p[3] >= half], [p[1] for p in pts if p[3] >= half])
        srt = sorted(pts, key=lambda p: p[0])
        k = len(srt) / 5.0
        qs = [srt[int(i * k): int((i + 1) * k)] for i in range(5)]
        cells = " ".join(f"{mean([p[1] for p in q]):+.2f}/{100 * mean([p[2] for p in q]):.0f}" for q in qs)
        print(f"{lab:<22}{fmt(rho, '+8.3f')}{fmt(t, '+9.1f')}{fmt(h1, '+8.3f')}{fmt(h2, '+8.3f')}   {cells}")
    real = [r for r in aps.load_rows() if r["entry"]]
    rd: dict[str, list[float]] = {}
    for r in real:
        rd.setdefault(r["trade_date"], []).append(float(r["outcome_day3_pct"]))
    print(f"\n  真实买点 {len(rd)} 天的日均三日收益 vs 情绪（样本太少，只看方向）：")
    for key, lab in feats:
        pts = [(regime[d][key], mean(v)) for d, v in rd.items() if d in regime and key in regime[d]]
        if len(pts) >= 8:
            print(f"    {lab:<22}{fmt(aps.spearman([p[0] for p in pts], [p[1] for p in pts]), '+.3f')}  (n={len(pts)})")
    # How much of signal outcome variance is day-level vs within-day.
    allv = [float(r["outcome_day3_pct"]) for r in real]
    m = mean(allv)
    tot = sum((x - m) ** 2 for x in allv)
    between = sum(len(v) * (mean(v) - m) ** 2 for v in rd.values())
    print(f"\n  真实买点三日收益的方差里，「哪一天买」解释了 {100 * between / tot:.0f}%，其余是同一天内挑哪只的差别。")


def index_returns(bars: list[dict[str, Any]]) -> dict[str, float]:
    out, prev = {}, None
    for b in bars:
        c = b.get("close")
        if c and prev:
            out[b["date"]] = (c / prev - 1.0) * 100.0
        prev = c or prev
    return out


def part_g(stocks: dict[str, list[dict[str, Any]]], mkt: dict[str, float], idx: dict[str, list[dict[str, Any]]]) -> None:
    """IVOL against each index as the market leg; IC, win-rate by fixed cutoffs and pooled percentiles."""
    bases = {"题材股等权": mkt, **{s: index_returns(b) for s, b in idx.items() if b}}
    print("\n######## G. 特质波动率：换不同市场基准（线上需要用指数）")
    pooled: dict[str, list[float]] = {}
    for name, base in bases.items():
        evs = []
        for code, bars in stocks.items():
            closes = [b["close"] for b in bars]
            for t in range(ops.WARMUP, len(bars) - ops.HOLD):
                c0, c = closes[t - 1], closes[t]
                pct = (c / c0 - 1.0) * 100.0
                if not (c > sum(closes[t - 20:t]) / 20.0 and (c0 / closes[t - 21] - 1.0) * 100.0 > 5.0 and -4.0 <= pct <= 1.0):
                    continue
                resid = [b["pct"] - base[b["date"]] for b in bars[t - 20:t] if b["pct"] is not None and b["date"] in base]
                if len(resid) < 15:
                    continue
                evs.append({"day": bars[t]["date"], "fwd3": (closes[t + ops.HOLD] / c - 1.0) * 100.0, "ivol": stdev(resid)})
        s = ops.ic_summary(ops.daily_ic(evs, lambda e: e["ivol"], "fwd3", min_n=10))
        vals = sorted(e["ivol"] for e in evs)
        pooled[name] = vals
        cuts = [vals[int(len(vals) * q)] for q in (0.2, 0.4, 0.6, 0.8)]
        print(f"  [{name}] 事件 {len(evs)} · IC {fmt(s['ic'])} t {fmt(s['t'], '+.1f')} 前半 {fmt(s['ic_h1'])} 后半 {fmt(s['ic_h2'])}"
              f" · 20/40/60/80 分位 " + " / ".join(f"{c:.2f}" for c in cuts))
        by: dict[str, list[dict[str, Any]]] = {}
        for e in evs:
            by.setdefault(e["day"], []).append(e)
        day_w = {d: sum(1 for e in v if e["fwd3"] > 0) / len(v) for d, v in by.items()}
        day_m = {d: mean([e["fwd3"] for e in v]) for d, v in by.items()}
        bands = [(-1, cuts[0]), (cuts[0], cuts[1]), (cuts[1], cuts[2]), (cuts[2], cuts[3]), (cuts[3], 1e9)]
        cells = []
        for lo, hi in bands:
            sub = [e for e in evs if lo <= e["ivol"] < hi and len(by[e["day"]]) >= 5]
            w = mean([(1.0 if e["fwd3"] > 0 else 0.0) - day_w[e["day"]] for e in sub])
            m = mean([e["fwd3"] - day_m[e["day"]] for e in sub])
            cells.append(f"{100 * (w or 0):+.1f}pp/{(m or 0):+.2f}%")
        print("    按固定分位档（低→高）同日胜率差/超额：" + "  ".join(cells))
        for lab, lo, hi in (("≥上 10%", vals[int(len(vals) * 0.9)], 1e9), ("≤下 10%", -1, vals[int(len(vals) * 0.1)])):
            sub = [e for e in evs if lo <= e["ivol"] < hi and len(by[e["day"]]) >= 5]
            w = mean([(1.0 if e["fwd3"] > 0 else 0.0) - day_w[e["day"]] for e in sub])
            print(f"    {lab}（阈值 {lo if lab.startswith('≤') is False else hi:.2f}）n={len(sub)} 胜率差 {100 * (w or 0):+.1f}pp")


def main() -> None:
    refresh = "--refresh" in sys.argv
    rows = [r for r in aps.load_rows() if r["entry"]]
    universe = asyncio.run(ops.fetch_universe(sorted({r["code"] for r in rows}), refresh))
    stocks, mkt, regime = universe_tables(universe)
    idx = asyncio.run(fetch_indexes(refresh))
    if "--ivol" in sys.argv:
        part_g(stocks, mkt, idx)
        return
    evs = part_e_universe(stocks, mkt)
    part_e_signals(mkt)
    part_f(evs, regime, idx)
    part_g(stocks, mkt, idx)


if __name__ == "__main__":
    main()
