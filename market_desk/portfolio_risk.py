"""Portfolio-level risk overlay (组合层风控).

The desk sized every card on its own: per-trade risk, per-trade cap, then stop.
Four cards from the same mainline therefore risked 4x the single-trade budget
with no shared ceiling. This module adds the missing portfolio view:

* **board concentration** — share of book value in one board;
* **correlation penalty** — average pairwise correlation of held names;
* **drawdown budget** — book drawdown against a pre-agreed cap.

Everything is a *soft* multiplier in [0.4, 1.0] plus notes; it never flips a
card to "do not buy" and never touches ``ready``. Missing inputs simply skip the
corresponding check instead of guessing.
"""

from __future__ import annotations

import math
from typing import Any

from market_desk.settings import setting


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _returns(closes: list[float], window: int = 60) -> list[float]:
    vals = [float(c) for c in (closes or [])[-window:] if c]
    if len(vals) < 3:
        return []
    return [vals[i] / vals[i - 1] - 1.0 for i in range(1, len(vals)) if vals[i - 1]]


def pearson(a: list[float], b: list[float]) -> float | None:
    """Correlation of two return series (None when inputs are too short)."""
    n = min(len(a or []), len(b or []))
    if n < 5:
        return None
    xs, ys = list(a)[-n:], list(b)[-n:]
    mx = sum(xs) / n
    my = sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return round(cov / math.sqrt(vx * vy), 4)


def average_correlation(closes_by_code: dict[str, list[float]]) -> float | None:
    """Mean pairwise correlation across held codes."""
    codes = [c for c, v in (closes_by_code or {}).items() if len(v or []) >= 6]
    if len(codes) < 2:
        return None
    series = {c: _returns(closes_by_code[c]) for c in codes}
    pairs: list[float] = []
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            rho = pearson(series[a], series[b])
            if rho is not None:
                pairs.append(rho)
    if not pairs:
        return None
    return round(sum(pairs) / len(pairs), 3)


def board_exposure(
    positions: list[dict[str, Any]],
    *,
    board_of: dict[str, str] | None = None,
) -> tuple[dict[str, float], float]:
    """Return (board → share of book value, total market value)."""
    board_of = board_of or {}
    values: dict[str, float] = {}
    for pos in positions or []:
        code = str(pos.get("code") or "").strip()
        qty = _num(pos.get("qty")) or 0.0
        last = _num(pos.get("last")) or _num(pos.get("cost")) or 0.0
        if not code or qty <= 0 or last <= 0:
            continue
        board = str(board_of.get(code) or pos.get("board") or pos.get("mainline") or "未归类")
        values[board] = values.get(board, 0.0) + qty * last
    total = sum(values.values())
    if total <= 0:
        return {}, 0.0
    return {k: round(100.0 * v / total, 1) for k, v in values.items()}, round(total, 2)


def portfolio_gate(
    *,
    positions: list[dict[str, Any]] | None = None,
    closes_by_code: dict[str, list[float]] | None = None,
    board_of: dict[str, str] | None = None,
    drawdown_pct: float | None = None,
) -> dict[str, Any]:
    """Combine concentration / correlation / drawdown into one soft multiplier."""
    caps = {
        "board_cap_pct": float(setting("portfolio_board_cap_pct", 50.0) or 50.0),
        "corr_cap": float(setting("portfolio_corr_cap", 0.75) or 0.75),
        "dd_cap_pct": float(setting("portfolio_dd_cap_pct", 8.0) or 8.0),
        "dd_hard_pct": float(setting("portfolio_dd_hard_pct", 15.0) or 15.0),
    }
    notes: list[str] = []
    mult = 1.0
    level = "ok"
    exposure, total_value = board_exposure(positions or [], board_of=board_of)
    if exposure:
        board, share = max(exposure.items(), key=lambda kv: kv[1])
        if share > caps["board_cap_pct"]:
            mult *= 0.8
            level = "warn"
            notes.append(f"{board}占仓{share:.0f}%超过上限{caps['board_cap_pct']:.0f}%·降档")
    corr = average_correlation(closes_by_code or {})
    if corr is not None and corr > caps["corr_cap"]:
        mult *= 0.85
        level = "warn"
        notes.append(f"持仓平均相关{corr:.2f}偏高·同涨同跌风险")
    dd = _num(drawdown_pct)
    if dd is not None and dd > 0:
        if dd >= caps["dd_hard_pct"]:
            mult *= 0.4
            level = "danger"
            notes.append(f"组合回撤{dd:.1f}%触及硬上限{caps['dd_hard_pct']:.0f}%·只减不加")
        elif dd >= caps["dd_cap_pct"]:
            mult *= 0.6
            level = "warn"
            notes.append(f"组合回撤{dd:.1f}%超过预算{caps['dd_cap_pct']:.0f}%·降档")
    mult = round(min(1.0, max(0.4, mult)), 3)
    return {
        "ok": True,
        "size_mult": mult,
        "level": level,
        "notes": notes,
        "board_exposure": exposure,
        "total_value": total_value,
        "avg_corr": corr,
        "drawdown_pct": dd,
        "caps": caps,
        "note": " · ".join(notes) if notes else "组合层无约束触发",
    }