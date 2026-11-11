"""Volume-ratio tone labels for soft list annotations (观察 only)."""

from __future__ import annotations

from typing import Any

from market_desk.numbers import num

# Tencent 量比 thresholds: below → 缩量, above → 放量, else 平量.
VOL_SHRINK = 0.8
VOL_EXPAND = 1.5


def classify_vol_ratio(vol_ratio: Any) -> dict[str, Any]:
    """Map a volume ratio (量比) to a compact 缩量/平量/放量 label.

    Args:
        vol_ratio: Today volume vs recent average (Tencent field ~49).

    Returns:
        Dict with ``ratio``, ``tone`` (``shrink``|``flat``|``expand``), and
        ``label`` like ``缩量 0.72x``. Empty dict when the ratio is missing.
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
    if r < VOL_SHRINK:
        tone = "shrink"
        word = "缩量"
    elif r > VOL_EXPAND:
        tone = "expand"
        word = "放量"
    else:
        tone = "flat"
        word = "平量"
    return {
        "ratio": round(r, 2),
        "tone": tone,
        "label": f"{word} {r:.2f}x",
    }
