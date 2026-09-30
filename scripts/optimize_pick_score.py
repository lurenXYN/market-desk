"""Offline factor research for the pick score (IC analysis + walk-forward models).

Part A — broad pseudo-events: ~2 years of forward-adjusted daily bars for every
code the desk has ever signaled. Each "uptrend pullback" day (close above MA20,
20-day return > 5%, day change in [-4%, +1%]) is one pseudo buy at the close.
Features use bars strictly before that day (plus the day's own change), the
target is the 3-day forward return. Reports the daily cross-sectional rank IC
(mean, t-stat on non-overlapping days, share of positive days) and bucket
excess returns for the desk's chip / volume tags.

Part B — stored buy signals: per-feature daily rank IC against the 3-day
outcome, then purged walk-forward models (ridge on per-day ranks, IC-weighted
composite) vs the live score on identical out-of-sample days.

Part C — history nudge: current threshold + cap vs empirical-Bayes shrinkage,
walk-forward.

Usage: ``python scripts/optimize_pick_score.py [--refresh]`` (reads data/desk.db read-only)
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import audit_pick_score as aps  # noqa: E402  (patches DB_PATH to a scratch file)
import audit_factors as af  # noqa: E402
from market_desk.chip_volume import classify  # noqa: E402
from market_desk.config import HTTP_HEADERS  # noqa: E402
from market_desk.pick_score import build_history_stats, score_pick  # noqa: E402
import market_desk.pick_score as ps  # noqa: E402

UNIVERSE_CACHE = Path(tempfile.gettempdir()) / "md-opt-universe-cache.json"
UNIVERSE_BARS = 500
CHIP_BINS = 160
WARMUP = 70
HOLD = 3
LAG_DAYS = aps.LAG_DAYS


# ---------------------------------------------------------------- stats helpers

def mean(v: list[float]) -> float | None:
    return sum(v) / len(v) if v else None


def stdev(v: list[float]) -> float | None:
    if len(v) < 2:
        return None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def ranks01(v: list[float]) -> list[float]:
    """Average ranks scaled to [-0.5, 0.5]."""
    n = len(v)
    order = sorted(range(n), key=lambda i: v[i])
    r = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0
        i = j + 1
    return [x / (n - 1) - 0.5 for x in r] if n > 1 else [0.0] * n


def ic_summary(daily: dict[str, float], every: int = HOLD) -> dict[str, Any]:
    """Mean IC, t-stat on every ``every``-th day (non-overlapping holds), share positive."""
    days = sorted(daily)
    vals = [daily[d] for d in days]
    thin = vals[::every]
    sd = stdev(thin)
    m = mean(vals)
    t = (mean(thin) / sd * math.sqrt(len(thin))) if sd and len(thin) > 2 else None
    half = len(vals) // 2
    return {
        "n_days": len(vals),
        "ic": m,
        "t": t,
        "pos": sum(1 for x in vals if x > 0) / len(vals) if vals else None,
        "ic_h1": mean(vals[:half]),
        "ic_h2": mean(vals[half:]),
    }


def daily_ic(rows: list[dict[str, Any]], feat: Callable[[dict[str, Any]], float | None],
             target: str, min_n: int = 8) -> dict[str, float]:
    """Spearman IC per day between a feature and the target."""
    by: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        x = feat(r)
        if x is None or r.get(target) is None:
            continue
        by.setdefault(r["day"], []).append((float(x), float(r[target])))
    out: dict[str, float] = {}
    for d, pairs in by.items():
        if len(pairs) < min_n:
            continue
        rho = aps.spearman([p[0] for p in pairs], [p[1] for p in pairs])
        if rho is not None:
            out[d] = rho
    return out


def fmt(x: float | None, spec: str = "+.3f") -> str:
    return "   —" if x is None else format(x, spec)


# ---------------------------------------------------------------- Part A data

async def _fetch_long(client: httpx.AsyncClient, code: str) -> list[dict[str, Any]]:
    sym = ("sh" if code.startswith(("5", "6", "9")) else "sz") + code
    url = (
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        f"?param={sym},day,,,{UNIVERSE_BARS},qfq"
    )
    for attempt in range(3):
        try:
            resp = await client.get(url, headers={"User-Agent": HTTP_HEADERS["User-Agent"]}, timeout=10.0)
            node = (resp.json().get("data") or {}).get(sym) or {}
            rows = node.get("qfqday") or node.get("day") or []
            bars = []
            for p in rows:
                if len(p) < 8 or af._f(p[2]) is None:
                    continue
                bars.append({
                    "date": str(p[0])[:10], "open": af._f(p[1]), "close": af._f(p[2]),
                    "high": af._f(p[3]), "low": af._f(p[4]), "volume": af._f(p[5]),
                    "turnover": af._f(p[7]),
                })
            if bars:
                return bars
        except Exception:
            pass
        await asyncio.sleep(1.0 + attempt)
    return []


async def fetch_universe(codes: list[str], refresh: bool) -> dict[str, list[dict[str, Any]]]:
    cache: dict[str, list[dict[str, Any]]] = {}
    if UNIVERSE_CACHE.exists() and not refresh:
        try:
            cache = json.loads(UNIVERSE_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    todo = [c for c in codes if not cache.get(c)]
    if todo:
        print(f"fetching {len(todo)} long bar series ...", flush=True)
        sem = asyncio.Semaphore(4)

        async def one(client: httpx.AsyncClient, code: str) -> None:
            async with sem:
                cache[code] = await _fetch_long(client, code)

        async with httpx.AsyncClient() as client:
            await asyncio.gather(*(one(client, c) for c in todo))
        UNIVERSE_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def _tri_weights(lo: float, hi: float, avg: float, grid: list[float], lo_all: float, step: float) -> list[tuple[int, float]]:
    i0 = max(0, int((lo - lo_all) / step))
    i1 = min(len(grid) - 1, int(round((hi - lo_all) / step)))
    if i1 <= i0:
        return [(i0, 1.0)]
    out = []
    for i in range(i0, i1 + 1):
        p = grid[i]
        if p <= avg:
            w = (p - lo) / (avg - lo) if avg > lo else 1.0
        else:
            w = (hi - p) / (hi - avg) if hi > avg else 1.0
        if w > 0:
            out.append((i, w))
    s = sum(w for _, w in out) or 1.0
    return [(i, w / s) for i, w in out] or [(i0, 1.0)]


def stock_events(code: str, bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk one stock's bars, keeping an incremental chip distribution, and emit pullback events."""
    bars = [b for b in bars if b.get("high") and b.get("low") and b.get("close") and b.get("volume") is not None]
    if len(bars) < WARMUP + HOLD + 5:
        return []
    lo_all = min(b["low"] for b in bars)
    hi_all = max(b["high"] for b in bars)
    if hi_all <= lo_all:
        return []
    step = (hi_all - lo_all) / (CHIP_BINS - 1)
    grid = [lo_all + i * step for i in range(CHIP_BINS)]
    chips = [0.0] * CHIP_BINS
    closes = [b["close"] for b in bars]
    vols = [b["volume"] or 0.0 for b in bars]
    out: list[dict[str, Any]] = []
    for t, b in enumerate(bars):
        # Chips/volume state here reflects bars[0..t-1] (strictly before day t).
        if WARMUP <= t < len(bars) - HOLD:
            c0, c = closes[t - 1], closes[t]
            pct = (c / c0 - 1.0) * 100.0
            ma20 = sum(closes[t - 20:t]) / 20.0
            ret20 = (c0 / closes[t - 21] - 1.0) * 100.0
            if c > ma20 and ret20 > 5.0 and -4.0 <= pct <= 1.0:
                total = sum(chips)
                if total > 0:
                    below = sum(ch for g, ch in zip(grid, chips) if g <= c)
                    avg_cost = sum(g * ch for g, ch in zip(grid, chips)) / total
                    cum, p5, p95 = 0.0, grid[0], grid[-1]
                    for g, ch in zip(grid, chips):
                        prev = cum
                        cum += ch / total
                        if prev < 0.05 <= cum:
                            p5 = g
                        if prev < 0.95 <= cum:
                            p95 = g
                    smooth = [sum(chips[max(0, i - 2): i + 3]) for i in range(CHIP_BINS)]
                    peak = grid[max(range(CHIP_BINS), key=lambda i: smooth[i])]
                    base5 = sum(vols[t - 6:t - 1]) / 5.0
                    early = sum(vols[t - 10:t - 3]) / 7.0
                    pct_prev = (closes[t - 1] / closes[t - 2] - 1.0) * 100.0
                    vr = vols[t - 1] / base5 if base5 > 0 else None
                    zt60 = sum(
                        1 for k in range(max(1, t - 60), t)
                        if closes[k] / closes[k - 1] - 1.0 >= 0.098
                    )
                    ev = {
                        "code": code, "day": b["date"],
                        "fwd3": (closes[t + HOLD] / c - 1.0) * 100.0,
                        "pct": pct,
                        "profit": below / total * 100.0,
                        "vs_cost": (c / avg_cost - 1.0) * 100.0,
                        "conc90": (p95 - p5) / (p95 + p5) * 100.0 if p95 + p5 > 0 else None,
                        "peak_vs": (peak / c - 1.0) * 100.0,
                        "vr_prev": vr,
                        "pct_prev": pct_prev,
                        "vol_trend3": (sum(vols[t - 3:t]) / 3.0) / early if early > 0 else None,
                        "ret5": (c0 / closes[t - 6] - 1.0) * 100.0,
                        "ret20": ret20,
                        "dist_ma20": (c / ma20 - 1.0) * 100.0,
                        "turn_prev": bars[t - 1].get("turnover"),
                        "zt60": zt60,
                    }
                    ev.update({f"b_{k}": v for k, v in classify(ev).items()})
                    out.append(ev)
        tv = b.get("turnover")
        if tv is None:
            continue
        tr = min(1.0, max(0.0, float(tv) / 100.0))
        avg = ((b.get("open") or b["close"]) + b["close"] + b["high"] + b["low"]) / 4.0
        w = _tri_weights(b["low"], b["high"], avg, grid, lo_all, step)
        chips = [ch * (1.0 - tr) for ch in chips]
        for i, wi in w:
            chips[i] += tr * wi
    return out


