"""Dual-dragon selection and independent popular pullback helpers."""

from __future__ import annotations

from typing import Any

from market_desk.filters import is_main_board, is_st, normalize_code
from market_desk.numbers import num


def stock_liquidity_ok(
    mv_yi: float | None,
    turnover: float | None,
    *,
    sealed: bool = False,
) -> bool:
    """Return False when market-cap / turnover fails desk liquidity floors.

    - Below ``STOCK_MV_HARD_MIN_YI`` (100亿): always reject.
    - 100–500亿: require turnover ≥ ``STOCK_TURN_MIN_MID`` (3%) when known.
    - Above 500亿: require turnover ≥ ``STOCK_TURN_MIN_LARGE`` (2%) when known.
    Sealed limit-ups skip the turnover floor (seal day turnover is often thin).
    Missing turnover does not reject mid/large names (data gap).
    """
    from market_desk.config import (
        STOCK_MV_HARD_MIN_YI,
        STOCK_MV_MID_MAX_YI,
        STOCK_TURN_MIN_LARGE,
        STOCK_TURN_MIN_MID,
    )

    if mv_yi is None:
        return False
    try:
        mv = float(mv_yi)
    except (TypeError, ValueError):
        return False
    if mv < float(STOCK_MV_HARD_MIN_YI):
        return False
    if sealed:
        return True
    if turnover is None:
        return True
    try:
        tr = float(turnover)
    except (TypeError, ValueError):
        return True
    if mv <= float(STOCK_MV_MID_MAX_YI):
        return tr >= float(STOCK_TURN_MIN_MID)
    return tr >= float(STOCK_TURN_MIN_LARGE)


def board_surge_fresh(board: dict[str, Any] | None, life_stage: str | None) -> bool:
    """Return True when the board looks like a same-day surge (defer stock buys).

    Prefer lifecycle「萌芽」; also catch jump-style zt expansions vs prior hist.
    """
    if str(life_stage or "") == "starting":
        return True
    card = board or {}
    zt_n = int(card.get("zt_n") or 0)
    hist = list(card.get("hist") or [])
    prev_zt = int(hist[-1].get("zt_n") or 0) if hist else 0
    if zt_n >= max(3, prev_zt + 2) and prev_zt <= 2:
        return True
    flags = card.get("flags") if isinstance(card.get("flags"), dict) else {}
    if flags.get("点火") and zt_n <= 2:
        return True
    return False


