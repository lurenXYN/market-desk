"""Pot-bottom (锅底) pattern on daily bars: drop → quiet low-volume base → mild-volume lift-off.

Shape (e.g. 华是科技 2026-09): a fall of ≥ ``POT_MIN_DROP_PCT`` from a recent high,
then ``POT_BASE_MIN``–``POT_BASE_MAX`` sessions of small candles in a tight close
range on shrunken volume, without a fresh breakdown late in the base, then the
first sessions closing above the base top on moderately expanding volume. The
hit is reported while the move off the base is still small (初期), so it fires
around the first breakout day rather than after the run.

Observation only: out-of-sample replays (``scripts/oos_pot_bottom.py``) found no
excess return over same-day names, so hits never feed ready / scores.

Pure functions; no I/O.
"""

from __future__ import annotations

from typing import Any

POT_VERSION = 1
POT_MIN_DROP_PCT = 15.0      # base mid at least this far below the high before it
POT_DROP_LOOKBACK = 60       # sessions before the base end searched for that high
POT_BASE_MIN = 8
POT_BASE_MAX = 30
POT_BASE_RANGE_PCT = 12.0    # (max close − min close) / min close inside the base
POT_BASE_BODY_PCT = 2.5      # median |daily %| inside the base (small candles)
POT_BASE_LATE_LOW = 0.95     # second-half low ≥ this × first-half low (no late breakdown)
POT_VOL_SHRINK = 0.8         # base median volume / median of the 40 sessions before it
POT_LIFT_VOL_MIN = 1.2       # max volume of the lift-off sessions / base median
POT_LIFT_VOL_MAX = 3.5
POT_LIFT_MAX_DAYS = 3        # sessions since the base end
POT_EARLY_RISE_PCT = 15.0    # close vs base median: ≤ this is 初期
# 浅跌放量 subset (drop 15–25%, lift ≥ 2×): best in discovery, flat out of sample
# (TODO 2026-10-10). Shown as a filter only, never as a ranking or a buy signal.
POT_FOCUS_DROP = (15.0, 25.0)
POT_FOCUS_LIFT = 2.0
POT_FOCUS_TAG = "浅跌放量"


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0) if n else 0.0


def _base_ok(closes: list[float], pcts: list[float], vols: list[float], s: int, e: int) -> dict[str, float] | None:
    """Return base stats for sessions ``s..e`` (inclusive) or None when it is not a quiet base."""
    cl = closes[s : e + 1]
    lo, hi = min(cl), max(cl)
    if lo <= 0:
        return None
    rng = (hi / lo - 1.0) * 100.0
    if rng > POT_BASE_RANGE_PCT:
        return None
    body = _median([abs(p) for p in pcts[s : e + 1]])
    if body > POT_BASE_BODY_PCT:
        return None
    half = (e - s + 1) // 2
    if min(cl[half:]) < min(cl[:half]) * POT_BASE_LATE_LOW:
        return None
    before = [v for v in vols[max(0, s - 40) : s] if v > 0]
    base_v = _median([v for v in vols[s : e + 1] if v > 0])
    if len(before) < 20 or base_v <= 0:
        return None
    shrink = base_v / _median(before)
    if shrink > POT_VOL_SHRINK:
        return None
    return {"range": rng, "body": body, "shrink": shrink, "vol": base_v, "top": hi, "mid": _median(cl)}


def score_pot_bottom(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Detect a pot-bottom lift-off on the last bar of ``bars`` (oldest first).

    The last bar must close above the base top, at most ``POT_LIFT_MAX_DAYS``
    sessions after the base ends, with lift-off volume within
    ``POT_LIFT_VOL_MIN``–``POT_LIFT_VOL_MAX`` × the base median.
    Among qualifying bases the longest one wins (a longer quiet base is the
    stronger "企稳").

    Returns:
        Diagnostics (base dates / length / range / volume shrink, drop from the
        prior high, lift-off volume ratio, rise off the base, ``stage``, ``tags``,
        ``days_since_base``) or None.
    """
    n = len(bars)
    if n < POT_DROP_LOOKBACK + POT_BASE_MAX + 5:
        return None
    closes = [float(b["close"]) for b in bars]
    highs = [float(b.get("high") or b["close"]) for b in bars]
    vols = [float(b.get("volume") or 0) for b in bars]
    pcts = [0.0] + [(closes[t] / closes[t - 1] - 1.0) * 100.0 if closes[t - 1] > 0 else 0.0 for t in range(1, n)]
    i = n - 1
    # No MA5 > MA10 gate: on the first close above a flat base MA5 often still sits
    # under MA10 (华是科技 2026-09-22: 45.17 vs 45.27), and by the time it crosses the
    # breakout is no longer fresh.
    # Every base close and every lift-off close before today stays ≤ the base top < today's close.
    if closes[i] <= max(closes[i - POT_BASE_MIN : i]):
        return None
    best: tuple[int, int, dict[str, float]] | None = None
    for lag in range(1, POT_LIFT_MAX_DAYS + 1):
        e = i - lag
        for length in range(POT_BASE_MAX, POT_BASE_MIN - 1, -1):
            s = e - length + 1
            if s < 40:
                continue
            st = _base_ok(closes, pcts, vols, s, e)
            if st is None:
                continue
            if best is None or length > best[1] - best[0] + 1:
                best = (s, e, st)
            break
    if best is None:
        return None
    s, e, st = best
    c = closes[i]
    if c <= st["top"]:
        return None
    # The breakout must be fresh: no close above the base top before today.
    if any(closes[t] > st["top"] for t in range(e + 1, i)):
        return None
    peak = max(highs[max(0, s - POT_DROP_LOOKBACK) : s + 1])
    drop = (1.0 - st["mid"] / peak) * 100.0 if peak > 0 else 0.0
    if drop < POT_MIN_DROP_PCT:
        return None
    lift_v = max(vols[e + 1 : i + 1])
    lift_ratio = lift_v / st["vol"]
    if not (POT_LIFT_VOL_MIN <= lift_ratio <= POT_LIFT_VOL_MAX):
        return None
    rise = (c / st["mid"] - 1.0) * 100.0
    stage = "初期" if rise <= POT_EARLY_RISE_PCT else "已拉开"
    length = e - s + 1
    focus = is_pot_focus(drop, lift_ratio)
    tags = [stage, f"平台{length}日", f"缩量{st['shrink']:.2f}", f"放量{lift_ratio:.1f}×", f"前跌{drop:.0f}%"]
    if focus:
        tags.append(POT_FOCUS_TAG)
    return {
        "base_start": str(bars[s].get("date") or ""),
        "base_end": str(bars[e].get("date") or ""),
        "base_len": length,
        "base_range": round(st["range"], 1),
        "base_body": round(st["body"], 2),
        "vol_shrink": round(st["shrink"], 2),
        "drop_pct": round(drop, 1),
        "lift_vol": round(lift_ratio, 2),
        "rise_pct": round(rise, 1),
        "days_since_base": i - e,
        "close": round(c, 2),
        "pct": round(pcts[i], 2),
        "stage": stage,
        "focus": focus,
        "tags": tags,
    }


def is_pot_focus(drop_pct: float, lift_vol: float) -> bool:
    """Return True for the 浅跌放量 subset (moderate prior drop, clear volume lift)."""
    lo, hi = POT_FOCUS_DROP
    return lo <= float(drop_pct) < hi and float(lift_vol) >= POT_FOCUS_LIFT
