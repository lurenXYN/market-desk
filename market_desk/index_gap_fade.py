"""Index-level 高开低走 statistic: gap up, then does the day break the open?

This is a *daily-bar* statistic for an index. The per-stock, intraday version lives in
``market_desk.gap_fade`` (quote gap >= 2.5% and last >= 1.5% below open, feeding the
blacklist); the two differ in purpose and inputs, so they stay separate modules.

Definition, kept narrow so the number is auditable:

* a **gap day** opens more than ``threshold`` percent above the previous close;
* a gap day **breaks the open** when its low prints below the open price;
* ``zero_miss`` is true only when every gap day broke the open.

The popular "全年零失误" claim comes from a hand-picked window; this module reports the
real count over the requested lookback and lists the days that did not break.
"""

from __future__ import annotations

from typing import Any, Sequence

INDEX_GAP_FADE_SYMBOL = "sz399006"  # 创业板指
INDEX_GAP_FADE_THRESHOLD = 1.0
INDEX_GAP_FADE_LOOKBACK = 240


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _day(bar: dict[str, Any]) -> str:
    return str(bar.get("date") or bar.get("trade_date") or "")[:10]


def index_gap_fade_stats(
    bars: Sequence[dict[str, Any]] | None,
    *,
    threshold: float = INDEX_GAP_FADE_THRESHOLD,
    lookback: int | None = INDEX_GAP_FADE_LOOKBACK,
    label: str = "",
) -> dict[str, Any]:
    """Count gap days and how many of them broke the open price."""
    clean: list[tuple[str, float, float, float]] = []
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        day = _day(bar)
        op, low, close = _num(bar.get("open")), _num(bar.get("low")), _num(bar.get("close"))
        if not day or op is None or low is None or close is None:
            continue
        clean.append((day, op, low, close))
    clean.sort(key=lambda row: row[0])
    if lookback is not None and len(clean) > int(lookback):
        clean = clean[-int(lookback):]

    base: dict[str, Any] = {
        "ok": False,
        "label": label,
        "threshold": float(threshold),
        "days": len(clean),
    }
    if len(clean) < 2:
        base["detail"] = "交易日不足，无法统计"
        return base

    gap_days = 0
    broke_days = 0
    misses: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    for prev, cur in zip(clean, clean[1:]):
        gap_pct = (cur[1] / prev[3] - 1.0) * 100.0
        if gap_pct <= float(threshold):
            continue
        gap_days += 1
        row = {
            "date": cur[0],
            "gap_pct": round(gap_pct, 2),
            "open": round(cur[1], 2),
            "low": round(cur[2], 2),
        }
        if cur[2] < cur[1]:
            broke_days += 1
            hits.append(row)
        else:
            misses.append(row)

    return {
        **base,
        "ok": True,
        "start": clean[0][0],
        "end": clean[-1][0],
        "gap_days": gap_days,
        "broke_days": broke_days,
        "miss_days": len(misses),
        "zero_miss": bool(gap_days and broke_days == gap_days),
        "hit_rate": round(100.0 * broke_days / gap_days, 1) if gap_days else None,
        "misses": misses[-8:],
        "last_gap": hits[-1] if hits else None,
    }


def index_gap_fade_line(stats: dict[str, Any] | None) -> str:
    """One-line UI string, e.g. ``创业板指 高开>1.0% 34 次 · 跌破开盘 33 次 · 97.1%``."""
    if not stats or not stats.get("ok"):
        return ""
    label = stats.get("label") or "指数"
    thr = stats.get("threshold")
    gap = int(stats.get("gap_days") or 0)
    broke = int(stats.get("broke_days") or 0)
    if not gap:
        return f"{label} 高开>{thr}% 0 次（近 {stats.get('days')} 个交易日）"
    tail = "零失误" if stats.get("zero_miss") else f"{stats.get('hit_rate')}%"
    return f"{label} 高开>{thr}% {gap} 次 · 跌破开盘 {broke} 次 · {tail}"