UNIVERSE_FEATS = (
    ("pct", "当日涨跌%"), ("profit", "获利盘%"), ("vs_cost", "距平均成本%"),
    ("conc90", "筹码集中度(越大越散)"), ("peak_vs", "主峰相对现价%"),
    ("vr_prev", "昨量比"), ("pct_prev", "昨涨跌%"), ("vol_trend3", "3日量能趋势"),
    ("ret5", "5日涨幅"), ("ret20", "20日涨幅"), ("dist_ma20", "距MA20%"),
    ("turn_prev", "昨换手%"), ("zt60", "60日涨停数"),
)


def bucket_table(evs: list[dict[str, Any]], key: str, labels: dict[str, str]) -> None:
    """Excess 3-day return (vs same-day mean) and win-rate edge per bucket value."""
    day_mean: dict[str, float] = {}
    by: dict[str, list[float]] = {}
    for e in evs:
        by.setdefault(e["day"], []).append(e["fwd3"])
    day_mean = {d: sum(v) / len(v) for d, v in by.items()}
    day_win = {d: sum(1 for x in v if x > 0) / len(v) for d, v in by.items()}
    acc: dict[str, list[dict[str, Any]]] = {}
    for e in evs:
        if len(by[e["day"]]) < 5 or e.get(key) is None:
            continue
        acc.setdefault(str(e[key]), []).append(e)
    for val, sub in sorted(acc.items()):
        ex = [e["fwd3"] - day_mean[e["day"]] for e in sub]
        wn = [(1.0 if e["fwd3"] > 0 else 0.0) - day_win[e["day"]] for e in sub]
        sd = stdev(ex)
        t = mean(ex) / sd * math.sqrt(len(ex) / HOLD) if sd else None
        print(f"  {labels.get(val, val):<10}{len(sub):>7}{fmt(mean(ex), '+8.2f')}{fmt(100 * mean(wn), '+8.1f')}{fmt(t, '+7.1f')}")


