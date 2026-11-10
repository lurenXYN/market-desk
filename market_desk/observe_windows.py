"""Observation-window due checklist for agent-pack / ops.

Reports whether each soft shadow ledger has accrued enough trading days to
revisit (news / defense / env_gate / NP …). Never changes knobs — only hints
「维持记账」vs「可复核」.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from market_desk.calendar import is_trading_day, today_cn


def _as_date(raw: str | date | None) -> date | None:
    """Parse YYYY-MM-DD or pass through a ``date``."""
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    s = str(raw or "")[:10]
    if len(s) != 10:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def count_trading_days(since: str | date | None, until: str | date | None = None) -> int:
    """Inclusive trading-day count from ``since`` through ``until`` (default today)."""
    start = _as_date(since)
    end = _as_date(until) or _as_date(today_cn())
    if start is None or end is None or end < start:
        return 0
    n = 0
    d = start
    # Cap at ~2 years of calendar walk.
    for _ in range(800):
        if d > end:
            break
        if is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def build_observe_windows(*, asof: str | date | None = None) -> dict[str, Any]:
    """Build due/not-due rows for soft observation windows.

    Returns:
        Dict with ``ok``, ``asof``, and ``items`` — each item has since / need /
        elapsed / due / verdict_hint. Soft observation only.
    """
    from market_desk.config import (
        DEFENSE_SHADOW_SINCE,
        ENV_GATE_LIT_MIN_DAYS,
        ENV_GATE_SHADOW_SINCE,
        NEWS_RADAR_SHADOW_SINCE,
        NP_GATE_MIN_DAYS,
        NP_SHADOW_SINCE,
        REGIME_SHADOW_SINCE,
    )

    end = _as_date(asof) or _as_date(today_cn())
    end_s = end.isoformat() if end else ""

    specs = [
        {
            "id": "news_radar",
            "title": "新闻雷达效果窗",
            "since": str(NEWS_RADAR_SHADOW_SINCE)[:10],
            "need_days": 7,
            "note": "手标+影子/超额样本够再判推送保留；不改买闸",
        },
        {
            "id": "defense",
            "title": "逆境档位影子",
            "since": str(DEFENSE_SHADOW_SINCE)[:10],
            "need_days": 10,
            "note": "满窗后再议 DEFENSE_SIZE_LIVE；当前只旁注",
        },
        {
            "id": "env_gate",
            "title": "环境闸影子",
            "since": str(ENV_GATE_SHADOW_SINCE)[:10],
            "need_days": int(ENV_GATE_LIT_MIN_DAYS or 10),
            "note": "满窗且 friendly 明显优于 hostile 才议 env_gate_live",
        },
        {
            "id": "np_plan",
            "title": "计划价 NP 影子",
            "since": str(NP_SHADOW_SINCE)[:10],
            "need_days": int(NP_GATE_MIN_DAYS or 10),
            "note": "配对日够 + 超额门槛见 TODO；未过线维持计划价",
        },
        {
            "id": "regime",
            "title": "大盘尺影子",
            "since": str(REGIME_SHADOW_SINCE)[:10],
            "need_days": 10,
            "note": "尺永不因自身关 ready；只作对照",
        },
    ]

    items: list[dict[str, Any]] = []
    due_n = 0
    for sp in specs:
        elapsed = count_trading_days(sp["since"], end_s)
        need = int(sp["need_days"])
        due = elapsed >= need
        if due:
            due_n += 1
        items.append(
            {
                "id": sp["id"],
                "title": sp["title"],
                "since": sp["since"],
                "need_days": need,
                "elapsed_trade_days": elapsed,
                "due": due,
                "verdict_hint": "可复核" if due else "维持记账",
                "note": sp["note"],
            }
        )
    return {
        "ok": True,
        "asof": end_s,
        "due_n": due_n,
        "n": len(items),
        "items": items,
        "note": "到期只表示可以对照预登记门槛复核，不自动改闸/仓位。",
    }
