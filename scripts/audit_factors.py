"""Offline audit: do daily volume, stock main-force flow and chip peaks separate buy outcomes?

Reads scored buy signals from ``data/desk.db`` (read-only), fetches East Money
daily klines (with turnover) and stock fund-flow day lines, then buckets each
signal by factors computed as of the previous close (no look-ahead). Factors
marked "ref" use the signal day's full-day print and are for reference only.

Usage: ``python scripts/audit_factors.py [--refresh]``
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from market_desk.config import EASTMONEY_UT, HTTP_HEADERS  # noqa: E402
from research_db import research_db_path  # noqa: E402

DB = research_db_path(ROOT / "data" / "desk.db")
CACHE = Path(tempfile.gettempdir()) / "md-audit-factors-cache.json"
CHIP_WINDOW = 120
CHIP_BINS = 200
EDGE_PP = 8.0
MIN_N = 20
MIN_DAYS = 5


def _secid(code: str) -> str:
    """Map a six-digit code to an East Money secid."""
    return f"1.{code}" if code.startswith(("5", "6", "9")) else f"0.{code}"


def _f(v: Any) -> float | None:
    """Parse a float, returning None for blanks and dashes."""
    try:
        if v in (None, "", "-"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def load_signals() -> list[dict[str, Any]]:
    """Load scored buy-family signals with an entry price and 3-day outcome."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, trade_date, code, signal_type, signaled_at, price, fill_price,
               payload, outcome_day1_pct, outcome_day3_pct
        FROM signals
        WHERE signal_type LIKE 'buy%' AND outcome_day3_pct IS NOT NULL
        """
    ).fetchall()
    conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except Exception:
            payload = {}
        entry = _f(r["fill_price"]) or _f(payload.get("plan_price")) or _f(r["price"])
        if not entry:
            continue
        out.append(
            {
                "id": r["id"],
                "day": str(r["trade_date"])[:10],
                "code": str(r["code"]).zfill(6),
                "type": r["signal_type"],
                "entry": entry,
                "d1": _f(r["outcome_day1_pct"]),
                "d3": float(r["outcome_day3_pct"]),
            }
        )
    return out


async def _get_json(client: httpx.AsyncClient, hosts: tuple[str, ...], path: str) -> dict[str, Any] | None:
    """GET the first host that answers with a data payload."""
    for host in hosts:
        try:
            resp = await client.get(f"https://{host}{path}", headers=HTTP_HEADERS, timeout=8.0)
            resp.raise_for_status()
            data = resp.json().get("data")
            if data:
                return data
        except Exception:
            continue
    return None


async def fetch_klines_tencent(client: httpx.AsyncClient, code: str) -> list[dict[str, Any]]:
    """Fetch ~220 unadjusted daily bars with turnover (percent) from Tencent newfqkline."""
    sym = ("sh" if code.startswith(("5", "6", "9")) else "sz") + code
    url = f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={sym},day,,,220,"
    try:
        resp = await client.get(url, headers={"User-Agent": HTTP_HEADERS["User-Agent"]}, timeout=8.0)
        rows = ((resp.json().get("data") or {}).get(sym) or {}).get("day") or []
    except Exception:
        return []
    bars: list[dict[str, Any]] = []
    prev: float | None = None
    for p in rows:
        if len(p) < 8:
            continue
        close = _f(p[2])
        if close is None:
            continue
        bars.append(
            {
                "date": str(p[0]),
                "open": _f(p[1]),
                "close": close,
                "high": _f(p[3]),
                "low": _f(p[4]),
                "volume": _f(p[5]),
                "pct": None if not prev else (close / prev - 1.0) * 100.0,
                "turnover": _f(p[7]),
            }
        )
        prev = close
    return bars


async def fetch_klines(client: httpx.AsyncClient, code: str) -> list[dict[str, Any]]:
    """Fetch ~220 unadjusted daily bars with turnover; Tencent first, East Money fallback."""
    bars = await fetch_klines_tencent(client, code)
    if bars:
        return bars
    path = (
        "/api/qt/stock/kline/get"
        f"?secid={_secid(code)}&ut={EASTMONEY_UT}"
        "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        "&klt=101&fqt=0&end=20500101&lmt=220"
    )
    data = await _get_json(client, ("push2his.eastmoney.com", "push2delay.eastmoney.com"), path)
    bars: list[dict[str, Any]] = []
    for line in (data or {}).get("klines") or []:
        p = str(line).split(",")
        if len(p) < 11:
            continue
        bars.append(
            {
                "date": p[0],
                "open": _f(p[1]),
                "close": _f(p[2]),
                "high": _f(p[3]),
                "low": _f(p[4]),
                "volume": _f(p[5]),
                "pct": _f(p[8]),
                "turnover": _f(p[10]),
            }
        )
    return bars


async def fetch_flow(client: httpx.AsyncClient, code: str) -> dict[str, dict[str, float | None]]:
    """Fetch stock fund-flow day lines: date -> main net amount / main net pct."""
    path = (
        "/api/qt/stock/fflow/daykline/get"
        f"?lmt=0&klt=101&secid={_secid(code)}&ut={EASTMONEY_UT}"
        "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
    )
    data = await _get_json(client, ("push2his.eastmoney.com", "push2.eastmoney.com"), path)
    out: dict[str, dict[str, float | None]] = {}
    for line in (data or {}).get("klines") or []:
        p = str(line).split(",")
        if len(p) < 7:
            continue
        out[p[0]] = {"main": _f(p[1]), "main_pct": _f(p[6])}
    return out


async def fetch_all(codes: list[str], refresh: bool, need_flow: bool = True) -> dict[str, Any]:
    """Fetch klines (+ flow when ``need_flow``) per code, reusing a temp-dir cache."""
    cache: dict[str, Any] = {}
    if CACHE.exists() and not refresh:
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    todo = [
        c for c in codes
        if not (cache.get(c) or {}).get("bars") or (need_flow and not (cache.get(c) or {}).get("flow"))
    ]
    sem = asyncio.Semaphore(3)

    async def one(client: httpx.AsyncClient, code: str) -> None:
        async with sem:
            slot = cache.setdefault(code, {})
            for attempt in range(3):
                if not slot.get("bars"):
                    slot["bars"] = await fetch_klines(client, code)
                if need_flow and not slot.get("flow"):
                    slot["flow"] = await fetch_flow(client, code)
                if slot.get("bars") and (slot.get("flow") or not need_flow):
                    break
                await asyncio.sleep(1.5 * (attempt + 1))

    if todo:
        print(f"fetching {len(todo)} codes ...", flush=True)
        async with httpx.AsyncClient() as client:
            await asyncio.gather(*(one(client, c) for c in todo))
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


def chip_profile(bars: list[dict[str, Any]], price: float) -> dict[str, float] | None:
    """Estimate a turnover-decayed triangular chip distribution and read it at ``price``."""
    win = [b for b in bars if b.get("high") and b.get("low") and b.get("turnover") is not None]
    if len(win) < 60:
        return None
    lo_all = min(float(b["low"]) for b in win)
    hi_all = max(float(b["high"]) for b in win)
    if hi_all <= lo_all:
        return None
    step = (hi_all - lo_all) / (CHIP_BINS - 1)
    grid = [lo_all + i * step for i in range(CHIP_BINS)]
    chips = [0.0] * CHIP_BINS
    for b in win:
        t = min(1.0, max(0.0, float(b["turnover"]) / 100.0))
        lo, hi = float(b["low"]), float(b["high"])
        avg = (float(b["open"] or b["close"]) + float(b["close"]) + hi + lo) / 4.0
        i0 = int((lo - lo_all) / step)
        i1 = int(round((hi - lo_all) / step))
        w = [0.0] * CHIP_BINS
        if i1 <= i0:
            w[max(0, min(CHIP_BINS - 1, i0))] = 1.0
        else:
            for i in range(i0, i1 + 1):
                p = grid[i]
                if p <= avg:
                    w[i] = (p - lo) / (avg - lo) if avg > lo else 1.0
                else:
                    w[i] = (hi - p) / (hi - avg) if hi > avg else 1.0
                w[i] = max(w[i], 0.0)
        s = sum(w) or 1.0
        chips = [c * (1.0 - t) + t * wi / s for c, wi in zip(chips, w)]
    total = sum(chips)
    if total <= 0:
        return None
    below = sum(c for g, c in zip(grid, chips) if g <= price)
    avg_cost = sum(g * c for g, c in zip(grid, chips)) / total
    cum, p5, p95 = 0.0, grid[0], grid[-1]
    for g, c in zip(grid, chips):
        prev = cum
        cum += c / total
        if prev < 0.05 <= cum:
            p5 = g
        if prev < 0.95 <= cum:
            p95 = g
    smooth = [sum(chips[max(0, i - 2): i + 3]) for i in range(CHIP_BINS)]
    peak = grid[max(range(CHIP_BINS), key=lambda i: smooth[i])]
    return {
        "profit": below / total * 100.0,
        "vs_cost": (price / avg_cost - 1.0) * 100.0,
        "conc90": (p95 - p5) / (p95 + p5) * 100.0 if p95 + p5 > 0 else 0.0,
        "peak_vs": (peak / price - 1.0) * 100.0,
    }


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def features(sig: dict[str, Any], blob: dict[str, Any]) -> dict[str, str]:
    """Bucket one signal by volume / flow / chip factors (prev close unless 'ref')."""
    bars = blob.get("bars") or []
    dates = [b["date"] for b in bars]
    if sig["day"] not in dates:
        return {}
    t = dates.index(sig["day"])
    if t < 11:
        return {}
    vol = [b.get("volume") or 0.0 for b in bars]
    out: dict[str, str] = {}

    def vr_bucket(r: float) -> str:
        return "<0.7缩" if r < 0.7 else ("0.7-1.3平" if r < 1.3 else ("1.3-2放" if r < 2.0 else "≥2巨"))

    base5 = _mean(vol[t - 6: t - 1])
    if base5:
        vr = vol[t - 1] / base5
        out["vol_prev"] = vr_bucket(vr)
        pct_prev = bars[t - 1].get("pct") or 0.0
        if pct_prev < 0 and vr < 0.8:
            out["pv_prev"] = "缩量跌"
        elif pct_prev < 0 and vr > 1.3:
            out["pv_prev"] = "放量跌"
        elif pct_prev > 0 and vr > 1.3:
            out["pv_prev"] = "放量涨"
        elif pct_prev > 0 and vr < 0.8:
            out["pv_prev"] = "缩量涨"
        else:
            out["pv_prev"] = "其他"
    early = _mean(vol[t - 10: t - 3])
    late = _mean(vol[t - 3: t])
    if early and late is not None:
        r = late / early
        out["vol_trend3"] = "<0.8连缩" if r < 0.8 else ("0.8-1.25" if r <= 1.25 else ">1.25连放")
    base_ref = _mean(vol[t - 5: t])
    if base_ref:
        out["ref_vol_day"] = vr_bucket(vol[t] / base_ref)

    flow = blob.get("flow") or {}
    prev = flow.get(dates[t - 1]) or {}
    if prev.get("main_pct") is not None:
        p = float(prev["main_pct"])
        out["flow_prev"] = "<-5流出" if p < -5 else ("-5~0" if p < 0 else ("0~5" if p < 5 else "≥5流入"))
    last3 = [flow.get(d, {}).get("main_pct") for d in dates[t - 3: t]]
    if all(x is not None for x in last3):
        m = sum(last3) / 3.0
        out["flow_3d"] = "<-3流出" if m < -3 else ("-3~0" if m < 0 else ("0~3" if m < 3 else "≥3流入"))
        pos = sum(1 for x in last3 if x > 0)
        out["flow_3d_days"] = f"{pos}/3天净流入"
    day = flow.get(sig["day"]) or {}
    if day.get("main_pct") is not None:
        p = float(day["main_pct"])
        out["ref_flow_day"] = "<-5流出" if p < -5 else ("-5~0" if p < 0 else ("0~5" if p < 5 else "≥5流入"))

    chip = chip_profile(bars[max(0, t - CHIP_WINDOW): t], sig["entry"])
    if chip:
        pr = chip["profit"]
        out["chip_profit"] = "<20深套" if pr < 20 else ("20-50" if pr < 50 else ("50-80" if pr < 80 else "≥80高获利"))
        vc = chip["vs_cost"]
        out["chip_vs_cost"] = "<-5低于成本" if vc < -5 else ("-5~5成本附近" if vc <= 5 else ("5~15" if vc <= 15 else ">15远高于成本"))
        pv = chip["peak_vs"]
        out["chip_peak"] = "主峰在上方>3%" if pv > 3 else ("主峰附近±3%" if pv >= -3 else "主峰在下方")
        c = chip["conc90"]
        out["chip_conc"] = "<10集中" if c < 10 else ("10-20" if c < 20 else "≥20分散")
    return out


def day_balanced(rows: list[dict[str, Any]], pred: Callable[[dict[str, Any]], bool]) -> float | None:
    """Mean of per-day win rates for rows matching ``pred``."""
    by: dict[str, list[int]] = {}
    for r in rows:
        s = by.setdefault(r["day"], [0, 0])
        s[0] += 1
        s[1] += 1 if pred(r) else 0
    rates = [w / n for n, w in by.values() if n]
    return 100.0 * sum(rates) / len(rates) if rates else None


FACTOR_LABELS = {
    "vol_prev": "前一日量比(对前5日均量)",
    "pv_prev": "前一日量价组合",
    "vol_trend3": "近3日量能趋势(对再前7日)",
    "ref_vol_day": "参考·信号当日全天量比(含未来)",
    "flow_prev": "前一日主力净占比%",
    "flow_3d": "前3日主力净占比均值%",
    "flow_3d_days": "前3日主力净流入天数",
    "ref_flow_day": "参考·信号当日主力净占比(含未来)",
    "chip_profit": "筹码获利盘%(按入场价)",
    "chip_vs_cost": "入场价相对平均成本%",
    "chip_peak": "主峰相对入场价",
    "chip_conc": "90%筹码集中度",
}


def report(rows: list[dict[str, Any]]) -> None:
    """Print per-factor bucket tables and flag buckets with a material edge."""
    win = lambda r: r["d3"] > 0  # noqa: E731
    base = day_balanced(rows, win)
    print(f"\n样本 {len(rows)} 条 · {len({r['day'] for r in rows})} 天 · 基准三日胜率(按天均衡) {base:.1f}%")
    flagged: list[str] = []
    for key, label in FACTOR_LABELS.items():
        have = [r for r in rows if key in r["f"]]
        if not have:
            continue
        print(f"\n== {label} · 覆盖 {len(have)}/{len(rows)} ==")
        print(f"{'分桶':<14}{'n':>5}{'天':>4}{'胜率3':>8}{'差pp':>7}{'均d3%':>8}{'均d1%':>8}")
        for b in sorted({r["f"][key] for r in have}):
            sub = [r for r in have if r["f"][key] == b]
            days = len({r["day"] for r in sub})
            w3 = day_balanced(sub, win)
            edge = (w3 - base) if (w3 is not None and base is not None) else None
            d3 = _mean([r["d3"] for r in sub])
            d1 = _mean([r["d1"] for r in sub])
            mark = ""
            if edge is not None and abs(edge) >= EDGE_PP and len(sub) >= MIN_N and days >= MIN_DAYS:
                mark = " ◀"
                flagged.append(f"{label} · {b}: {edge:+.1f}pp (n={len(sub)}, {days}天)")
            print(
                f"{b:<14}{len(sub):>5}{days:>4}{w3:>8.1f}{edge:>+7.1f}"
                f"{(d3 or 0):>8.2f}{(d1 if d1 is not None else 0):>8.2f}{mark}"
            )
    print(f"\n== 有区分度的分桶（|差|≥{EDGE_PP:.0f}pp, n≥{MIN_N}, 天≥{MIN_DAYS}） ==")
    for line in flagged or ["（无）"]:
        print(" ", line)


def main() -> None:
    sigs = load_signals()
    codes = sorted({s["code"] for s in sigs})
    cache = asyncio.run(fetch_all(codes, refresh="--refresh" in sys.argv))
    ok_k = sum(1 for c in codes if (cache.get(c) or {}).get("bars"))
    ok_f = sum(1 for c in codes if (cache.get(c) or {}).get("flow"))
    print(f"codes {len(codes)} · klines ok {ok_k} · flow ok {ok_f}")
    rows = []
    for s in sigs:
        f = features(s, cache.get(s["code"]) or {})
        if f:
            rows.append({**s, "f": f})
    report(rows)


if __name__ == "__main__":
    main()