def quintiles(evs: list[dict[str, Any]], key: str) -> list[float | None]:
    """Mean excess fwd3 per within-day quintile of ``key`` (Q1 low … Q5 high)."""
    by: dict[str, list[dict[str, Any]]] = {}
    for e in evs:
        if e.get(key) is not None:
            by.setdefault(e["day"], []).append(e)
    q: list[list[float]] = [[] for _ in range(5)]
    for rows in by.values():
        if len(rows) < 10:
            continue
        m = sum(r["fwd3"] for r in rows) / len(rows)
        rk = ranks01([float(r[key]) for r in rows])
        for r, x in zip(rows, rk):
            q[min(4, int((x + 0.5) * 5))].append(r["fwd3"] - m)
    return [mean(v) for v in q]


def part_a(universe: dict[str, list[dict[str, Any]]]) -> None:
    evs: list[dict[str, Any]] = []
    for code, bars in universe.items():
        if code.startswith(("1", "5")):
            continue
        evs.extend(stock_events(code, bars))
    days = sorted({e["day"] for e in evs})
    print(f"\n######## A. 扩样本：{len({e['code'] for e in evs})} 只票 · {len(evs)} 个回踩事件 · "
          f"{days[0] if days else '-'} ~ {days[-1] if days else '-'} ({len(days)} 天)")
    print("  事件 = 收盘在 MA20 上、20 日涨幅 >5%、当天 −4%～+1%；目标 = 之后 3 日收益；IC = 同日横截面秩相关")
    print(f"\n{'因子':<22}{'天':>5}{'IC均值':>8}{'t':>7}{'IC>0':>7}{'前半':>8}{'后半':>8}   Q1..Q5 同日超额%")
    for key, lab in UNIVERSE_FEATS:
        s = ic_summary(daily_ic(evs, lambda e, k=key: e.get(k), "fwd3", min_n=10))
        qs = quintiles(evs, key)
        print(
            f"{lab:<22}{s['n_days']:>5}{fmt(s['ic'], '+8.3f')}{fmt(s['t'], '+7.1f')}"
            f"{fmt(None if s['pos'] is None else 100 * s['pos'], '7.0f')}{fmt(s['ic_h1'], '+8.3f')}{fmt(s['ic_h2'], '+8.3f')}   "
            + " ".join(fmt(x, "+.2f") for x in qs)
        )
    print(f"\n  台面标签（同日超额3日收益 / 胜率差pp / t）{'':<4}{'n':>7}{'超额%':>8}{'胜率pp':>8}{'t':>7}")
    print(" 筹码位置 chip_pos"); bucket_table(evs, "b_chip_pos", {"high": "偏高", "ok": "正常"})
    print(" 昨日量能 vol1"); bucket_table(evs, "b_vol1", {"shrink": "昨缩量", "spike": "昨巨量", "normal": "正常"})
    print(" 量能趋势 vol3"); bucket_table(evs, "b_vol3", {"fade": "连缩量", "normal": "正常"})
    for e in evs:
        p = e["pct"]
        e["b_pct"] = "≤-3" if p <= -3 else ("-3~-1" if p <= -1 else ("-1~0" if p <= 0 else "0~1"))
        z = e["zt60"]
        e["b_zt"] = "0" if z == 0 else ("1-2" if z <= 2 else "≥3")
        vc = e["vs_cost"]
        e["b_vc"] = "<0" if vc < 0 else ("0-5" if vc <= 5 else ("5-15" if vc <= 15 else ">15"))
    print(" 当日涨跌"); bucket_table(evs, "b_pct", {})
    print(" 60日涨停数"); bucket_table(evs, "b_zt", {})
    print(" 距平均成本"); bucket_table(evs, "b_vc", {})


# ---------------------------------------------------------------- Part B

def minutes_of(ts: Any) -> float | None:
    s = str(ts or "")
    try:
        hh, mm = int(s[11:13]), int(s[14:16])
    except ValueError:
        return None
    m = hh * 60 + mm
    return float(m - 570 if m < 720 else m - 660)


