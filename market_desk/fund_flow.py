"""Sector money-flow monitor (observe-only).

Tracks East Money board-level 主力 / 超大单净流入 as a proxy for large
institutional / 「国家队口径」capital rotating across industries and concepts.
Does not feed desk buy/sell gates.
"""

from __future__ import annotations

from typing import Any


def build_fund_flow_board(
    industry_rows: list[dict[str, Any]] | None,
    concept_rows: list[dict[str, Any]] | None,
    *,
    top_n: int = 15,
) -> dict[str, Any]:
    """Assemble inflow/outflow boards ranked by main-force and super-large orders."""
    industry = _rank_rows(industry_rows or [])
    concept = _rank_rows(concept_rows or [])
    return {
        "ok": bool(industry or concept),
        "standalone": True,
        "disclaimer": (
            "板块主力/超大单净流入来自东财公开榜，作「大资金」代理观察；"
            "非官方国家队持仓披露，不改作战台买卖结论。"
        ),
        "note": "按主力净流入排序；超大单净流入偏高时标记为大资金主导。",
        "industry_in": industry[:top_n],
        "industry_out": list(reversed(industry[-top_n:])) if industry else [],
        "concept_in": concept[:top_n],
        "concept_out": list(reversed(concept[-top_n:])) if concept else [],
        "spotlight": _spotlight(industry, concept),
        "totals": {
            "industry_n": len(industry),
            "concept_n": len(concept),
            "industry_in_yi": _sum_main(industry[:top_n]),
            "industry_out_yi": _sum_main(industry[-top_n:]) if industry else 0.0,
        },
    }


def _rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        main = row.get("main_net")
        if main is None:
            continue
        try:
            main_f = float(main)
        except (TypeError, ValueError):
            continue
        super_net = _f(row.get("super_net"))
        large_net = _f(row.get("large_net"))
        main_pct = _f(row.get("main_pct"))
        item = {
            "bk": row.get("bk") or "",
            "name": row.get("name") or "",
            "kind": row.get("kind") or "",
            "pct": _round(row.get("pct"), 2),
            "main_net": round(main_f, 0),
            "main_yi": round(main_f / 1e8, 2),
            "main_pct": _round(main_pct, 2),
            "super_net": round(super_net, 0) if super_net is not None else None,
            "super_yi": round(super_net / 1e8, 2) if super_net is not None else None,
            "large_net": round(large_net, 0) if large_net is not None else None,
            "large_yi": round(large_net / 1e8, 2) if large_net is not None else None,
            "leader_name": row.get("leader_name") or "",
            "leader_code": row.get("leader_code") or "",
            "big_money": _is_big_money(main_f, super_net),
        }
        cleaned.append(item)
    cleaned.sort(key=lambda x: float(x.get("main_net") or 0.0), reverse=True)
    return cleaned


def _is_big_money(main: float, super_net: float | None) -> bool:
    """Flag boards where super-large orders dominate the main-force print."""
    if super_net is None:
        return abs(main) >= 3e8
    if abs(main) < 1e8:
        return False
    # Super-large contributes a large share of |main|, or absolute super print is big.
    if abs(super_net) >= 2e8:
        return True
    if abs(main) > 0 and abs(super_net) / abs(main) >= 0.55:
        return True
    return False


def _spotlight(
    industry: list[dict[str, Any]],
    concept: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pick a short list of big-money led boards across industry + concept."""
    pool = [r for r in (industry[:20] + concept[:20]) if r.get("big_money")]
    pool.sort(key=lambda x: float(x.get("super_yi") or x.get("main_yi") or 0), reverse=True)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in pool:
        key = str(row.get("bk") or row.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= 8:
            break
    return out


def _sum_main(rows: list[dict[str, Any]]) -> float:
    return round(sum(float(r.get("main_yi") or 0) for r in rows), 2)


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _round(v: Any, n: int) -> float | None:
    f = _f(v)
    return round(f, n) if f is not None else None
