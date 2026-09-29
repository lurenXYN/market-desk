"""Chip-peak and daily-volume context for buy candidates.

Everything is computed from daily bars *before* the signal day (no look-ahead),
so one fetch per code per trade date is enough. Chips are a turnover-decayed
triangular estimate, not real holdings. Soft context only — never gates a buy.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from market_desk.config import (
    CV_CHIP_BINS,
    CV_CHIP_MIN_BARS,
    CV_CHIP_WINDOW,
    CV_FETCH_CONCURRENCY,
    HTTP_HEADERS,
)
from market_desk.numbers import num
from market_desk.tencent import tencent_symbol

log = logging.getLogger(__name__)

# code -> (trade_date, bars before that date)
_BARS_CACHE: dict[str, tuple[str, list[dict[str, Any]]]] = {}
_BARS_CACHE_MAX = 800


async def fetch_bars_with_turnover(
    client: httpx.AsyncClient, code: str, limit: int = CV_CHIP_WINDOW + 10
) -> list[dict[str, Any]]:
    """Fetch forward-adjusted daily bars with turnover (%) from Tencent newfqkline."""
    sym = tencent_symbol(code)
    url = (
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        f"?param={sym},day,,,{int(limit)},qfq"
    )
    try:
        resp = await client.get(
            url, headers={**HTTP_HEADERS, "Referer": "https://gu.qq.com/"}, timeout=8.0
        )
        resp.raise_for_status()
        node = ((resp.json().get("data") or {}).get(sym)) or {}
    except Exception as exc:
        log.debug("cv bars %s failed: %r", code, exc)
        return []
    rows = node.get("qfqday") or node.get("day") or []
    out: list[dict[str, Any]] = []
    prev: float | None = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 8:
            continue
        close = num(row[2])
        if close is None:
            continue
        out.append(
            {
                "date": str(row[0])[:10],
                "open": num(row[1]),
                "close": float(close),
                "high": num(row[3]),
                "low": num(row[4]),
                "volume": num(row[5]),
                "turnover": num(row[7]),
                "pct": None if not prev else (float(close) / prev - 1.0) * 100.0,
            }
        )
        prev = float(close)
    return out


async def bars_before_many(
    client: httpx.AsyncClient, codes: list[str], trade_date: str
) -> dict[str, list[dict[str, Any]]]:
    """Return bars strictly before ``trade_date`` per code, cached for the day."""
    day = str(trade_date or "")[:10]
    uniq = list(dict.fromkeys(str(c or "").zfill(6) for c in codes if c))
    need = [c for c in uniq if (_BARS_CACHE.get(c) or ("", []))[0] != day]
    if need:
        sem = asyncio.Semaphore(int(CV_FETCH_CONCURRENCY))

        async def one(code: str) -> None:
            async with sem:
                bars = await fetch_bars_with_turnover(client, code)
            if bars:
                _BARS_CACHE[code] = (day, [b for b in bars if b["date"] < day])

        await asyncio.gather(*(one(c) for c in need))
        if len(_BARS_CACHE) > _BARS_CACHE_MAX:
            for key in [k for k, v in _BARS_CACHE.items() if v[0] != day]:
                _BARS_CACHE.pop(key, None)
    return {c: list(_BARS_CACHE[c][1]) for c in uniq if (_BARS_CACHE.get(c) or ("", []))[0] == day}


def chip_profile(bars: list[dict[str, Any]], price: float) -> dict[str, float] | None:
    """Estimate the chip distribution over ``bars`` and read it at ``price``.

    Each day decays existing chips by that day's turnover and lays the new
    chips as a triangle over [low, high] peaking at the OHLC average.
    Returns profit share (%), price vs average cost (%), 90% concentration
    and main-peak offset vs price (%), or None when bars are too few.
    """
    win = [
        b for b in bars[-int(CV_CHIP_WINDOW):]
        if b.get("high") and b.get("low") and b.get("turnover") is not None
    ]
    if len(win) < int(CV_CHIP_MIN_BARS) or not price or price <= 0:
        return None
    lo_all = min(float(b["low"]) for b in win)
    hi_all = max(float(b["high"]) for b in win)
    if hi_all <= lo_all:
        return None
    n_bins = int(CV_CHIP_BINS)
    step = (hi_all - lo_all) / (n_bins - 1)
    grid = [lo_all + i * step for i in range(n_bins)]
    chips = [0.0] * n_bins
    for b in win:
        t = min(1.0, max(0.0, float(b["turnover"]) / 100.0))
        lo, hi = float(b["low"]), float(b["high"])
        avg = (float(b.get("open") or b["close"]) + float(b["close"]) + hi + lo) / 4.0
        i0 = max(0, int((lo - lo_all) / step))
        i1 = min(n_bins - 1, int(round((hi - lo_all) / step)))
        w = [0.0] * n_bins
        if i1 <= i0:
            w[i0] = 1.0
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
    smooth = [sum(chips[max(0, i - 2): i + 3]) for i in range(n_bins)]
    peak = grid[max(range(n_bins), key=lambda i: smooth[i])]
    return {
        "profit": round(below / total * 100.0, 1),
        "avg_cost": round(avg_cost, 3),
        "vs_cost": round((price / avg_cost - 1.0) * 100.0, 2),
        "conc90": round((p95 - p5) / (p95 + p5) * 100.0, 1) if p95 + p5 > 0 else 0.0,
        "peak": round(peak, 3),
        "peak_vs": round((peak / price - 1.0) * 100.0, 2),
    }


def volume_profile(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Summarize the last close's volume vs the prior 5 days and the 3-day trend."""
    vols = [float(b.get("volume") or 0.0) for b in bars]
    if len(vols) < 10:
        return None
    base5 = sum(vols[-6:-1]) / 5.0
    if base5 <= 0:
        return None
    vr = vols[-1] / base5
    pct = float(bars[-1].get("pct") or 0.0)
    if pct < 0 and vr < 0.8:
        pv = "缩量跌"
    elif pct < 0 and vr > 1.3:
        pv = "放量跌"
    elif pct > 0 and vr > 1.3:
        pv = "放量涨"
    elif pct > 0 and vr < 0.8:
        pv = "缩量涨"
    else:
        pv = ""
    early = sum(vols[-10:-3]) / 7.0
    trend3 = (sum(vols[-3:]) / 3.0) / early if early > 0 else None
    return {
        "vr_prev": round(vr, 2),
        "pct_prev": round(pct, 2),
        "pv_prev": pv,
        "vol_trend3": None if trend3 is None else round(trend3, 2),
    }