def signal_features(r: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, float | None]:
    """Numeric features of one stored buy signal as of its signal time."""
    p, cand = r["payload"], r["cand"]
    cv = cand.get("cv") or {}
    before = [b for b in bars if b["date"] < r["trade_date"]]
    closes = [b["close"] for b in before]
    entry = r["entry"]
    minute = p.get("minute") if isinstance(p.get("minute"), dict) else {}
    ctx = p.get("context") if isinstance(p.get("context"), dict) else {}
    f: dict[str, float | None] = {
        "pct": af._f(p.get("pct")),
        "profit": af._f(cv.get("profit")),
        "vs_cost": af._f(cv.get("vs_cost")),
        "conc90": af._f(cv.get("conc90")),
        "peak_vs": af._f(cv.get("peak_vs")),
        "vr_prev": af._f(cv.get("vr_prev")),
        "vol_trend3": af._f(cv.get("vol_trend3")),
        "zt_ytd": af._f(cand.get("zt_ytd")),
        "ret5": (closes[-1] / closes[-6] - 1.0) * 100.0 if len(closes) >= 6 else None,
        "ret20": (closes[-1] / closes[-21] - 1.0) * 100.0 if len(closes) >= 21 else None,
        "dist_ma20": (entry / (sum(closes[-20:]) / 20.0) - 1.0) * 100.0 if len(closes) >= 20 and entry else None,
        "turn_prev": af._f(before[-1].get("turnover")) if before else None,
        "m_volr": af._f(minute.get("vol_ratio")),
        "m_pullback": af._f(minute.get("pullback_pct")),
        "sig_min": minutes_of(r.get("signaled_at")),
        "trend_up": 1.0 if p.get("trend_ok") else 0.0,
        "trend_down": 1.0 if p.get("trend_down") else 0.0,
        "board_match": 1.0 if p.get("board_match") else 0.0,
        "ready": 1.0 if r.get("ready") else 0.0,
        "ctx_high_vol": 1.0 if ctx.get("vol") == "high" else 0.0,
    }
    return f


