"""Pick the session's live mainline board and a matching ETF vehicle."""

from __future__ import annotations

from typing import Any

from market_desk.config import (
    MAINLINE_ETF_RULES,
    MAINLINE_HOLD_SWITCH_MULT,
    MAINLINE_THEME_GROUPS,
    MAINLINE_THEME_SWITCH_MULT,
)
from market_desk.settings import setting


def theme_key(board_name: str | None) -> str:
    """Return a canonical theme id so sibling boards share sticky identity.

    Longer keyword hits win so「通信线缆」maps to 通信 rather than a short miss.
    """
    text = (board_name or "").strip()
    if not text:
        return ""
    best: tuple[int, str] | None = None
    for group in MAINLINE_THEME_GROUPS:
        canon = group[0]
        for key in group:
            if key and key in text:
                score = len(key)
                if best is None or score > best[0]:
                    best = (score, canon)
    return best[1] if best else text


def same_theme(a: str | None, b: str | None) -> bool:
    """Return True when two board names belong to the same theme family."""
    ka = theme_key(a)
    kb = theme_key(b)
    return bool(ka and kb and ka == kb)


def pick_mainline(
    hot: list[dict[str, Any]] | None,
    sticky_name: str | None = None,
    margin: float | None = None,
    *,
    sticky_held_seconds: float | None = None,
) -> dict[str, Any] | None:
    """Choose the live mainline from hot industry cards, then concepts.

    When ``sticky_name`` is still in the pool, keep it unless the raw leader's
    score beats it by ``margin`` (hysteresis against board-score flicker).
    Same-theme challengers (煤炭↔动力煤) and hold-window flips need a larger gap.
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
        # Sticky left the hot pool: prefer a same-theme successor when present.
        themed = [b for b in pool if same_theme(sticky, b.get("name"))]
        if themed:
            return max(themed, key=mainline_score)
        return leader
    if (leader.get("name") or "") == sticky:
        return leader

    need = float(margin) if margin is not None else float(setting("sticky_margin", 12.0))
    lead_s = mainline_score(leader)
    hold_s = mainline_score(incumbent)
    # Same theme: treat as continuity, not a "mainline change", unless clearly stronger
    # or the incumbent is already fading.
    if same_theme(sticky, leader.get("name")):
        if (incumbent.get("status") or "") == "退潮" and (leader.get("status") or "") != "退潮":
            return leader
        theme_need = need * float(MAINLINE_THEME_SWITCH_MULT)
        if lead_s >= hold_s + theme_need:
            return leader
        return incumbent

    # Fresh sticky hold: demand a clearer breakout before flipping live.
    hold_min = float(setting("switch_min_seconds", 300) or 0)
    if (
        sticky_held_seconds is not None
        and hold_min > 0
        and sticky_held_seconds < hold_min
    ):
        need = need * float(MAINLINE_HOLD_SWITCH_MULT)

    if lead_s >= hold_s + need:
        return leader
    return incumbent


def pick_side_mainline(
    hot: list[dict[str, Any]] | None,
    main: dict[str, Any] | None,
    *,
    max_gap: float | None = None,
) -> dict[str, Any] | None:
    """Return a competitive runner-up board for observation only.

    Surfaced when its score stays within ``max_gap`` of the live mainline and it
    is not already in退潮. Never replaces the primary mainline.
    """
    if not main:
        return None
    main_name = str(main.get("name") or "").strip()
    if not main_name:
        return None
    gap_limit = (
        float(max_gap)
        if max_gap is not None
        else float(setting("side_mainline_gap", 12.0) or 0.0)
    )
    if gap_limit <= 0:
        return None
    boards = list(hot or [])
    industries = [b for b in boards if b.get("kind") == "industry"]
    pool = industries or boards
    if len(pool) < 2:
        return None
    main_score = mainline_score(main)
    ranked = sorted(pool, key=mainline_score, reverse=True)
    for board in ranked:
        name = str(board.get("name") or "").strip()
        if not name or name == main_name:
            continue
        # Skip same-theme runner-ups; they are not a real side branch.
        if same_theme(main_name, name):
            continue
        if (board.get("status") or "") == "退潮":
            continue
        sc = mainline_score(board)
        if main_score - sc > gap_limit:
            continue
        out = dict(board)
        out["score"] = round(sc, 1)
        out["main_score"] = round(main_score, 1)
        out["score_gap"] = round(main_score - sc, 1)
        return out
    return None


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
    """Return (symbol, code, etf_name) for the best matching keyword rule.

    Prefers the longest keyword hit so short keys like「电子」do not beat a more
    specific board name match when both fire.
    """
    text = board_name or ""
    if not text:
        return None
    best: tuple[int, tuple[str, str, str]] | None = None
    for keys, spec in MAINLINE_ETF_RULES:
        for key in keys:
            if key and key in text:
                score = len(key)
                if best is None or score > best[0]:
                    best = (score, spec)
    return best[1] if best else None


def etf_spec_soft_fallback(board_name: str) -> tuple[str, str, str] | None:
    """Looser map when exact keyword miss: prefer 3-char stems, else 2-char overlap."""
    text = board_name or ""
    if not text:
        return None
    if etf_spec_for_name(text):
        return etf_spec_for_name(text)
    best: tuple[int, tuple[str, str, str]] | None = None
    for keys, spec in MAINLINE_ETF_RULES:
        for key in keys:
            if len(key) < 2:
                continue
            score = 0
            if len(key) >= 3 and (key[:3] in text or (len(text) >= 3 and text[:3] in key)):
                score = 3
            elif text[:2] in key or key[:2] in text:
                score = 2
            if score and (best is None or score > best[0]):
                best = (score, spec)
    return best[1] if best else None


def mainline_score(board: dict[str, Any]) -> float:
    """Score a hot board for mainline ranking, with multi-day persistence."""
    status = board.get("status") or ""
    zt_n = int(board.get("zt_n") or 0)
    rank = {
        "确认中": 50.0,
        "尖峰禁追": 28.0,
        "观察": 12.0,
        "退潮": -25.0,
    }.get(status, 0.0)
    # Thin「确认中」(only 2 limit-ups) is fragile — dampen so niche spikes lose to real themes.
    if status == "确认中" and zt_n < 3:
        rank = 36.0
    hist = list(board.get("hist") or [])
    # Reward boards that stayed hot across recent sessions (anti one-day wonder).
    persist = 0
    for row in hist[-5:]:
        zt_h = int(row.get("zt_n") or 0)
        pct = float(row.get("pct") or 0)
        if zt_h >= 2 or pct >= 1.5:
            persist += 1
    today_hot = zt_n >= 2 or float(board.get("pct") or 0) >= 1.5
    if today_hot:
        persist += 1
    # Penalize boards with many broken seals / late first seals (weaker quality).
    zb_pen = float(board.get("zb_n") or 0) * 3.0
    explode_pen = min(float(board.get("explode_sum") or 0), 8.0) * 1.5
    late_pen = float(board.get("late_seal_n") or 0) * 2.0
    # Ladder completeness inside the board: reward fill, cut broken high boards.
    if board.get("ladder_gap"):
        ladder_adj = -8.0
    else:
        ladder_adj = min(float(board.get("ladder_fill") or 0) / 100.0, 1.0) * 6.0
    ladder_adj += min(int(board.get("ge2") or 0), 4) * 1.5
    return (
        rank
        + float(zt_n) * 6.0
        + float(board.get("pct") or 0)
        + float(board.get("focus") or 0) * 0.15
        + min(persist, 6) * 3.0
        + ladder_adj
        - zb_pen
        - explode_pen
        - late_pen
    )
