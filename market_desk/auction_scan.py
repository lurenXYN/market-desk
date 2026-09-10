"""Standalone call-auction strategy board (observe-only; never feeds desk buys)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.filters import is_limit_up, limit_up_threshold, normalize_code
from market_desk.numbers import num
from market_desk.session import session_segment


def build_auction_strategy(
    *,
    yesterday_zt: list[dict[str, Any]] | None,
    quotes: list[dict[str, Any]] | None,
    zt_today: list[dict[str, Any]] | None,
    zb_today: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    trading_day: bool = True,
) -> dict[str, Any]:
    """Build tiered yesterday-ZT auction cards for the dedicated tab.

    Completely isolated from verdict / recommend / ready. Labels like「抢筹」
    are strategy-board jargon only — they never unlock desk buys.
    """
    del zb_today  # Reserved for broken-seal context in a later pass.
    clock = now or datetime.now()
    seg = session_segment(clock)
    seg_key = str(seg.get("key") or "")
    if not trading_day or seg_key == "closed":
        run_state = "未运行"
        run_note = "非交易时段；可看昨停名单骨架，开幅以昨收后数据为准"
    elif seg_key == "auction":
        run_state = "竞价中"
        run_note = "9:15–9:30 集合竞价观察；本页不改作战台买卖结论"
    elif seg_key in ("open30", "open_mute", "morning", "afternoon"):
        run_state = "已开盘"
        run_note = "开盘后仍展示昨停跟风分档，仅供对照，不当现买清单"
    else:
        run_state = "未运行"
        run_note = "等待下一交易日竞价"

    qmap = _quote_map(quotes)
    zt_map = {
        normalize_code(r.get("code")): r
        for r in (zt_today or [])
        if normalize_code(r.get("code"))
    }
    yzt = list(yesterday_zt or [])
    rows: list[dict[str, Any]] = []
    for raw in yzt:
        code = normalize_code(raw.get("code"))
        if not code:
            continue
        name = str(raw.get("name") or code)
        q = qmap.get(code) or {}
        open_pct = num(q.get("open_pct"))
        last_pct = num(q.get("pct"))
        # Prefer live/open; fall back to yesterday-ZT follow-through pct.
        pct = open_pct if open_pct is not None else num(raw.get("pct"))
        if pct is None:
            pct = last_pct
        if pct is None:
            continue
        boards_y = int(raw.get("boards_yesterday") or 0)
        in_zt = code in zt_map
        zt_row = zt_map.get(code) or {}
        open_type, advice, score = _classify(
            name=name,
            pct=float(pct),
            boards_yesterday=boards_y,
            in_zt_today=in_zt,
        )
        if score <= 0:
            continue
        seal_ratio = _seal_ratio(zt_row)
        tier = _tier_for(open_type=open_type, advice=advice, score=score, boards_y=boards_y)
        rows.append(
            {
                "code": code,
                "name": name,
                "open_pct": round(float(pct), 2),
                "last_pct": round(float(last_pct), 2) if last_pct is not None else None,
                "open_type": open_type,
                "seal_ratio": seal_ratio,
                "advice": advice,
                "judge": "保" if advice in ("抢筹", "关注") else "剔",
                "boards_yesterday": boards_y,
                "industry": str(raw.get("industry") or zt_row.get("industry") or ""),
                "in_zt_today": in_zt,
                "tier": tier,
                "score": score,
                "price": num(q.get("price")),
            }
        )

    tiers = {
        "double": [r for r in rows if r["tier"] == "double"],
        "top": [r for r in rows if r["tier"] == "top"],
        "second": [r for r in rows if r["tier"] == "second"],
    }
    for key in tiers:
        tiers[key].sort(key=lambda x: (-float(x.get("score") or 0), -float(x.get("open_pct") or 0)))
        tiers[key] = tiers[key][:12]

    return {
        "ok": True,
        "standalone": True,
        "title": "集合竞价策略",
        "run_state": run_state,
        "run_note": run_note,
        "segment": seg.get("label") or seg_key,
        "yzt_n": len(yzt),
        "scan_n": sum(len(v) for v in tiers.values()),
        "tiers": [
            {
                "id": "double",
                "label": "双确认",
                "hint": "昨连板/今日已封 + 高开形态交叉",
                "items": tiers["double"],
            },
            {
                "id": "top",
                "label": "顶级（抢筹优先）",
                "hint": "高开强票；一字仍标不买，只列出来防追",
                "items": tiers["top"],
            },
            {
                "id": "second",
                "label": "次级（可排单）",
                "hint": "开幅略低一档，可观察排单，不接作战台 ready",
                "items": tiers["second"],
            },
        ],
        "disclaimer": "本页与作战台买卖结论隔离；「抢筹」仅策略板用语，不会把顶栏改成可买入。",
    }


def _quote_map(quotes: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in quotes or []:
        code = normalize_code(row.get("code"))
        if code:
            out[code] = row
    return out


def _seal_ratio(zt_row: dict[str, Any]) -> float | None:
    """Limit-seal amount / turnover when both sides exist."""
    fund = num(zt_row.get("seal_fund") or zt_row.get("fund"))
    amount = num(zt_row.get("amount"))
    if fund is None or amount is None or amount <= 0:
        return None
    return round(float(fund) / float(amount), 2)


def _classify(
    *,
    name: str,
    pct: float,
    boards_yesterday: int,
    in_zt_today: bool,
) -> tuple[str, str, float]:
    """Return (open_type, advice, score). Score 0 means drop from board."""
    thr = limit_up_threshold(name)
    near = thr - 0.15
    soft = thr - 1.0
    if pct >= thr or (pct >= near and is_limit_up(name, pct)):
        # Hard one-word / sealed open — list but never「抢筹」.
        score = 40.0 + min(boards_yesterday, 5) * 3.0 + (8.0 if in_zt_today else 0.0)
        return "一字封死", "不买", score
    if pct >= soft:
        score = 55.0 + min(boards_yesterday, 5) * 4.0 + (12.0 if in_zt_today else 0.0)
        return "一键T板", "关注", score
    if pct >= 5.0:
        score = 70.0 + (pct - 5.0) * 2.0 + min(boards_yesterday, 5) * 5.0
        if in_zt_today:
            score += 15.0
        return "非涨停高开", "抢筹", score
    if pct >= 3.0:
        score = 35.0 + (pct - 3.0) * 3.0 + min(boards_yesterday, 4) * 3.0
        return "温和高开", "关注", score
    return "偏弱", "观察", 0.0


def _tier_for(
    *,
    open_type: str,
    advice: str,
    score: float,
    boards_y: int,
) -> str:
    """Map classified rows into double / top / second buckets."""
    if open_type == "一字封死":
        return "top"
    if advice == "抢筹" and (boards_y >= 2 or score >= 85):
        return "double"
    if open_type == "一键T板" and (boards_y >= 2 or score >= 70):
        return "double"
    if advice == "抢筹" or open_type in ("一键T板", "非涨停高开"):
        return "top" if score >= 60 else "second"
    if advice == "关注":
        return "second"
    return "second"