SIGNAL_FEATS = (
    ("pct", "当日涨跌%"), ("profit", "获利盘%"), ("vs_cost", "距平均成本%"),
    ("conc90", "筹码集中度"), ("peak_vs", "主峰相对价%"), ("vr_prev", "昨量比"),
    ("vol_trend3", "3日量能趋势"), ("zt_ytd", "年内涨停数"), ("ret5", "5日涨幅"),
    ("ret20", "20日涨幅"), ("dist_ma20", "距MA20%"), ("turn_prev", "昨换手%"),
    ("m_volr", "分时量比"), ("m_pullback", "分时回撤%"), ("sig_min", "出信号分钟"),
    ("trend_up", "日线上升"), ("trend_down", "日线下降"), ("board_match", "贴合主线"),
    ("ready", "亮过可买"), ("ctx_high_vol", "高波动时段"),
)


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (small dense systems)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                k = m[r][c] / m[c][c]
                if k:
                    m[r] = [x - k * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def ridge(xs: list[list[float]], ys: list[float], lam: float) -> list[float]:
    p = len(xs[0])
    a = [[sum(x[i] * x[j] for x in xs) + (lam if i == j else 0.0) for j in range(p)] for i in range(p)]
    b = [sum(x[i] * y for x, y in zip(xs, ys)) for i in range(p)]
    return solve(a, b)


def rank_matrix(rows: list[dict[str, Any]], keys: list[str]) -> None:
    """Attach per-day rank-normalized features ``rk`` (missing → 0)."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["day"], []).append(r)
    for day_rows in by.values():
        for r in day_rows:
            r["rk"] = [0.0] * len(keys)
        for j, k in enumerate(keys):
            have = [r for r in day_rows if r["f"].get(k) is not None]
            if len(have) < 3:
                continue
            for r, x in zip(have, ranks01([float(r["f"][k]) for r in have])):
                r["rk"][j] = x


def eval_oos(rows: list[dict[str, Any]], key: str, days: list[str]) -> str:
    sub = [r for r in rows if r["day"] in days and r.get(key) is not None]
    rho = aps.spearman([r[key] for r in sub], [r["d3"] for r in sub])
    spreads, top1, rhos, better = [], [], [], 0
    by: dict[str, list[dict[str, Any]]] = {}
    for r in sub:
        by.setdefault(r["day"], []).append(r)
    for day_rows in by.values():
        if len(day_rows) < 6:
            continue
        s = sorted(day_rows, key=lambda r: -r[key])
        k = max(2, len(s) // 3)
        sp = mean([r["d3"] for r in s[:k]]) - mean([r["d3"] for r in s[-k:]])
        spreads.append(sp)
        better += sp > 0
        top1.append(s[0]["d3"] - mean([r["d3"] for r in s]))
        rr = aps.spearman([r[key] for r in s], [r["d3"] for r in s])
        if rr is not None:
            rhos.append(rr)
    return (f"整体秩相关 {fmt(rho)} · 当日IC均值 {fmt(mean(rhos))} · 前1/3−后1/3 {fmt(mean(spreads), '+.2f')}pp "
            f"({better}/{len(spreads)}天更好) · 最高分−均值 {fmt(mean(top1), '+.2f')}pp")


def part_b(rows: list[dict[str, Any]], cache: dict[str, Any]) -> list[dict[str, Any]]:
    empty = build_history_stats([])
    days = sorted({r["trade_date"] for r in rows})
    idx = {d: i for i, d in enumerate(days)}
    sig: list[dict[str, Any]] = []
    for r in rows:
        bars = (cache.get(r["code"]) or {}).get("bars") or []
        known = [h for h in rows if idx[h["trade_date"]] <= idx[r["trade_date"]] - LAG_DAYS]
        sig.append({
            "day": r["trade_date"], "d3": float(r["outcome_day3_pct"]),
            "f": signal_features(r, bars),
            "rule": score_pick(r["cand"], empty)["score"],
            "live": score_pick(r["cand"], build_history_stats(known))["score"],
            "src": r["signal_type"], "raw": r,
        })
    by: dict[str, list[dict[str, Any]]] = {}
    for s in sig:
        by.setdefault(s["day"], []).append(s)
    for s in sig:
        dm = mean([x["d3"] for x in by[s["day"]]])
        s["ex"] = max(-12.0, min(12.0, s["d3"] - dm))
    print(f"\n######## B. 真实买点：{len(sig)} 条 · {len(days)} 天（按天去掉整体涨跌后看相对强弱）")
    print(f"\n{'因子':<16}{'天':>4}{'IC均值':>8}{'t':>7}{'IC>0':>7}{'前半':>8}{'后半':>8}")
    for key, lab in SIGNAL_FEATS + (("rule", "现行规则分"), ("live", "现行分+历史")):
        get = (lambda s, k=key: s["f"].get(k)) if key not in ("rule", "live") else (lambda s, k=key: s[k])
        st = ic_summary(daily_ic(sig, get, "d3", min_n=6), every=1)
        print(
            f"{lab:<16}{st['n_days']:>4}{fmt(st['ic'], '+8.3f')}{fmt(st['t'], '+7.1f')}"
            f"{fmt(None if st['pos'] is None else 100 * st['pos'], '7.0f')}{fmt(st['ic_h1'], '+8.3f')}{fmt(st['ic_h2'], '+8.3f')}"
        )
    keys = [k for k, _ in SIGNAL_FEATS]
    rank_matrix(sig, keys)
    oos_days: list[str] = []
    for d in days:
        train = [s for s in sig if idx[s["day"]] <= idx[d] - LAG_DAYS]
        if len({s["day"] for s in train}) < 5:
            continue
        oos_days.append(d)
        test = [s for s in sig if s["day"] == d]
        for lam_name, lam in (("ridge_s", 5.0), ("ridge_l", 40.0)):
            w = ridge([s["rk"] for s in train], [s["ex"] for s in train], lam * len(keys))
            for s in test:
                s[lam_name] = sum(a * b for a, b in zip(w, s["rk"]))
        tr_ic = {k: mean(list(daily_ic(train, lambda s, k=k: s["f"].get(k), "d3", min_n=6).values())) or 0.0 for k in keys}
        for s in test:
            s["icw"] = sum(tr_ic[k] * s["rk"][j] for j, k in enumerate(keys))
    print(f"\n  前推（每天只用 ≥{LAG_DAYS} 天前已知结果训练）样本外 {len(oos_days)} 天：")
    for key, lab in (("rule", "现行规则分"), ("live", "现行分+历史"), ("ridge_s", "岭回归(弱约束)"),
                     ("ridge_l", "岭回归(强约束)"), ("icw", "IC加权组合")):
        print(f"  {lab:<14}{eval_oos(sig, key, oos_days)}")
    w_all = ridge([s["rk"] for s in sig], [s["ex"] for s in sig], 40.0 * len(keys))
    print("\n  全样本强约束岭回归系数（同日排名从最低到最高 → 超额3日收益 pp）：")
    for (k, lab), w in sorted(zip(SIGNAL_FEATS, w_all), key=lambda kv: -abs(kv[1])):
        print(f"    {lab:<14}{w:+.2f}")
    return sig


# ---------------------------------------------------------------- Part C

def part_c(rows: list[dict[str, Any]]) -> None:
    days = sorted({r["trade_date"] for r in rows})
    idx = {d: i for i, d in enumerate(days)}
    print("\n######## C. 历史微调：硬门槛+截断 vs 经验贝叶斯收缩（前推）")
    orig = (ps.PICK_HIST_MIN_N, ps.PICK_HIST_MIN_DAYS, ps.PICK_HIST_PP_TO_PTS, ps.PICK_HIST_MAX_ADJ)
    variants = {
        "现行 0.4/pp ±8 n≥8": (8, 5, 0.4, 8.0, None),
        "关掉历史微调": (10**9, 10**9, 0.0, 0.0, None),
        "收缩 k=20 0.4/pp ±8": (1, 1, 0.4, 8.0, 20.0),
        "收缩 k=40 0.4/pp ±8": (1, 1, 0.4, 8.0, 40.0),
        "收缩 k=40 0.6/pp ±10": (1, 1, 0.6, 10.0, 40.0),
    }
    orig_adj = ps._hist_adj
    res: dict[str, list[dict[str, Any]]] = {k: [] for k in variants}
    for name, (mn, md, pp, cap, k) in variants.items():
        ps.PICK_HIST_MIN_N, ps.PICK_HIST_MIN_DAYS, ps.PICK_HIST_PP_TO_PTS, ps.PICK_HIST_MAX_ADJ = mn, md, pp, cap
        if k is not None:
            def shrunk(dim: str, val: str | None, stats: dict[str, Any], _k: float = k, _pp: float = pp, _cap: float = cap):
                b = (stats.get("buckets") or {}).get(f"{dim}:{val}") if val else None
                base = stats.get("base_win3")
                if not b or base is None or b.get("win3") is None:
                    return 0.0, ""
                wt = b["n"] / (b["n"] + _k)
                adj = max(-_cap, min(_cap, (b["win3"] - base) * wt * _pp))
                return round(adj, 1), f"同类 {b['n']} 条×权重{wt:.2f}"
            ps._hist_adj = shrunk
        else:
            ps._hist_adj = orig_adj
        for r in rows:
            known = [h for h in rows if idx[h["trade_date"]] <= idx[r["trade_date"]] - LAG_DAYS]
            if len({h["trade_date"] for h in known}) < 5:
                continue
            res[name].append({"day": r["trade_date"], "d3": float(r["outcome_day3_pct"]),
                              "s": score_pick(r["cand"], build_history_stats(known))["score"]})
    ps._hist_adj = orig_adj
    ps.PICK_HIST_MIN_N, ps.PICK_HIST_MIN_DAYS, ps.PICK_HIST_PP_TO_PTS, ps.PICK_HIST_MAX_ADJ = orig
    for name, sc in res.items():
        dd = sorted({s["day"] for s in sc})
        print(f"  {name:<22}{eval_oos(sc, 's', dd)}")


def part_d(rows: list[dict[str, Any]]) -> None:
    """Score candidate formula variants on all days, both halves and the walk-forward days."""
    days = sorted({r["trade_date"] for r in rows})
    idx = {d: i for i, d in enumerate(days)}
    names = ("PICK_HIST_PP_TO_PTS", "PICK_CV_CHIP_HIGH_PTS", "PICK_CV_VOL_FADE_PTS",
             "PICK_CV_IVOL_HIGH_PTS", "PICK_CV_IVOL_LOW_PTS", "PICK_PM_WEAK_PTS")
    orig = {n: getattr(ps, n) for n in names}
    # (config patch, extra points when daily trend is up, drop the "no limit-up this year" −4)
    variants: dict[str, tuple[dict[str, float], float, bool]] = {
        "现行": ({}, 0.0, False),
        "无涨停不扣": ({}, 0.0, True),
        "无涨停不扣+连缩量−3": ({"PICK_CV_VOL_FADE_PTS": -3.0}, 0.0, True),
        "无涨停不扣+连缩量−3+午后−3": ({"PICK_CV_VOL_FADE_PTS": -3.0, "PICK_PM_WEAK_PTS": -3.0}, 0.0, True),
        "筹码偏高/连缩量/股性躁都−5": (
            {"PICK_CV_CHIP_HIGH_PTS": -5.0, "PICK_CV_VOL_FADE_PTS": -5.0, "PICK_CV_IVOL_HIGH_PTS": -5.0}, 0.0, False),
        "走势稳不加分": ({"PICK_CV_IVOL_LOW_PTS": 0.0}, 0.0, False),
    }
    print("\n######## D. 候选公式（样本内，警惕过拟合）")
    half = days[len(days) // 2]
    for name, (patch, trend_pts, drop_zt) in variants.items():
        for n in names:
            setattr(ps, n, patch.get(n, orig[n]))
        sc = []
        for r in rows:
            known = [h for h in rows if idx[h["trade_date"]] <= idx[r["trade_date"]] - LAG_DAYS]
            res = score_pick(r["cand"], build_history_stats(known))
            s = float(res["score"])
            if drop_zt:
                s -= sum(float(f["points"]) for f in res["factors"] if f["key"] == "zt")
            if r["payload"].get("trend_ok"):
                s += trend_pts
            sc.append({"day": r["trade_date"], "d3": float(r["outcome_day3_pct"]), "s": s})
        print(f"  [{name}]")
        print(f"    全部     {eval_oos(sc, 's', days)}")
        print(f"    前半     {eval_oos(sc, 's', [d for d in days if d < half])}")
        print(f"    后半     {eval_oos(sc, 's', [d for d in days if d >= half])}")
    for n in names:
        setattr(ps, n, orig[n])


def part_h(rows: list[dict[str, Any]], cache: dict[str, Any]) -> None:
    """Position-vs-plan factor: accuracy without it, and a proxy reading from the last-seen price.

    The live price at signal time is not stored; ``signals.last`` is the price at the
    signal's last refresh, so the position read here is a proxy, not what the desk saw first.
    """
    import sqlite3

    from market_desk.pick_score import _position_status
    from market_desk.review import enrich_signals_with_live_marks

    legacy_pts = {"stop": -25.0, "chase": -20.0, "band": 10.0, "below": 8.0, "near": 0.0}

    def _position_factor(item: dict[str, Any]) -> dict[str, Any] | None:
        """Old (pre 2026-09-30) position points, for comparison only."""
        st = _position_status(item)
        if not st:
            return None
        if st["kind"] == "above":
            pts = -min(15.0, 4.0 + 2.0 * float(item.get("above_plan_pct") or 0.0))
        else:
            pts = legacy_pts[st["kind"]]
        return {"points": pts, "detail": st["detail"]}

    conn = sqlite3.connect(f"file:{aps.DB}?mode=ro", uri=True)
    last_by = {int(i): af._f(v) for i, v in conn.execute("SELECT id, last FROM signals")}
    conn.close()
    empty = build_history_stats([])
    out = []
    for r in rows:
        bars = (cache.get(r["code"]) or {}).get("bars") or []
        day0 = next((b for b in bars if b["date"] == r["trade_date"]), None)
        plan = af._f(r["payload"].get("plan_price")) or af._f(r["price"])
        base = score_pick(r["cand"], empty)
        last = last_by.get(int(r["id"]))
        pos = None
        if last and plan:
            item = enrich_signals_with_live_marks(
                [{"code": r["code"], "signal_type": r["signal_type"], "price": plan, "payload": r["payload"]}],
                {r["code"]: {"price": last}},
            )[0]
            pos = _position_factor(item)
        pts = float(pos["points"]) if pos else 0.0
        out.append({
            "day": r["trade_date"], "d3": float(r["outcome_day3_pct"]),
            "no_pos": float(base["score"]),
            "with_pos": max(0.0, min(100.0, float(base["score"]) + pts)),
            "pos_pts": pts if pos else None,
            "pos_label": (pos or {}).get("detail", "无"),
            "touched": bool(day0 and day0.get("low") is not None and plan and day0["low"] <= plan),
        })
    print("\n######## H. 买点位置")
    print("  [不计买点位置（此前所有离线回测都是这个口径）] 各档三日胜率：")
    aps.band_table([{**s, "d1": None} for s in out], "no_pos")
    print(f"  {eval_oos(out, 'no_pos', sorted({s['day'] for s in out}))}")

    def bucket(p: float | None) -> str:
        if p is None:
            return "无数据"
        if p <= -20:
            return "过不追/到止损(≤−20)"
        if p < 0:
            return "高于计划价(−4~−15)"
        if p == 0:
            return "略高于计划价(0)"
        return "回踩带/低于计划价(+8/+10)"

    print("\n  [近似：按信号最后一次出现时的价格判断位置]")
    print(f"  {'位置':<24}{'n':>5}{'当天回踩到计划价':>12}{'三日胜率':>9}{'回踩到的三日胜率':>12}{'均d3%':>8}")
    acc: dict[str, list[dict[str, Any]]] = {}
    for s in out:
        acc.setdefault(bucket(s["pos_pts"]), []).append(s)
    for k, sub in sorted(acc.items()):
        hit = [s for s in sub if s["touched"]]
        w_all = af.day_balanced(sub, lambda s: s["d3"] > 0)
        w_hit = af.day_balanced(hit, lambda s: s["d3"] > 0) if hit else None
        print(f"  {k:<24}{len(sub):>5}{100 * len(hit) / len(sub):>11.0f}%{fmt(w_all, '8.1f')}%{fmt(w_hit, '11.1f')}%{mean([s['d3'] for s in sub]):>8.2f}")
    print("\n  [计入近似买点位置] 各档三日胜率：")
    aps.band_table([{**s, "d1": None} for s in out], "with_pos")
    print(f"  {eval_oos(out, 'with_pos', sorted({s['day'] for s in out}))}")


def part_r(rows: list[dict[str, Any]]) -> None:
    """Ready-gate audit: last-refresh ``ready`` vs ``payload.ever_ready``, against same-day peers."""
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(r["trade_date"], []).append(r)
    day_m = {d: mean([float(x["outcome_day3_pct"]) for x in v]) for d, v in by_day.items()}
    day_w = {d: sum(1 for x in v if float(x["outcome_day3_pct"]) > 0) / len(v) for d, v in by_day.items()}

    def line(lab: str, sub: list[dict[str, Any]]) -> None:
        if not sub:
            print(f"  {lab:<30}{0:>5}")
            return
        d3 = [float(r["outcome_day3_pct"]) for r in sub]
        d1 = [af._f(r["outcome_day1_pct"]) for r in sub if af._f(r["outcome_day1_pct"]) is not None]
        ex = mean([float(r["outcome_day3_pct"]) - day_m[r["trade_date"]] for r in sub])
        wex = mean([(1.0 if float(r["outcome_day3_pct"]) > 0 else 0.0) - day_w[r["trade_date"]] for r in sub])
        w3 = af.day_balanced([{"day": r["trade_date"], "d3": float(r["outcome_day3_pct"])} for r in sub], lambda s: s["d3"] > 0)
        print(f"  {lab:<30}{len(sub):>5}{len({r['trade_date'] for r in sub}):>4}{w3:>8.1f}{100 * wex:>+9.1f}"
              f"{mean(d3):>8.2f}{ex:>+8.2f}{(mean(d1) or 0):>8.2f}")

    print("\n######## R. 可买入（ready）")
    print(f"  {'口径':<30}{'n':>5}{'天':>4}{'胜率3':>8}{'同日胜率差':>9}{'均d3%':>8}{'同日超额':>8}{'均d1%':>8}")
    ready_last = [r for r in rows if int(r.get("ready") or 0)]
    ever = [r for r in rows if r["payload"].get("ever_ready") or r["payload"].get("first_ready_at")]
    line("最后一次刷新仍可买（打分口径）", ready_last)
    line("亮过可买（ever_ready）", ever)
    line("亮过但后来熄灭", [r for r in ever if not int(r.get("ready") or 0)])
    line("从未亮过", [r for r in rows if r not in ever and not int(r.get("ready") or 0)])
    line("全部", rows)
    print("\n  按信号类型（亮过可买）：")
    for st in sorted({r["signal_type"] for r in ever}):
        line(f"  {st}", [r for r in ever if r["signal_type"] == st])
    print("\n  按日期（亮过可买 vs 当天全部）：")
    for d in sorted({r["trade_date"] for r in ever}):
        sub = [r for r in ever if r["trade_date"] == d]
        wins = sum(1 for r in sub if float(r["outcome_day3_pct"]) > 0)
        print(f"    {d}  亮过 {len(sub):>3} 只 胜 {wins:>2} · 均d3 {mean([float(r['outcome_day3_pct']) for r in sub]):+6.2f}%"
              f" · 当天全部 {len(by_day[d]):>3} 只 均d3 {day_m[d]:+6.2f}% 胜率 {100 * day_w[d]:.0f}%")
    print("\n  亮过可买的首次亮起时刻分布（按小时）：")
    hours: dict[str, list[dict[str, Any]]] = {}
    for r in ever:
        at = str(r["payload"].get("first_ready_at") or r.get("signaled_at") or "")
        hours.setdefault(at[11:13] or "?", []).append(r)
    for h, sub in sorted(hours.items()):
        line(f"  {h} 点", sub)


def part_r2(rows: list[dict[str, Any]], cache: dict[str, Any]) -> None:
    """Ready deep-dive: buy-now vs wait-price entry, and each gate reason vs same-day peers."""
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(r["trade_date"], []).append(r)
    day_m = {d: mean([float(x["outcome_day3_pct"]) for x in v]) for d, v in by_day.items()}
    day_w = {d: sum(1 for x in v if float(x["outcome_day3_pct"]) > 0) / len(v) for d, v in by_day.items()}

    def stat(sub: list[dict[str, Any]]) -> str:
        ex = mean([float(r["outcome_day3_pct"]) - day_m[r["trade_date"]] for r in sub])
        wex = mean([(1.0 if float(r["outcome_day3_pct"]) > 0 else 0.0) - day_w[r["trade_date"]] for r in sub])
        return f"{len(sub):>5}{len({r['trade_date'] for r in sub}):>4}{100 * wex:>+9.1f}{ex:>+9.2f}"

    ever = [r for r in rows if r["payload"].get("ever_ready") or r["payload"].get("first_ready_at")]
    print("\n######## R2. 可买入深挖")
    print("  [A] 亮过可买的票：按计划价（亮起时现价）买 vs 当天回踩到等回踩价再买")
    got = []
    for r in ever:
        p = r["payload"]
        plan = af._f(p.get("plan_price")) or af._f(r["price"])
        wait = af._f(p.get("wait_price"))
        bars = (cache.get(r["code"]) or {}).get("bars") or []
        day0 = next((b for b in bars if b["date"] == r["trade_date"]), None)
        if not (plan and wait and day0 and day0.get("low")):
            continue
        close3 = plan * (1.0 + float(r["outcome_day3_pct"]) / 100.0)
        touched = day0["low"] <= wait
        got.append({
            "gap": (plan / wait - 1.0) * 100.0, "touched": touched,
            "d3_plan": float(r["outcome_day3_pct"]),
            "d3_wait": (close3 / wait - 1.0) * 100.0 if touched else None,
        })
    if got:
        t = [g for g in got if g["touched"]]
        print(f"    {len(got)} 只有等回踩价：计划价平均高于等回踩价 {mean([g['gap'] for g in got]):+.2f}%；"
              f"当天回踩到等回踩价 {len(t)}/{len(got)}")
        if t:
            print(f"    回踩到的那些：按计划价买均d3 {mean([g['d3_plan'] for g in t]):+.2f}%（胜 {sum(g['d3_plan'] > 0 for g in t)}/{len(t)}）"
                  f" · 按等回踩价买均d3 {mean([g['d3_wait'] for g in t]):+.2f}%（胜 {sum(g['d3_wait'] > 0 for g in t)}/{len(t)}）")
    same = [r for r in rows if af._f(r["payload"].get("plan_price")) and af._f(r["payload"].get("wait_price"))]
    eq = [r for r in same if abs(af._f(r["payload"]["plan_price"]) - af._f(r["payload"]["wait_price"])) < 1e-6]
    print(f"    全部买点里计划价=等回踩价（盯回踩口径）的 {len(eq)}/{len(same)} 条")

    print("\n  [B] 每个闸门/软条件：被它卡过的买点 vs 同日其他买点（后续表现好 = 这条可能卡错了）")
    print(f"    {'条件':<28}{'n':>5}{'天':>4}{'同日胜率差':>9}{'同日超额':>9}")
    acc: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        p = r["payload"]
        reasons = set()
        for key, tag in (("confirm_fail_hist", "闸门"), ("confirm_soft", "软"), ("final_fail", "最终")):
            for x in p.get(key) or []:
                reasons.add(f"{tag}:{str(x).strip()[:14]}")
        m = p.get("minute") if isinstance(p.get("minute"), dict) else {}
        for x in m.get("fails") or []:
            reasons.add(f"分时:{str(x).strip()[:14]}")
        for flag, tag in (("block_ready", "block_ready"), ("fly_warn", "fly_warn"), ("size_cap_block", "仓位上限"),
                          ("ready_relaxed", "放宽ready"), ("near_entry", "near_entry"), ("probe_ok", "probe_ok")):
            if p.get(flag):
                reasons.add(f"标记:{tag}")
        for x in reasons:
            acc.setdefault(x, []).append(r)
    for k, sub in sorted(acc.items(), key=lambda kv: -len(kv[1])):
        if len(sub) >= 8:
            print(f"    {k:<28}{stat(sub)}")
    print(f"    {'亮过可买':<28}{stat(ever)}")


def main() -> None:
    refresh = "--refresh" in sys.argv
    rows = [r for r in aps.load_rows() if r["entry"]]
    codes = sorted({r["code"] for r in rows})
    cache = asyncio.run(af.fetch_all(codes, refresh=refresh, need_flow=False))
    for r in rows:
        r["cand"] = aps.candidate(r, (cache.get(r["code"]) or {}).get("bars") or [])
    if "--pos" in sys.argv:
        part_h(rows, cache)
        return
    if "--ready" in sys.argv:
        part_r(rows)
        part_r2(rows, cache)
        from market_desk.review import build_ready_monitor

        mon = build_ready_monitor(rows)
        print(f"\n  [复盘看板·可买入监控] {mon['note']}")
        for d in mon["by_day"]:
            print(f"    {d}")
        return
    universe = asyncio.run(fetch_universe(codes, refresh))
    part_a(universe)
    part_b(rows, cache)
    part_c(rows)
    part_d(rows)


if __name__ == "__main__":
    main()
