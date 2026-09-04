"""Pick the session's live mainline board and a matching ETF vehicle."""

from __future__ import annotations

from typing import Any

from market_desk.config import MAINLINE_ETF_RULES, MAINLINE_STICKY_MARGIN


def pick_mainline(
    hot: list[dict[str, Any]] | None,
    sticky_name: str | None = None,
    margin: float | None = None,
) -> dict[str, Any] | None:
    """Choose the live mainline from hot industry cards, then concepts.

    When ``sticky_name`` is still in the pool, keep it unless the raw leader's
    score beats it by ``margin`` (hysteresis against board-score flicker).
    """
    boards = list(hot or [])
    industries = [b for b in boards if b.get("kind") == "industry"]
    pool = industries or boards
    if not pool:
        return None
    leader = max(pool, key=mainline_score)
    sticky = (sticky_name or "").strip()
    if not sticky:
        return leader
    incumbent = next((b for b in pool if (b.get("name") or "") == sticky), None)
    if not incumbent:
        return leader
    if (leader.get("name") or "") == sticky:
        return leader
    need = MAINLINE_STICKY_MARGIN if margin is None else float(margin)
    if mainline_score(leader) >= mainline_score(incumbent) + need:
        return leader
    return incumbent


def match_mainline_etf(
    board_name: str,
    etfs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the ETF quote that maps to a sector name, if configured."""
    spec = etf_spec_for_name(board_name)
    if not spec:
        return None
    _, code, name = spec
    quote = next((x for x in etfs if x.get("code") == code), None)
    if quote:
        return dict(quote)
    return {"code": code, "name": name, "price": None, "pct": None, "low": None, "high": None}


def etf_spec_for_name(board_name: str) -> tuple[str, str, str] | None:
    """Return (symbol, code, etf_name) for the first matching keyword rule."""
    text = board_name or ""
    for keys, spec in MAINLINE_ETF_RULES:
        if any(k in text for k in keys):
            return spec
    return None


def mainline_score(board: dict[str, Any]) -> float:
    """Score a hot board for mainline ranking."""
    status = board.get("status") or ""
    rank = {
        "确认中": 50.0,
        "尖峰禁追": 28.0,
        "观察": 12.0,
        "退潮": -25.0,
    }.get(status, 0.0)
    return (
        rank
        + float(board.get("zt_n") or 0) * 5.0
        + float(board.get("pct") or 0)
        + float(board.get("focus") or 0) * 0.15
    )