def pick_emotion_dragon(
    board: dict[str, Any] | None,
    zt: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Pick the emotion dragon: highest limit-up boards, then amount / seal proxy."""
    card = board or {}
    pool_codes = {
        normalize_code(m.get("code"))
        for m in (card.get("pool") or card.get("members") or [])
        if normalize_code(m.get("code"))
    }
    zt_by = {normalize_code(x.get("code")): x for x in (zt or [])}
    rows: list[dict[str, Any]] = []
    for code, row in zt_by.items():
        if pool_codes and code not in pool_codes:
            continue
        if not is_main_board(code):
            continue
        name = str(row.get("name") or "")
        if is_st(name):
            continue
        boards = int(row.get("boards") or 0)
        # Tip ladder (≥3) stays visible but is not a buyable emotion dragon.
        if boards >= 3:
            continue
        amount = float(num(row.get("amount"), 0) or 0)
        seal = float(num(row.get("seal_amount") or row.get("fund"), 0) or 0)
        rows.append(
            {
                "code": code,
                "name": name or code,
                "boards": boards,
                "amount": amount,
                "seal": seal,
                "pct": row.get("pct"),
                "price": row.get("price") or row.get("last"),
                "dragon_kind": "emotion",
                "sealed": True,
            }
        )
    if not rows:
        # Fall back to board leader when it is a 1–2 board name still in zt.
        lc = normalize_code(card.get("leader_code"))
        lb = int(card.get("leader_boards") or 0)
        if lc and 1 <= lb <= 2 and lc in zt_by:
            row = zt_by[lc]
            return {
                "code": lc,
                "name": str(row.get("name") or card.get("leader_name") or lc),
                "boards": lb,
                "amount": float(num(row.get("amount"), 0) or 0),
                "seal": float(num(row.get("seal_amount") or row.get("fund"), 0) or 0),
                "pct": row.get("pct"),
                "price": row.get("price") or row.get("last"),
                "dragon_kind": "emotion",
                "sealed": True,
            }
        return None
    rows.sort(
        key=lambda r: (int(r["boards"]), float(r["seal"]), float(r["amount"])),
        reverse=True,
    )
    return rows[0]


def pick_mid_army_dragon(
    board: dict[str, Any] | None,
    *,
    skip_codes: set[str] | None = None,
) -> dict[str, Any] | None:
    """Pick the mid-army dragon: largest turnover / mv core that need not limit-up."""
    card = board or {}
    skip = {normalize_code(c) for c in (skip_codes or set()) if normalize_code(c)}
    best: dict[str, Any] | None = None
    best_score = -1.0
    for member in card.get("pool") or card.get("members") or []:
        code = normalize_code(member.get("code"))
        name = str(member.get("name") or "")
        if not code or code in skip or not is_main_board(code) or is_st(name):
            continue
        try:
            amount = float(member.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        try:
            mv = float(member.get("mv_yi") or 0)
        except (TypeError, ValueError):
            mv = 0.0
        try:
            pct = float(member.get("pct") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        try:
            turnover = float(member.get("turnover")) if member.get("turnover") is not None else None
        except (TypeError, ValueError):
            turnover = None
        if not stock_liquidity_ok(mv if mv else None, turnover, sealed=False):
            continue
        # Prefer heavy turnover; soft-boost larger caps and leaders vs board.
        score = amount / 1e8 + min(mv, 800.0) / 200.0 + max(0.0, pct) * 0.15
        if score > best_score:
            best_score = score
            best = {
                "code": code,
                "name": name or code,
                "amount": amount,
                "mv_yi": mv,
                "pct": member.get("pct"),
                "price": member.get("price"),
                "high": member.get("high"),
                "low": member.get("low"),
                "dragon_kind": "mid_army",
                "sealed": False,
            }
    return best


def build_dual_dragon_stocks(
    main: dict[str, Any] | None,
    zt: list[dict[str, Any]] | None,
    *,
    surge_fresh: bool = False,
    phase: str = "",
) -> list[dict[str, Any]]:
    """Build up to two mainline stock cards: emotion + mid-army dragons.

    Emotion timing = 确认异动 (observe while sealed / surge); mid-army = 趋势回踩.
    Back-row constituents are not included here.
    """
    from market_desk.verdict import _score_stock

    card = main or {}
    board_name = str(card.get("name") or "")
    board_pct = card.get("pct")
    board_flow = card.get("main_yi")
    sealed = {normalize_code(x.get("code")) for x in (zt or [])}
    boards_by = {
        normalize_code(x.get("code")): int(x.get("boards") or 0) for x in (zt or [])
    }
    explode_by = {
        normalize_code(x.get("code")): int(x.get("explode_count") or 0)
        for x in (zt or [])
    }
    emotion = pick_emotion_dragon(card, zt)
    mid = pick_mid_army_dragon(
        card, skip_codes={emotion["code"]} if emotion else set()
    )
    out: list[dict[str, Any]] = []

    def _member_for(code: str) -> dict[str, Any] | None:
        for m in card.get("pool") or card.get("members") or []:
            if normalize_code(m.get("code")) == code:
                return dict(m)
        return None

    if emotion:
        code = emotion["code"]
        member = _member_for(code) or {
            "code": code,
            "name": emotion.get("name"),
            "pct": emotion.get("pct"),
            "price": emotion.get("price"),
            "high": emotion.get("price"),
            "low": emotion.get("price"),
            "amount": emotion.get("amount"),
        }
        # Sealed emotion dragon: still list as observe / 确认异动, never ready.
        if code in sealed or surge_fresh:
            item = dict(member)
            item["code"] = code
            item["ready"] = False
            item["dragon_kind"] = "emotion"
            item["role_label"] = "情绪龙·异动"
            item["desk_source"] = "emotion_dragon"
            boards_n = int(emotion.get("boards") or boards_by.get(code) or 0)
            item["reason"] = (
                f"情绪龙 {boards_n}板·确认异动"
                + ("；已封板先观察" if code in sealed else "")
                + ("；板块暴起当日不推现买" if surge_fresh else "")
            )
            out.append(item)
        else:
            scored = _score_stock(
                member,
                sealed,
                boards_by,
                skip=set(),
                broken=set(),
                explode_by=explode_by,
                blocked=None,
                board_name=board_name,
                membership={},
                strict=False,
                orphan="",
                board_pct=board_pct,
                board_main_yi=board_flow,
                phase=phase,
            )
            if scored:
                item = scored[1]
                item["dragon_kind"] = "emotion"
                item["role_label"] = "情绪龙·异动"
                item["desk_source"] = "emotion_dragon"
                item["reason"] = (
                    f"情绪龙·确认异动；{item.get('reason') or '回踩确认后再动'}"
                )
                # Emotion buys only after mild confirmation, never on fresh surge.
                if surge_fresh:
                    item["ready"] = False
                out.append(item)
            else:
                item = dict(member)
                item["code"] = code
                item["ready"] = False
                item["dragon_kind"] = "emotion"
                item["role_label"] = "情绪龙·异动"
                item["desk_source"] = "emotion_dragon"
                item["reason"] = "情绪龙·等待确认异动（未进回踩带）"
                out.append(item)

    if mid and len(out) < 2:
        code = mid["code"]
        member = _member_for(code) or mid
        if code in sealed:
            item = dict(member)
            item["code"] = code
            item["ready"] = False
            item["dragon_kind"] = "mid_army"
            item["role_label"] = "中军龙·回踩"
            item["desk_source"] = "mid_army_dragon"
            item["reason"] = "中军龙已封板，趋势回踩口径先观察"
            out.append(item)
        else:
            scored = _score_stock(
                member,
                sealed,
                boards_by,
                skip=set(),
                broken=set(),
                explode_by=explode_by,
                blocked=None,
                board_name=board_name,
                membership={},
                strict=True,
                orphan="",
                board_pct=board_pct,
                board_main_yi=board_flow,
                phase=phase,
            )
            if scored:
                item = scored[1]
                item["dragon_kind"] = "mid_army"
                item["role_label"] = "中军龙·回踩"
                item["desk_source"] = "mid_army_dragon"
                item["reason"] = f"中军龙·趋势回踩；{item.get('reason') or ''}".strip("；")
                if surge_fresh:
                    item["ready"] = False
                out.append(item)
            else:
                # Still surface mid-army as observe when not in sweet band.
                item = dict(member)
                item["code"] = code
                item["ready"] = False
                item["dragon_kind"] = "mid_army"
                item["role_label"] = "中军龙·回踩"
                item["desk_source"] = "mid_army_dragon"
                item["reason"] = "中军龙·等趋势回踩到位"
                out.append(item)
    return out[:2]


def _near_day_low(member: dict[str, Any], *, max_pct: float = 2.0) -> bool:
    """Return True when last is within max_pct of the session low."""
    try:
        price = float(member.get("price"))
        low = float(member.get("low"))
    except (TypeError, ValueError):
        return False
    if price <= 0 or low <= 0 or price < low:
        return False
    return (price - low) / low * 100.0 <= max_pct


def _independent_vs_board(member: dict[str, Any], board_pct: float | None) -> bool:
    """Rough independence: stock path diverges from board pct."""
    try:
        pct = float(member.get("pct"))
    except (TypeError, ValueError):
        return False
    if board_pct is None:
        return abs(pct) >= 1.0
    try:
        bp = float(board_pct)
    except (TypeError, ValueError):
        return abs(pct) >= 1.0
    # Held up while board soft, or sold off while board strong — independent path.
    if pct >= bp + 1.2:
        return True
    if pct <= bp - 1.5 and pct > -4.0:
        return True
    return abs(pct - bp) >= 2.0


def build_independent_pullback_candidates(
    main: dict[str, Any] | None,
    *,
    skip_codes: set[str] | None = None,
    max_items: int = 5,
) -> list[dict[str, Any]]:
    """List mainline constituents for independent popular pullback observe cards.

    Soft pre-filter (day low / vs board). Engine may tighten with 5-day lows + zt_ytd.
    """
    card = main or {}
    board_pct = card.get("pct")
    skip = {normalize_code(c) for c in (skip_codes or set()) if normalize_code(c)}
    # Always skip board emotion/mid dragons from this observe pool.
    emo = pick_emotion_dragon(card, None)
    if emo:
        skip.add(emo["code"])
    mid = pick_mid_army_dragon(card, skip_codes=skip)
    if mid:
        skip.add(mid["code"])
    skip.add(normalize_code(card.get("leader_code")))
    skip.add(normalize_code(card.get("slot_code")))

    scored: list[tuple[float, dict[str, Any]]] = []
    for member in card.get("pool") or card.get("members") or []:
        code = normalize_code(member.get("code"))
        name = str(member.get("name") or "")
        if not code or code in skip or not is_main_board(code) or is_st(name):
            continue
        try:
            pct = float(member.get("pct"))
        except (TypeError, ValueError):
            continue
        try:
            mv = float(member.get("mv_yi")) if member.get("mv_yi") is not None else None
        except (TypeError, ValueError):
            mv = None
        try:
            turnover = float(member.get("turnover")) if member.get("turnover") is not None else None
        except (TypeError, ValueError):
            turnover = None
        if not stock_liquidity_ok(mv, turnover, sealed=False):
            continue
        # Soft 1–2 day pullback window: not chasing highs, not collapsing.
        if pct > 3.5 or pct < -3.5:
            continue
        if not _near_day_low(member, max_pct=2.5):
            continue
        if not _independent_vs_board(member, board_pct if board_pct is not None else None):
            continue
        try:
            amount = float(member.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        try:
            high = float(member.get("high") or 0)
            price = float(member.get("price") or 0)
            pb = (high - price) / high * 100.0 if high > 0 else 0.0
        except (TypeError, ValueError):
            pb = 0.0
        score = amount / 1e8 + pb * 0.4 - abs(pct) * 0.2
        item = dict(member)
        item["code"] = code
        item["ready"] = False
        item["desk_source"] = "independent_pop"
        item["role_label"] = "独立人气·回踩"
        item["reason"] = (
            f"主线板内独立行情·近低回踩观察（vs板 {_fmt_board(board_pct)}，"
            f"个股 {pct:+.1f}%）"
        )
        scored.append((score, item))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [row[1] for row in scored[: max(1, int(max_items))]]


def _fmt_board(board_pct: Any) -> str:
    try:
        return f"{float(board_pct):+.1f}%"
    except (TypeError, ValueError):
        return "—"


def within_n_day_low(
    lows: list[float | None] | None,
    last: float | None,
    *,
    n: int = 5,
    max_pct: float = 2.0,
) -> bool:
    """Return True when last is within max_pct of the min low over the last n bars."""
    if last is None or last <= 0:
        return False
    vals = [float(x) for x in (lows or [])[-n:] if x is not None and float(x) > 0]
    if len(vals) < max(3, min(n, 3)):
        return False
    floor = min(vals)
    if floor <= 0:
        return False
    return (float(last) - floor) / floor * 100.0 <= max_pct
