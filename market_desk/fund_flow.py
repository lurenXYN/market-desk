"""Sector money-flow monitor with a soft desk feed.

East Money day / ~5d / ~10d boards only. Today's net inflow soft-feeds
mainline scoring and sell urgency (not a hard gate).
"""

from __future__ import annotations

from typing import Any

# id, label, source(api), hint
PERIODS: tuple[tuple[str, str, str, str], ...] = (
    ("em_day", "今日", "api", "东财今日主力净流入（每次刷新重拉）"),
    ("em_5d", "近5日", "api", "东财近5日累计（打开资金页补齐）"),
    ("em_10d", "近10日", "api", "东财近10日累计（打开资金页补齐）"),
)


def build_fund_flow_board(
    api_periods: dict[str, dict[str, list[dict[str, Any]]]] | None,
    *,
    trade_date: str,
    stored_dates: list[str] | None = None,
    stored_rows: list[dict[str, Any]] | None = None,
    top_n: int = 15,
) -> dict[str, Any]:
    """Assemble East Money period boards and sticky-leader compare.

    ``stored_dates`` / ``stored_rows`` are accepted for call-site compatibility
    but ignored (local SQLite accumulation was removed).
    """
    del stored_dates, stored_rows
    day = str(trade_date or "")[:10]
    packed: dict[str, Any] = {}

    # API periods (keys: day / week / month from engine → remap).
    api_map = {
        "em_day": (api_periods or {}).get("day") or (api_periods or {}).get("em_day") or {},
        "em_5d": (api_periods or {}).get("week") or (api_periods or {}).get("em_5d") or {},
        "em_10d": (api_periods or {}).get("month") or (api_periods or {}).get("em_10d") or {},
    }
    for pid in ("em_day", "em_5d", "em_10d"):
        blob = api_map[pid]
        packed[pid] = _pack_period(
            pid,
            industry=blob.get("industry") or [],
            concept=blob.get("concept") or [],
            top_n=top_n,
            extra={"dates": [day] if pid == "em_day" else [], "day_n": 1 if pid == "em_day" else None},
        )

    return {
        "ok": any(
            bool((packed.get(p) or {}).get("totals", {}).get("industry_n"))
            or bool((packed.get(p) or {}).get("totals", {}).get("concept_n"))
            for p, *_ in PERIODS
        ),
        "standalone": True,
        "disclaimer": (
            "东财主力/超大单净流入作大资金代理；仅观察。"
            "今日净流入会软性影响主线打分与卖侧紧迫度（非硬闸门）。"
        ),
        "note": "",
        "default_period": "em_day",
        "period_meta": [
            {"id": p, "label": lab, "source": src, "hint": hint}
            for p, lab, src, hint in PERIODS
        ],
        "periods": packed,
        "diffs": [],
        "compare": _cross_period_compare(packed),
        **_flat_alias(packed.get("em_day") or {}),
    }


