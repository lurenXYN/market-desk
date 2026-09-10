"""Daily trend classification from closing prices."""

from __future__ import annotations

from typing import Any


def classify_daily_trend(
    closes: list[float] | None,
    *,
    fetch_ok: bool = True,
) -> dict[str, Any]:
    """Classify a daily close series as up / down / not-up.

    Rules (need ≥20 bars):
    - Up: close > MA20, MA5 > MA10, MA10 ≳ MA20, and MA20 not falling
    - Down: close < MA20, MA5 < MA10, MA10 ≲ MA20, and MA20 not rising
    - Otherwise: sideways / not an uptrend

    Insufficient samples:
    - fetch_ok=False or empty series → 行情未取到
    - 1–19 bars → 样本不足（上市不足约20日）
    """
    series = [float(x) for x in (closes or []) if x is not None]
    if not fetch_ok or not series:
        return {
            "label": "行情未取到",
            "up": False,
            "down": False,
            "warn": "不是上升趋势",
            "ma5": None,
            "ma10": None,
            "ma20": None,
            "bars": len(series),
            "quality": "fetch_fail",
        }
    if len(series) < 20:
        return {
            "label": "样本不足（上市不足约20日）",
            "up": False,
            "down": False,
            "warn": "不是上升趋势",
            "ma5": None,
            "ma10": None,
            "ma20": None,
            "bars": len(series),
            "quality": "thin",
        }

    def _ma(n: int) -> float:
        return sum(series[-n:]) / float(n)

    ma5 = _ma(5)
    ma10 = _ma(10)
    ma20 = _ma(20)
    ma20_prev = sum(series[-25:-5]) / 20.0 if len(series) >= 25 else ma20
    last = series[-1]
    rising_ma = ma20 >= ma20_prev * 0.998
    falling_ma = ma20 <= ma20_prev * 1.002
    up = bool(last > ma20 and ma5 > ma10 and ma10 >= ma20 * 0.999 and rising_ma)
    down = bool(last < ma20 and ma5 < ma10 and ma10 <= ma20 * 1.001 and falling_ma)
    if up:
        label = "上升趋势"
        warn = None
    elif down:
        label = "下降趋势"
        warn = "不是上升趋势"
    else:
        label = "震荡/非上升"
        warn = "不是上升趋势"
    return {
        "label": label,
        "up": up,
        "down": down,
        "warn": warn,
        "ma5": round(ma5, 3),
        "ma10": round(ma10, 3),
        "ma20": round(ma20, 3),
        "last": round(last, 3),
        "bars": len(series),
        "quality": "ok",
    }


def trend_score_adj(
    trend: dict[str, Any] | None,
    *,
    up_bonus: float,
    down_penalty: float,
) -> float:
    """Return score nudge: up +, down −, unclear/missing 0."""
    t = trend or {}
    if t.get("up"):
        return float(up_bonus)
    if t.get("down"):
        return -float(down_penalty)
    return 0.0


def classify_many(
    closes_by_code: dict[str, list[float]],
    fetch_ok_by_code: dict[str, bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """Classify daily trends for a code→closes map."""
    ok_map = fetch_ok_by_code or {}
    out: dict[str, dict[str, Any]] = {}
    for code, closes in (closes_by_code or {}).items():
        c = str(code or "").zfill(6)
        if not c:
            continue
        out[c] = classify_daily_trend(
            closes,
            fetch_ok=ok_map.get(c, bool(closes)),
        )
    return out
