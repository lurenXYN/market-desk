"""CUSUM change-point detection for the desk's own state series.

The phase ladder (恐慌/分歧/发酵/高潮) uses fixed cut-offs, so a real regime turn
is only visible once the level has already crossed a band — usually 1-2 sessions
after the turn, which is exactly where drawdowns come from.

This module runs a two-sided CUSUM on a numeric series (temperature, breadth,
index return…) and reports *when the mean shifted*, not just the current level.
It is deliberately side-effect free and read-only: callers decide whether to act.
"""

from __future__ import annotations

import math
from typing import Any, Sequence


def _clean(values: Sequence[Any]) -> list[float]:
    out: list[float] = []
    for v in values or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def cusum(
    values: Sequence[Any],
    *,
    drift: float = 0.5,
    threshold: float = 5.0,
) -> dict[str, Any]:
    """Two-sided CUSUM over a series; ``drift`` is in standard-deviation units.

    Returns the first index where the cumulative deviation crosses
    ``threshold``, the direction of the shift, and the shift magnitude in
    standard deviations.
    """
    vals = _clean(values)
    n = len(vals)
    if n < 8:
        return {"ok": False, "reason": f"样本不足（n={n}）", "changed": False}
    # Baseline must come from the *early* part of the series: using the whole
    # window lets the post-shift tail drag the mean, so the pre-shift points look
    # like an upward deviation and the direction flips.
    base_n = max(8, n // 2)
    base = vals[:base_n]
    mean = sum(base) / len(base)
    var = sum((v - mean) ** 2 for v in base) / max(1, len(base) - 1)
    sd = math.sqrt(var)
    if sd <= 1e-9:
        # A flat baseline (e.g. temperature pinned for a stretch) still has a
        # meaningful scale once the shift lands; fall back to the full spread.
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / max(1, n - 1))
    if sd <= 1e-9:
        return {"ok": False, "reason": "序列无波动", "changed": False}
    k = max(0.0, float(drift)) * sd
    h = max(0.5, float(threshold)) * sd
    up = down = 0.0
    first_up = first_down = None
    for i, v in enumerate(vals):
        up = max(0.0, up + (v - mean) - k)
        down = max(0.0, down - (v - mean) - k)
        if first_up is None and up > h:
            first_up = i
        if first_down is None and down > h:
            first_down = i
    if first_up is None and first_down is None:
        return {
            "ok": True,
            "changed": False,
            "direction": "flat",
            "since_index": None,
            "score": 0.0,
            "shift_sd": 0.0,
            "mean": round(mean, 3),
            "note": "未见均值漂移（CUSUM未越界）",
        }
    up_hit = first_up is not None and (first_down is None or first_up <= first_down)
    idx = first_up if up_hit else first_down
    tail = vals[idx:]
    shift = (sum(tail) / len(tail) - mean) / sd if tail else 0.0
    return {
        "ok": True,
        "changed": True,
        "direction": "up" if up_hit else "down",
        "since_index": idx,
        "score": round((up if up_hit else down) / h, 3),
        "shift_sd": round(shift, 3),
        "mean": round(mean, 3),
        "note": (
            f"均值向{'上' if up_hit else '下'}漂移 {shift:+.2f}σ（自序列第{idx}点起）"
        ),
    }


def state_shift(
    values: Sequence[Any],
    *,
    window: int = 40,
    drift: float = 0.5,
    threshold: float = 5.0,
) -> dict[str, Any]:
    """Detect a shift inside the most recent ``window`` observations."""
    vals = _clean(values)
    if not vals:
        return {"ok": False, "reason": "无可用序列", "changed": False}
    tail = vals[-max(8, int(window)) :]
    out = cusum(tail, drift=drift, threshold=threshold)
    out["window"] = len(tail)
    out["latest"] = round(tail[-1], 3)
    if out.get("changed") and out.get("since_index") is not None:
        behind = len(tail) - 1 - int(out["since_index"])
        out["sessions_since"] = behind
        out["note"] = f"{out['note']}，距最新约{behind}个观测"
    return out