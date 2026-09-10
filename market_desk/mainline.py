"""Pick the session's live mainline board and a matching ETF vehicle."""

from __future__ import annotations

from typing import Any

from market_desk.config import (
    MAINLINE_ETF_RULES,
    MAINLINE_FADE_SWITCH_MULT,
    MAINLINE_HOLD_SWITCH_MULT,
    MAINLINE_LEADER_MAINBOARD_BONUS,
    MAINLINE_LEADER_PAIR_BONUS,
    MAINLINE_LEADER_STRUCT_BONUS,
    MAINLINE_THEME_GROUPS,
    MAINLINE_THEME_SWITCH_MULT,
)
from market_desk.filters import is_main_board
from market_desk.settings import setting


def leader_structure_adj(board: dict[str, Any] | None) -> float:
    """Reward leader+follower structure; never reward tip-height dragons.

    - 尖峰 / leader_boards≥3 → 0 (identity only; status already soft-penalizes)
    - 1–2板龙且有卡位龙 / 二板家数≥2 / 涨停≥3 → structure bonus
    - 二板龙 + 跟风额外加分；主板龙再小幅加分
    """
    b = board or {}
    status = str(b.get("status") or "")
    lb = int(b.get("leader_boards") or 0)
    if status == "尖峰禁追" or lb >= 3:
        return 0.0
    if lb < 1:
        return 0.0
    has_slot = bool(b.get("slot_name") or b.get("slot_code"))
    ge2 = int(b.get("ge2") or 0)
    zt_n = int(b.get("zt_n") or 0)
    if not (has_slot or ge2 >= 2 or zt_n >= 3):
        return 0.0
    adj = float(MAINLINE_LEADER_STRUCT_BONUS)
    if lb == 2 and (has_slot or ge2 >= 2):
        adj += float(MAINLINE_LEADER_PAIR_BONUS)
    code = str(b.get("leader_code") or "")
    if code and is_main_board(code):
        adj += float(MAINLINE_LEADER_MAINBOARD_BONUS)
    return adj


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
    # Prefer exact ETF maps so the desk can price a vehicle (soft/orphan lag a bit).
    name = str(board.get("name") or "")
    if etf_spec_for_name(name):
        etf_adj = 5.0
    elif etf_spec_soft_fallback(name):
        etf_adj = 0.0
    else:
        etf_adj = -3.0
    leader_adj = leader_structure_adj(board)
    # Carrier ETF daily trend (attached once per session day); unclear → 0.
    etf_trend_adj = float(board.get("etf_trend_adj") or 0.0)
    return (
        rank
        + float(zt_n) * 6.0
        + float(board.get("pct") or 0)
        + float(board.get("focus") or 0) * 0.15
        + min(persist, 6) * 3.0
        + ladder_adj
        + etf_adj
        + leader_adj
        + etf_trend_adj
        - zb_pen
        - explode_pen
        - late_pen
    )


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
    # Ending / 退潮 sticky: lower the bar so identity catches up with sell bias.
    from market_desk.lifecycle import classify_lifecycle

    inc_status = str(incumbent.get("status") or "")
    inc_ending = classify_lifecycle(incumbent) == "ending" or inc_status == "退潮"
    if inc_ending:
        need = need * float(MAINLINE_FADE_SWITCH_MULT)
    # Same theme: treat as continuity, not a "mainline change", unless clearly stronger
    # or the incumbent is already fading.
    if same_theme(sticky, leader.get("name")):
        if inc_status == "退潮" and (leader.get("status") or "") != "退潮":
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
        and not inc_ending
    ):
        need = need * float(MAINLINE_HOLD_SWITCH_MULT)
    # Easier to adopt a challenger that has an exact ETF map when sticky does not.
    if etf_spec_for_name(str(leader.get("name") or "")) and not etf_spec_for_name(sticky):
        need = need * 0.75

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
    """Looser map when exact keyword miss: only 3-char stem overlaps (no 2-char)."""
    text = board_name or ""
    if not text:
        return None
    if etf_spec_for_name(text):
        return etf_spec_for_name(text)
    best: tuple[int, tuple[str, str, str]] | None = None
    for keys, spec in MAINLINE_ETF_RULES:
        for key in keys:
            if len(key) < 3:
                continue
            score = 0
            if key[:3] in text or (len(text) >= 3 and text[:3] in key):
                score = 3
            if len(key) >= 4 and (key[:4] in text or (len(text) >= 4 and text[:4] in key)):
                score = 4
            if score and (best is None or score > best[0]):
                best = (score, spec)
    return best[1] if best else None