def classify(cv: dict[str, Any]) -> dict[str, str]:
    """Map raw cv numbers onto the coarse buckets used for tags, scoring and history.

    ``chip_pos=high`` when the entry sits 5–15% above average cost or on ≥80%
    profit chips without being a >15% runaway; ``vol1`` reads the prior day
    (shrink < 0.7× or shrinking up-day, spike ≥ 2×); ``vol3=fade`` when the
    last 3 days average < 0.8× the 7 days before.
    """
    out: dict[str, str] = {}
    vc, profit = num(cv.get("vs_cost")), num(cv.get("profit"))
    if vc is not None and profit is not None:
        out["chip_pos"] = "high" if vc <= 15 and (vc > 5 or profit >= 80) else "ok"
    vr = num(cv.get("vr_prev"))
    if vr is not None:
        if vr >= 2.0:
            out["vol1"] = "spike"
        elif vr < 0.7 or cv.get("pv_prev") == "缩量涨":
            out["vol1"] = "shrink"
        else:
            out["vol1"] = "normal"
    t3 = num(cv.get("vol_trend3"))
    if t3 is not None:
        out["vol3"] = "fade" if t3 < 0.8 else "normal"
    return out


def build_cv(bars: list[dict[str, Any]], price: float | None) -> dict[str, Any] | None:
    """Combine chip + volume context for one code at ``price`` (bars end the prior day)."""
    if not bars:
        return None
    out: dict[str, Any] = {"as_of": bars[-1].get("date")}
    chip = chip_profile(bars, float(price)) if price else None
    if chip:
        out.update(chip)
    vol = volume_profile(bars)
    if vol:
        out.update(vol)
    if len(out) == 1:
        return None
    out.update(classify(out))
    return out


def cv_tags(cv: dict[str, Any] | None) -> list[dict[str, str]]:
    """Return short display tags ``{k, label, tone, title}`` for one cv dict."""
    if not isinstance(cv, dict):
        return []
    tags: list[dict[str, str]] = []
    profit, vc = num(cv.get("profit")), num(cv.get("vs_cost"))
    if cv.get("chip_pos") == "high":
        tags.append({
            "k": "chip_high",
            "label": f"获利盘{profit:.0f}%" if profit is not None else "位置偏高",
            "tone": "warn",
            "title": (
                f"按计划价算，筹码获利盘 {profit}%，高于平均成本 {vc:+.1f}%；"
                "获利盘多、有兑现压力，挂计划价不追（打分只小幅扣分）。"
            ),
        })
    vol1, vr = cv.get("vol1"), num(cv.get("vr_prev"))
    if vol1 == "shrink":
        tags.append({
            "k": "vol_shrink",
            "label": "昨缩量",
            "tone": "good",
            "title": f"前一日量比 {vr}（对前 5 日均量），缩量休整；仅作提示，不计分。",
        })
    elif vol1 == "spike":
        tags.append({
            "k": "vol_spike",
            "label": "昨巨量",
            "tone": "warn",
            "title": f"前一日量比 {vr}，巨量后容易分歧，别追高；仅作提示，不计分。",
        })
    if cv.get("vol3") == "fade":
        tags.append({
            "k": "vol_fade",
            "label": "连缩量",
            "tone": "warn",
            "title": f"近 3 日均量只有再前 7 日的 {cv.get('vol_trend3')} 倍，人气在退。",
        })
    return tags


async def attach_cv(
    client: httpx.AsyncClient,
    items: list[dict[str, Any]],
    trade_date: str,
    *,
    price_keys: tuple[str, ...] = ("plan_price", "buy_price", "last"),
    overwrite: bool = False,
) -> int:
    """Attach ``cv`` onto stock items in place; return how many were filled."""
    todo = [
        it for it in items
        if it.get("code") and str(it.get("kind") or "stock") == "stock"
        and (overwrite or not isinstance(it.get("cv"), dict))
    ]
    if not todo:
        return 0
    bars_by = await bars_before_many(client, [str(it["code"]) for it in todo], trade_date)
    n = 0
    for it in todo:
        bars = bars_by.get(str(it["code"]).zfill(6))
        price = next((num(it.get(k)) for k in price_keys if num(it.get(k))), None)
        cv = build_cv(bars or [], price)
        if cv:
            it["cv"] = cv
            n += 1
    return n
