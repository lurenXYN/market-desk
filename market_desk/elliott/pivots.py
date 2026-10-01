"""Bar normalization, zigzag pivots, and structure snapshot."""

from __future__ import annotations

from typing import Any


def _normalize_bars(bars: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in bars or []:
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        high = row.get("high")
        low = row.get("low")
        try:
            hi = float(high) if high is not None else close
            lo = float(low) if low is not None else close
        except (TypeError, ValueError):
            hi, lo = close, close
        if hi < lo:
            hi, lo = lo, hi
        out.append(
            {
                "date": str(row.get("date") or row.get("trade_date") or ""),
                "close": close,
                "high": hi,
                "low": lo,
            }
        )
    return out


def _alternating_chain(
    pivots: list[dict[str, Any]],
    need: int,
    start_kind: str,
) -> list[dict[str, Any]]:
    """Return the most recent alternating pivot chain of length ``need``.

    Scans windows from the right so a completed wave ending a few pivots ago
    still wins over a dangling newer swing that breaks the start kind.
    """
    if need < 2 or not pivots or len(pivots) < need:
        # Still allow shorter fallbacks via caller using a smaller ``need``.
        if need < 2 or not pivots:
            return []
    best: list[dict[str, Any]] = []
    upper = len(pivots)
    for end in range(upper - 1, need - 2, -1):
        start = end - need + 1
        if start < 0:
            continue
        chunk = pivots[start : end + 1]
        if str(chunk[0].get("kind") or "") != start_kind:
            continue
        ok = True
        expect = start_kind
        for p in chunk:
            if str(p.get("kind") or "") != expect:
                ok = False
                break
            expect = "high" if expect == "low" else "low"
        if ok:
            return list(chunk)
    return best


def _zigzag_pivots(
    series: list[dict[str, Any]],
    *,
    min_move_pct: float,
) -> list[dict[str, Any]]:
    """Build alternating high/low pivots with a minimum percentage swing.

    A pivot is confirmed only after an opposite retrace of ``min_move_pct``.
    Extremes for the *next* hunt are taken strictly from bars after the last
    pivot index, so the scanner cannot stall on the pivot bar itself.
    """
    n = len(series)
    if n < 5:
        return []

    pivots: list[dict[str, Any]] = []
    hunting: str | None = None  # "high" or "low"
    last_i = -1
    last_px = 0.0
    ext_high_i = -1
    ext_high = 0.0
    ext_low_i = -1
    ext_low = 0.0

    def _push(kind: str, idx: int, px: float) -> None:
        if pivots and pivots[-1]["kind"] == kind:
            prev = pivots[-1]
            if kind == "high" and px >= float(prev["price"]):
                pivots[-1] = _pivot(kind, idx, px, series)
            elif kind == "low" and px <= float(prev["price"]):
                pivots[-1] = _pivot(kind, idx, px, series)
            return
        if pivots and int(pivots[-1]["index"]) >= idx:
            return
        pivots.append(_pivot(kind, idx, px, series))

    first_low = float(series[0]["low"])
    first_high = float(series[0]["high"])
    boot_high_i, boot_high = 0, first_high
    boot_low_i, boot_low = 0, first_low

    for i, bar in enumerate(series):
        hi = float(bar["high"])
        lo = float(bar["low"])

        if hunting is None:
            if hi >= boot_high:
                boot_high, boot_high_i = hi, i
            if lo <= boot_low:
                boot_low, boot_low_i = lo, i
            up = (boot_high - first_low) / first_low * 100.0 if first_low > 0 else 0.0
            dn = (first_high - boot_low) / first_high * 100.0 if first_high > 0 else 0.0
            if up >= min_move_pct and boot_high_i > 0:
                _push("low", 0, first_low)
                last_i, last_px = 0, first_low
                hunting = "high"
                ext_high_i, ext_high = -1, 0.0
            elif dn >= min_move_pct and boot_low_i > 0:
                _push("high", 0, first_high)
                last_i, last_px = 0, first_high
                hunting = "low"
                ext_low_i, ext_low = -1, 0.0
            continue

        # Only bars AFTER the last confirmed pivot feed the next extreme.
        if i <= last_i:
            continue
        if hunting == "high":
            if ext_high_i < 0 or hi >= ext_high:
                ext_high, ext_high_i = hi, i
            retrace = (ext_high - lo) / ext_high * 100.0 if ext_high > 0 else 0.0
            if retrace >= min_move_pct and ext_high_i > last_i:
                _push("high", ext_high_i, ext_high)
                last_i, last_px = ext_high_i, ext_high
                hunting = "low"
                if i > ext_high_i:
                    ext_low_i, ext_low = i, lo
                else:
                    ext_low_i, ext_low = -1, 0.0
        else:
            if ext_low_i < 0 or lo <= ext_low:
                ext_low, ext_low_i = lo, i
            bounce = (hi - ext_low) / ext_low * 100.0 if ext_low > 0 else 0.0
            if bounce >= min_move_pct and ext_low_i > last_i:
                _push("low", ext_low_i, ext_low)
                last_i, last_px = ext_low_i, ext_low
                hunting = "high"
                if i > ext_low_i:
                    ext_high_i, ext_high = i, hi
                else:
                    ext_high_i, ext_high = -1, 0.0

    if hunting == "high" and ext_high_i > last_i and last_px > 0:
        move = (ext_high - last_px) / last_px * 100.0
        if move >= min_move_pct * 0.8:
            _push("high", ext_high_i, ext_high)
    elif hunting == "low" and ext_low_i > last_i and last_px > 0:
        move = (last_px - ext_low) / last_px * 100.0
        if move >= min_move_pct * 0.8:
            _push("low", ext_low_i, ext_low)
    return pivots


def _pivot(kind: str, idx: int, px: float, series: list[dict[str, Any]]) -> dict[str, Any]:
    bar = series[idx]
    return {
        "kind": kind,
        "index": idx,
        "price": round(float(px), 2),
        "date": bar.get("date") or "",
    }


def _structure_snapshot(
    pivots: list[dict[str, Any]],
    last: float,
    series: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive recent swing context used by scenario scorers."""
    last_n = pivots[-6:] if len(pivots) >= 2 else list(pivots)
    highs = [p for p in last_n if p.get("kind") == "high"]
    lows = [p for p in last_n if p.get("kind") == "low"]
    last_pivot = pivots[-1] if pivots else None
    prev_pivot = pivots[-2] if len(pivots) >= 2 else None
    swing_pct = None
    if last_pivot and prev_pivot:
        a = float(prev_pivot["price"])
        b = float(last_pivot["price"])
        if a > 0:
            swing_pct = round((b - a) / a * 100.0, 2)
    # MA20 slope soft context.
    closes = [float(x["close"]) for x in series]
    ma20 = sum(closes[-20:]) / 20.0 if len(closes) >= 20 else None
    ma20_prev = sum(closes[-40:-20]) / 20.0 if len(closes) >= 40 else ma20
    trend = "up"
    if ma20 is not None and ma20_prev is not None:
        if ma20 > ma20_prev * 1.004 and last >= ma20:
            trend = "up"
        elif ma20 < ma20_prev * 0.996 and last <= ma20:
            trend = "down"
        else:
            trend = "side"
    # Contracting range?
    recent = pivots[-5:]
    contracting = False
    if len(recent) >= 4:
        spans = []
        for i in range(1, len(recent)):
            spans.append(abs(float(recent[i]["price"]) - float(recent[i - 1]["price"])))
        if len(spans) >= 3 and spans[-1] < spans[0] * 0.7:
            contracting = True
    hh = float(highs[-1]["price"]) if highs else None
    hl = float(lows[-1]["price"]) if lows else None
    ph = float(highs[-2]["price"]) if len(highs) >= 2 else None
    pl = float(lows[-2]["price"]) if len(lows) >= 2 else None
    higher_high = bool(hh is not None and ph is not None and hh > ph)
    higher_low = bool(hl is not None and pl is not None and hl > pl)
    lower_high = bool(hh is not None and ph is not None and hh < ph)
    lower_low = bool(hl is not None and pl is not None and hl < pl)
    from_low = None
    from_high = None
    if hl and hl > 0:
        from_low = round((last - hl) / hl * 100.0, 2)
    if hh and hh > 0:
        from_high = round((last - hh) / hh * 100.0, 2)
    return {
        "trend": trend,
        "last_pivot": last_pivot,
        "prev_pivot": prev_pivot,
        "swing_pct": swing_pct,
        "last_high": hh,
        "last_low": hl,
        "prev_high": ph,
        "prev_low": pl,
        "higher_high": higher_high,
        "higher_low": higher_low,
        "lower_high": lower_high,
        "lower_low": lower_low,
        "contracting": contracting,
        "from_last_low_pct": from_low,
        "from_last_high_pct": from_high,
        "ma20": round(ma20, 2) if ma20 is not None else None,
        "above_ma20": bool(ma20 is not None and last >= ma20),
    }