def explain_mainline(
    hot: list[dict[str, Any]] | None,
    chosen: dict[str, Any] | None,
    *,
    sticky_name: str | None = None,
    sticky_held_seconds: float | None = None,
    margin: float | None = None,
) -> dict[str, Any]:
    """Explain why the live mainline is kept or switched (for the desk strip)."""
    boards = list(hot or [])
    industries = [b for b in boards if b.get("kind") == "industry"]
    pool = industries or boards
    chosen = chosen or {}
    name = str(chosen.get("name") or "").strip()
    sticky = (sticky_name or "").strip()
    need = float(margin) if margin is not None else float(setting("sticky_margin", 12.0))
    hold_min = float(setting("switch_min_seconds", 300) or 0)
    held = float(sticky_held_seconds) if sticky_held_seconds is not None else None

    leader = max(pool, key=mainline_score) if pool else None
    incumbent = next((b for b in pool if (b.get("name") or "") == sticky), None) if sticky else None
    lead_name = str((leader or {}).get("name") or "")
    lead_s = round(mainline_score(leader), 1) if leader else None
    hold_s = round(mainline_score(incumbent), 1) if incumbent else None
    chosen_s = round(mainline_score(chosen), 1) if name else None
    gap = None
    if lead_s is not None and hold_s is not None:
        gap = round(lead_s - hold_s, 1)

    effective_need = need
    same = bool(sticky and lead_name and same_theme(sticky, lead_name))
    in_hold = bool(held is not None and hold_min > 0 and held < hold_min)
    if same:
        effective_need = need * float(MAINLINE_THEME_SWITCH_MULT)
    elif in_hold:
        effective_need = need * float(MAINLINE_HOLD_SWITCH_MULT)
    if (
        lead_name
        and sticky
        and etf_spec_for_name(lead_name)
        and not etf_spec_for_name(sticky)
        and not same
    ):
        effective_need = float(effective_need) * 0.75
    effective_need = round(float(effective_need), 1)

    if not name:
        reason = "热点池为空，主线未明"
        kept = False
    elif not sticky:
        reason = "首任主线（无粘性前任）"
        kept = False
    elif name == sticky:
        if same and lead_name and lead_name != sticky:
            reason = (
                f"同主题粘滞：挑战者 {lead_name} 分差 {gap if gap is not None else '—'} "
                f"< 需 {effective_need}"
            )
        elif in_hold and lead_name and lead_name != sticky:
            reason = (
                f"持有期内未换防：挑战者 {lead_name} 分差 {gap if gap is not None else '—'} "
                f"< 需 {effective_need}（已持 {int(held or 0)}s / {int(hold_min)}s）"
            )
        elif lead_name and lead_name != sticky:
            reason = (
                f"迟滞保留：挑战者 {lead_name} 分差 {gap if gap is not None else '—'} "
                f"< 需 {effective_need}"
            )
        else:
            reason = "仍为池内最高分（或并列领先）"
        kept = True
    elif not incumbent and sticky:
        if same_theme(sticky, name):
            reason = f"前任 {sticky} 离开热点池，同主题接任 {name}"
        else:
            reason = f"前任 {sticky} 离开热点池，改认 {name}"
        kept = False
    elif same and name != sticky:
        reason = (
            f"同主题换板：{sticky} → {name}，分差 {gap if gap is not None else '—'} "
            f"≥ 需 {effective_need}"
        )
        kept = False
    else:
        reason = (
            f"分差达标换防：{sticky} → {name}，分差 {gap if gap is not None else '—'} "
            f"≥ 需 {effective_need}"
        )
        kept = False

    runners = sorted(pool, key=mainline_score, reverse=True)[:3]
    return {
        "name": name or None,
        "theme": theme_key(name) if name else None,
        "score": chosen_s,
        "zt_n": chosen.get("zt_n"),
        "status": chosen.get("status"),
        "leader_boards": chosen.get("leader_boards"),
        "leader_adj": round(leader_structure_adj(chosen), 1) if name else 0.0,
        "etf_exact": bool(etf_spec_for_name(name)) if name else False,
        "sticky_name": sticky or None,
        "sticky_score": hold_s,
        "challenger_name": lead_name or None,
        "challenger_score": lead_s,
        "gap": gap,
        "need": effective_need,
        "base_need": round(need, 1),
        "held_seconds": None if held is None else int(held),
        "hold_min_seconds": int(hold_min) if hold_min else 0,
        "in_hold": in_hold,
        "same_theme_challenge": same and bool(lead_name and lead_name != sticky),
        "kept": kept,
        "reason": reason,
        "top": [
            {
                "name": str(b.get("name") or ""),
                "score": round(mainline_score(b), 1),
                "zt_n": b.get("zt_n"),
                "status": b.get("status"),
                "leader_boards": b.get("leader_boards"),
                "leader_adj": round(leader_structure_adj(b), 1),
                "theme": theme_key(str(b.get("name") or "")),
            }
            for b in runners
            if b.get("name")
        ],
    }