def _pack_period(
    pid: str,
    *,
    industry: list[dict[str, Any]],
    concept: list[dict[str, Any]],
    top_n: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = next((x for x in PERIODS if x[0] == pid), None)
    label = meta[1] if meta else pid
    source = meta[2] if meta else ""
    hint = meta[3] if meta else ""
    ind = _rank_rows(industry)
    con = _rank_rows(concept)
    out = {
        "id": pid,
        "label": label,
        "source": source,
        "hint": hint,
        "industry_in": ind[:top_n],
        "industry_out": list(reversed(ind[-top_n:])) if ind else [],
        "concept_in": con[:top_n],
        "concept_out": list(reversed(con[-top_n:])) if con else [],
        "spotlight": _spotlight(ind, con),
        "stats": _period_stats(ind, con, top_n=top_n),
        "industry_rank": ind[:40],
        "concept_rank": con[:40],
        "totals": {
            "industry_n": len(ind),
            "concept_n": len(con),
            "industry_in_yi": _sum_main(ind[:top_n]),
            "industry_out_yi": _sum_main(ind[-top_n:]) if ind else 0.0,
        },
    }
    if extra:
        out.update(extra)
    return out


def _flat_alias(day: dict[str, Any]) -> dict[str, Any]:
    if not day:
        return {
            "industry_in": [],
            "industry_out": [],
            "concept_in": [],
            "concept_out": [],
            "spotlight": [],
            "totals": {},
            "stats": {},
        }
    return {
        "industry_in": day.get("industry_in") or [],
        "industry_out": day.get("industry_out") or [],
        "concept_in": day.get("concept_in") or [],
        "concept_out": day.get("concept_out") or [],
        "spotlight": day.get("spotlight") or [],
        "totals": day.get("totals") or {},
        "stats": day.get("stats") or {},
    }


def _period_stats(
    industry: list[dict[str, Any]],
    concept: list[dict[str, Any]],
    *,
    top_n: int,
) -> dict[str, Any]:
    """Summarize breadth / concentration / big-money share for one period."""
    ind_in = industry[:top_n]
    ind_out = list(reversed(industry[-top_n:])) if industry else []
    in_yi = _sum_main(ind_in)
    out_yi = _sum_main(ind_out)
    pos_n = sum(1 for r in industry if float(r.get("main_yi") or 0) > 0)
    neg_n = sum(1 for r in industry if float(r.get("main_yi") or 0) < 0)
    top5 = industry[:5]
    top5_yi = _sum_main(top5)
    pos_total = sum(float(r.get("main_yi") or 0) for r in industry if float(r.get("main_yi") or 0) > 0)
    conc = round(top5_yi / pos_total * 100.0, 1) if pos_total > 0 else None
    big_n = sum(1 for r in industry[:30] if r.get("big_money"))
    big_con = sum(1 for r in concept[:30] if r.get("big_money"))
    return {
        "industry_in_yi": in_yi,
        "industry_out_yi": out_yi,
        "industry_net_yi": round(in_yi + out_yi, 2),
        "pos_n": pos_n,
        "neg_n": neg_n,
        "breadth": round(pos_n / max(pos_n + neg_n, 1) * 100.0, 1),
        "top5_in_yi": top5_yi,
        "top5_share_pct": conc,
        "big_money_industry_n": big_n,
        "big_money_concept_n": big_con,
        "top_in_names": [str(r.get("name") or "") for r in ind_in[:5]],
        "top_out_names": [str(r.get("name") or "") for r in ind_out[:5]],
    }


def _cross_period_compare(packed: dict[str, Any]) -> dict[str, Any]:
    """Sticky leaders across East Money day / 5d / 10d."""
    def names(pid: str) -> list[str]:
        rows = ((packed.get(pid) or {}).get("industry_in") or [])[:5]
        return [str(r.get("name") or "") for r in rows if r.get("name")]

    day_in, d5, d10 = names("em_day"), names("em_5d"), names("em_10d")
    sticky_5 = sorted(set(day_in) & set(d5))
    sticky_10 = sorted(set(day_in) & set(d10))
    sticky_all = sorted(set(day_in) & set(d5) & set(d10))
    return {
        "day_top5": day_in,
        "week_top5": d5,
        "month_top5": d10,
        "sticky_day_week": sticky_5,
        "sticky_day_month": sticky_10,
        "sticky_all": sticky_all,
        "note": (
            f"东财 日&5日同榜 {len(sticky_5)} · 日&10日同榜 {len(sticky_10)} · "
            f"三榜同在 {len(sticky_all)}"
        ),
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
            "period": row.get("period") or "",
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
            "days": row.get("days"),
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


def flow_lookup(fund_flow: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Map board name → day flow row (main_yi, sticky flags) from fund_flow board."""
    out: dict[str, dict[str, Any]] = {}
    box = fund_flow or {}
    periods = box.get("periods") or {}
    day = periods.get("em_day") or {}
    for key in ("industry_rank", "concept_rank", "industry_in", "industry_out", "concept_in", "concept_out"):
        for row in day.get(key) or []:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            cur = out.get(name) or {}
            cur.update({k: row.get(k) for k in ("bk", "name", "main_yi", "main_pct", "big_money") if row.get(k) is not None})
            out[name] = cur
    compare = box.get("compare") or {}
    sticky = set(compare.get("sticky_day_week") or [])
    for name in sticky:
        if name in out:
            out[name]["sticky_week"] = True
        else:
            out[name] = {"name": name, "sticky_week": True, "main_yi": None}
    return out


def flow_score_adj(main_yi: float | None, *, sticky_week: bool = False) -> float:
    """Return mainline score nudge from day net inflow (亿元)."""
    from market_desk.config import (
        MAINLINE_FLOW_IN_ADJ,
        MAINLINE_FLOW_IN_YI,
        MAINLINE_FLOW_OUT_ADJ,
        MAINLINE_FLOW_OUT_YI,
        MAINLINE_FLOW_STICKY_BONUS,
    )

    adj = 0.0
    try:
        yi = float(main_yi) if main_yi is not None else None
    except (TypeError, ValueError):
        yi = None
    if yi is not None:
        if yi >= float(MAINLINE_FLOW_IN_YI):
            adj += float(MAINLINE_FLOW_IN_ADJ)
        elif yi <= float(MAINLINE_FLOW_OUT_YI):
            adj += float(MAINLINE_FLOW_OUT_ADJ)
    if sticky_week and (yi is None or yi >= 0):
        adj += float(MAINLINE_FLOW_STICKY_BONUS)
    return adj


def attach_boards_fund_flow(
    boards: list[dict[str, Any]] | None,
    fund_flow: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attach day main_yi / flow_adj onto hot-board cards for scoring and sells."""
    lookup = flow_lookup(fund_flow)
    out: list[dict[str, Any]] = []
    for board in boards or []:
        card = dict(board)
        name = str(card.get("name") or "").strip()
        hit = lookup.get(name) or {}
        # Fuzzy: substring match when exact name missing.
        if not hit and name:
            for key, row in lookup.items():
                if name in key or key in name:
                    hit = row
                    break
        main_yi = hit.get("main_yi")
        sticky = bool(hit.get("sticky_week"))
        card["main_yi"] = main_yi
        card["flow_sticky"] = sticky
        card["flow_adj"] = round(flow_score_adj(main_yi, sticky_week=sticky), 2)
        out.append(card)
    return out
