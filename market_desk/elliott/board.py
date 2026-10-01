"""Entry point that ranks every scenario for the index readout."""

from __future__ import annotations

from typing import Any

from market_desk.elliott.catalog import _SCENARIOS
from market_desk.elliott.pivots import (
    _normalize_bars,
    _structure_snapshot,
    _zigzag_pivots,
)
from market_desk.elliott.indicators import _build_indicators
from market_desk.elliott.fit import _invalidation, _key_levels, _score_fit, _subwave_draft
from market_desk.elliott.timing import _scenario_levels, _scenario_turns, _timing_summary
from market_desk.elliott.chart import _build_elliott_chart


def build_elliott_scenarios(
    bars: list[dict[str, Any]] | None,
    *,
    index_name: str = "上证指数",
    index_code: str = "000001",
    last_price: float | None = None,
) -> dict[str, Any]:
    """Score every catalogued Elliott regime against recent index swings.

    ``bars`` items need at least ``close``; ``high``/``low``/``date`` preferred.
    """
    series = _normalize_bars(bars)
    if len(series) < 30:
        return {
            "ok": False,
            "standalone": True,
            "index_name": index_name,
            "index_code": index_code,
            "note": "日线样本不足（需约30根以上），暂无法做浪型情景",
            "pivots": [],
            "structure": {},
            "scenarios": [],
            "primary": None,
            "disclaimer": "波浪计数多解，仅观察；不改作战台买卖结论。",
        }

    pivots = _zigzag_pivots(series, min_move_pct=2.2)
    last = float(last_price) if last_price and last_price > 0 else float(series[-1]["close"])
    structure = _structure_snapshot(pivots, last, series)
    scored: list[dict[str, Any]] = []
    for tpl in _SCENARIOS:
        fit, why = _score_fit(tpl["id"], structure, pivots, last)
        inv = _invalidation(tpl["id"], structure, last)
        levels = _key_levels(structure, last)
        scored.append(
            {
                **tpl,
                "fit": fit,
                "fit_note": why,
                "invalidation": inv,
                "levels": levels,
                "path": tpl["next"],
                "watch": tpl["risk"],
            }
        )
    scored.sort(key=lambda x: (-int(x.get("fit") or 0), str(x.get("id") or "")))
    indicators = _build_indicators(series)
    top = scored[:5]
    fine_pivots = _zigzag_pivots(series, min_move_pct=1.05)
    fine_st = _structure_snapshot(fine_pivots, last, series)
    for i, row in enumerate(top):
        row["turns"] = _scenario_turns(row["id"], structure, pivots, series, last, indicators)
        row["levels"] = _scenario_levels(row["id"], structure, last)
        row["timing"] = _timing_summary(row["id"], indicators, structure)
        row["levels_note"] = "点位按距现价排序：↑阻力 / ↓支撑；均为结构观察位，非变盘日必达价。"
        # One-layer internal draft only for Top1 / Top2.
        if i < 2:
            row["subwaves"] = _subwave_draft(
                str(row.get("id") or ""),
                fine_st,
                fine_pivots,
                last,
            )
        else:
            row["subwaves"] = None
    primary = top[0] if top else None
    next_turn: dict[str, Any] | None = None
    if primary and primary.get("turns"):
        for t in primary.get("turns") or []:
            if isinstance(t, dict) and t.get("bars_ahead") and int(t.get("bars_ahead") or 0) > 0:
                next_turn = t
                break
    return {
        "ok": True,
        "standalone": True,
        "index_name": index_name,
        "index_code": index_code,
        "last": round(last, 2),
        "next_turn_date": next_turn.get("date") if next_turn else None,
        "next_turn_countdown": int(next_turn.get("bars_ahead") or 0) if next_turn else None,
        "next_turn_conf": next_turn.get("conf") if next_turn else None,
        "bars": len(series),
        "bar_from": series[0].get("date") or "",
        "bar_to": series[-1].get("date") or "",
        "pivot_n": len(pivots),
        "fine_pivot_n": len(fine_pivots),
        "structure": structure,
        "pivots": pivots[-8:],
        "indicators": {
            "macd": indicators.get("macd_snap"),
            "rsi": indicators.get("rsi_snap"),
            "divergence": indicators.get("divergence"),
        },
        "scenarios": top,
        "catalog_n": len(scored),
        "chart": _build_elliott_chart(
            series,
            pivots,
            fine_pivots,
            last,
            primary=primary,
            scenarios=top,
        ),
        "primary": {
            "id": primary.get("id"),
            "title": primary.get("title"),
            "fit": primary.get("fit"),
            "wave": primary.get("wave"),
            "family": primary.get("family"),
            "subwave": ((primary.get("subwaves") or {}).get("primary") or {}).get("label"),
        }
        if primary
        else None,
        "note": (
            f"{index_name} 近 {len(series)} 日"
            f"（{series[0].get('date') or '?'} → {series[-1].get('date') or '?'}）"
            f" · 枢轴 {len(pivots)} 个 · 细枢轴 {len(fine_pivots)} 个 · "
            f"当前价 {round(last, 2)}；仅展示契合度最高的 5 个浪型情景；"
            f"Top1/Top2 附一层子浪草稿"
        ),
        "disclaimer": (
            "艾略特波浪天然多解。本页只列 Top5；Top1/Top2 可展开「内部结构草稿」"
            "（更细锯齿上一层子浪，多解且不保证）；"
            "变盘时间窗按浪型时钟分开计算，结构点位单独列出（距现价%），"
            "二者不捆绑成「某日必达某价」；参考 MACD/RSI；不改作战台可买入/ready。"
        ),
    }
