"""Gap classes and a small set of classic candlestick soft tags.

Tags are observational only — they feed ``env_gate`` / payload soft labels and
must not alone flip ready. Prefer precision over covering every textbook name.
"""

from __future__ import annotations

from typing import Any

from market_desk.numbers import num


def _bar(row: dict[str, Any]) -> dict[str, float] | None:
    c = num(row.get("close") or row.get("price"))
    if c is None or float(c) <= 0:
        return None
    o = num(row.get("open"))
    h = num(row.get("high"))
    l = num(row.get("low"))
    return {
        "open": float(o) if o is not None else float(c),
        "high": float(h) if h is not None else float(c),
        "low": float(l) if l is not None else float(c),
        "close": float(c),
    }


def classify_gap(
    *,
    open_px: float | None,
    prev_close: float | None,
    prev_high: float | None = None,
    prev_low: float | None = None,
) -> dict[str, Any]:
    """Classify an opening gap: common / breakaway / weak exhaustion heuristic.

    Breakaway: gap beyond prior high/low. Exhaustion: large gap (≥2.5%) after
    already-extended move is only weakly tagged (needs more context).
    """
    if open_px is None or prev_close is None or prev_close <= 0:
        return {"ok": False, "kind": "unknown", "gap_pct": None, "tags": [], "score": 0.0}
    gap_pct = (float(open_px) / float(prev_close) - 1.0) * 100.0
    tags: list[str] = []
    kind = "普通缺口"
    score = 0.0
    if abs(gap_pct) < 0.3:
        return {"ok": True, "kind": "无缺口", "gap_pct": round(gap_pct, 3), "tags": [], "score": 0.0}
    if gap_pct >= 0.3:
        tags.append("高开跳空缺口")
        score += 0.03
        if prev_high is not None and open_px > float(prev_high):
            kind = "突破缺口"
            tags.append("向上突破缺口")
            score += 0.04
        elif abs(gap_pct) >= 2.5:
            kind = "竭尽缺口弱标"
            tags.append("高开竭尽弱标")
            score -= 0.02
        else:
            kind = "普通缺口"
    else:
        tags.append("低开跳空缺口")
        score -= 0.05
        if prev_low is not None and open_px < float(prev_low):
            kind = "突破缺口"
            tags.append("向下突破缺口")
            score -= 0.04
        elif abs(gap_pct) >= 2.5:
            kind = "竭尽缺口弱标"
            tags.append("低开竭尽弱标")
            score -= 0.01
        else:
            kind = "普通缺口"
    return {
        "ok": True,
        "kind": kind,
        "gap_pct": round(gap_pct, 3),
        "tags": tags,
        "score": round(score, 3),
    }


def _body(b: dict[str, float]) -> float:
    return abs(b["close"] - b["open"])


def _range(b: dict[str, float]) -> float:
    return max(1e-9, b["high"] - b["low"])


def _bull(b: dict[str, float]) -> bool:
    return b["close"] >= b["open"]


def detect_kline_tags(bars: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Detect up to five high-value classic patterns on the last bars.

    Patterns: bullish/bearish engulfing, hammer, shooting star, morning star,
    piercing / dark-cloud (as a pair of related two-bar tags).
    """
    series = [_bar(x) for x in (bars or [])]
    series = [b for b in series if b]
    if len(series) < 2:
        return {"ok": False, "tags": [], "score": 0.0, "hits": []}
    tags: list[str] = []
    hits: list[str] = []
    score = 0.0
    a, b = series[-2], series[-1]

    # Engulfing
    if (not _bull(a)) and _bull(b) and b["open"] <= a["close"] and b["close"] >= a["open"]:
        if _body(b) >= _body(a) * 0.9:
            tags.append("阳包阴")
            hits.append("bullish_engulfing")
            score += 0.06
    if _bull(a) and (not _bull(b)) and b["open"] >= a["close"] and b["close"] <= a["open"]:
        if _body(b) >= _body(a) * 0.9:
            tags.append("阴包阳")
            hits.append("bearish_engulfing")
            score -= 0.06

    # Hammer / shooting star on last bar
    body = _body(b)
    upper = b["high"] - max(b["open"], b["close"])
    lower = min(b["open"], b["close"]) - b["low"]
    rng = _range(b)
    if lower >= max(body * 2.0, rng * 0.55) and upper <= body * 0.35:
        tags.append("锤头线")
        hits.append("hammer")
        score += 0.05
    if upper >= max(body * 2.0, rng * 0.55) and lower <= body * 0.35:
        tags.append("射击之星")
        hits.append("shooting_star")
        score -= 0.05

    # Piercing / dark cloud
    mid_a = (a["open"] + a["close"]) / 2.0
    if (not _bull(a)) and _bull(b) and b["open"] < a["low"] and b["close"] > mid_a and b["close"] < a["open"]:
        tags.append("曙光初现")
        hits.append("piercing")
        score += 0.05
    if _bull(a) and (not _bull(b)) and b["open"] > a["high"] and b["close"] < mid_a and b["close"] > a["open"]:
        tags.append("乌云盖顶")
        hits.append("dark_cloud")
        score -= 0.05

    # Morning star (3 bars)
    if len(series) >= 3:
        x, y, z = series[-3], series[-2], series[-1]
        small = _body(y) <= max(_body(x), _body(z)) * 0.45
        mid_x = (x["open"] + x["close"]) / 2.0
        if (not _bull(x)) and small and _bull(z) and z["close"] > mid_x:
            tags.append("早晨之星")
            hits.append("morning_star")
            score += 0.07

    # Dedup tags preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return {
        "ok": bool(uniq),
        "tags": uniq[:5],
        "hits": hits[:5],
        "score": round(max(-0.12, min(0.12, score)), 3),
    }


def feature_gap_and_kline(
    bars: list[dict[str, Any]] | None,
    *,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine gap class + kline soft tags for env_gate feature pack."""
    series = [_bar(x) for x in (bars or [])]
    series = [b for b in series if b]
    gap = {"ok": False, "score": 0.0, "tags": []}
    if len(series) >= 2:
        gap = classify_gap(
            open_px=series[-1]["open"],
            prev_close=series[-2]["close"],
            prev_high=series[-2]["high"],
            prev_low=series[-2]["low"],
        )
    elif metrics:
        # Fallback to metrics-only open gap without OHLC continuity.
        g = num((metrics or {}).get("index_gap_pct") or (metrics or {}).get("gap_pct"))
        if g is not None:
            gap = classify_gap(open_px=100.0 * (1.0 + float(g) / 100.0), prev_close=100.0)
    kline = detect_kline_tags(bars)
    tags = list(gap.get("tags") or []) + list(kline.get("tags") or [])
    score = float(gap.get("score") or 0) + float(kline.get("score") or 0)
    return {
        "ok": bool(gap.get("ok") or kline.get("ok")),
        "score": round(score, 3),
        "tags": tags[:8],
        "gap": gap,
        "kline": kline,
    }
