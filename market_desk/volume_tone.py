"""Volume-ratio tone labels for soft list annotations (观察 only)."""

from __future__ import annotations

from typing import Any

from market_desk.numbers import num

# Tencent 量比 thresholds: below → 缩量, above → 放量, else 平量.
VOL_SHRINK = 0.8
VOL_EXPAND = 1.5


def classify_vol_ratio(vol_ratio: Any, *, baseline: Any = None) -> dict[str, Any]:
    """Map a volume ratio (量比) to a compact 缩量/平量/放量 label.

    Args:
        vol_ratio: Vendor 量比 (Tencent field ~49).
        baseline: Typical 量比 for this clock time, from the prior session's own volume
            shape. The neutral value is ~3 early and ~1 near the close, so a fixed 1.5
            threshold would call almost everything "expanding" in the first hour.

    Returns:
        Dict with raw ``ratio``, ``rel`` (ratio / baseline), ``baseline``, ``tone``
        (``shrink``|``flat``|``expand``), ``label`` like ``放量 2.90x`` (relative) and
        ``title`` carrying both numbers. Empty dict when the ratio is missing.
    """
    ratio = num(vol_ratio)
    if ratio is None:
        return {}
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return {}
    if r != r:  # NaN
        return {}
    base = num(baseline)
    base = float(base) if base and float(base) > 0 else None
    rel = r / base if base else r
    if rel < VOL_SHRINK:
        tone = "shrink"
        word = "缩量"
    elif rel > VOL_EXPAND:
        tone = "expand"
        word = "放量"
    else:
        tone = "flat"
        word = "平量"
    title = f"量比 {r:.2f}x"
    if base:
        title = f"原始量比 {r:.2f}x · 该时点典型 {base:.2f}x · 相对 {rel:.2f}x"
    return {
        "ratio": round(r, 2),
        "rel": round(rel, 2),
        "baseline": None if base is None else round(base, 2),
        "tone": tone,
        "label": f"{word} {rel:.2f}x",
        "title": title,
    }
