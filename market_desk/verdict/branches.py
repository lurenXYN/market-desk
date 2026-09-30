"""Side-line and link-line recommendation branches next to the mainline."""

from __future__ import annotations

from typing import Any
from market_desk.filters import normalize_code
from market_desk.lifecycle import classify_lifecycle
from market_desk.mainline import (
    etf_spec_for_name,
    etf_spec_soft_fallback,
    match_mainline_etf,
    pick_side_mainline,
    same_theme,
)
from market_desk.settings import setting

from market_desk.verdict.common import (
    _blocks_chi_star_stocks,
    _demote_buy_to_wait,
    _join_hint,
    _pool_codes_from_board,
)
from market_desk.verdict.scoring import _build_recommend, _stock_candidates


def _build_side_branch(
    *,
    hot: list[dict[str, Any]],
    main: dict[str, Any],
    etfs: list[dict[str, Any]],
    zt: list[dict[str, Any]],
    zb: list[dict[str, Any]],
    bans: list[str],
    stock_block: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Build an observation-only side branch; never marks ready buys."""
    side = pick_side_mainline(hot, main)
    if not side:
        return None, None
    side_name = str(side.get("name") or "").strip()
    if not side_name:
        return None, None
    exact = etf_spec_for_name(side_name)
    soft = False
    vehicle = match_mainline_etf(side_name, etfs) if exact else None
    if not vehicle:
        soft_spec = etf_spec_soft_fallback(side_name)
        if soft_spec:
            _, code, name = soft_spec
            quote = next((x for x in (etfs or []) if x.get("code") == code), None)
            vehicle = (
                dict(quote)
                if quote
                else {
                    "code": code,
                    "name": name,
                    "price": None,
                    "pct": None,
                    "low": None,
                    "high": None,
                }
            )
            soft = True
    vehicle = vehicle or {}
    bounce = None
    price = vehicle.get("price")
    low = vehicle.get("low")
    if price is not None and low not in (None, 0):
        bounce = (float(price) / float(low) - 1.0) * 100.0
    allow_stocks = not stock_block and not _blocks_chi_star_stocks(side_name)
    stocks = _stock_candidates(side, zt, zb, boards=hot) if allow_stocks else []
    # Always observation path: wait prices only, never ready.
    rec = _build_recommend("观察回踩", side, vehicle, bounce, stocks, bans)
    if setting("side_require_member", True):
        members = {
            normalize_code(m.get("code") if isinstance(m, dict) else m)
            for m in side.get("pool") or side.get("members") or []
        }
        members.discard("")
        rec["items"] = [
            it
            for it in rec.get("items") or []
            if (it.get("kind") or "stock") == "etf" or normalize_code(it.get("code")) in members
        ]
    for item in rec.get("items") or []:
        item["ready"] = False
        _demote_buy_to_wait(item)
        kind = item.get("kind") or "stock"
        item["role_label"] = "支线ETF盯回踩" if kind == "etf" else "支线个股盯回踩"
    rec["buy"] = False
    rec["title"] = f"观察支线 · {side_name}"
    rec["size_note"] = _join_hint(
        "仅观察回踩，不当现买主推；买卖仍跟实时主线",
        f"分差 {side.get('score_gap')}" if side.get("score_gap") is not None else "",
    )
    if soft and vehicle.get("name"):
        rec["size_note"] = _join_hint(
            str(rec.get("size_note") or ""),
            f"支线载体近似映射 {vehicle.get('name')}",
        )
    if not (rec.get("items") or []):
        rec["text"] = f"观察支线 {side_name} · 暂无合格回踩票"
        rec["size_note"] = _join_hint(
            str(rec.get("size_note") or ""),
            "可盯板块状态，先不定价",
        )
    life = classify_lifecycle(side)
    info = {
        "name": side_name,
        "bk": side.get("bk"),
        "kind": side.get("kind"),
        "status": side.get("status"),
        "pct": side.get("pct"),
        "zt_n": side.get("zt_n"),
        "leader_name": side.get("leader_name"),
        "leader_code": side.get("leader_code"),
        "lifecycle": life,
        "score": side.get("score"),
        "score_gap": side.get("score_gap"),
        "etf_soft": soft,
        "carrier_code": vehicle.get("code"),
        "carrier_name": vehicle.get("name"),
        "pool_codes": _pool_codes_from_board(side),
    }
    return info, rec


def _mainline_needs_link(recommend: dict[str, Any] | None) -> tuple[bool, str]:
    """Decide whether soft sibling-board cards should surface.

    Triggers when sticky mainline has no ready entry, or every priced card hugs
    the chase band (overheated / near 不追价) even if some ready flags linger.
    """
    from market_desk.config import BOARD_LINK_CHASE_RATIO

    items = list((recommend or {}).get("items") or [])
    if not items:
        return True, "主线暂无卡片"
    if not any(bool(i.get("ready")) for i in items):
        return True, "主线暂无现买点"
    priced = 0
    at_chase = 0
    for item in items:
        last = item.get("last")
        chase = item.get("chase_price")
        if last is None or chase is None:
            continue
        try:
            priced += 1
            if float(last) >= float(chase) * float(BOARD_LINK_CHASE_RATIO):
                at_chase += 1
        except (TypeError, ValueError):
            continue
    if priced > 0 and at_chase >= priced:
        return True, "主线全贴不追价/过热"
    return False, ""


def _peer_sim_for_name(
    peers: list[dict[str, Any]] | None,
    name: str,
) -> float | None:
    """Return similarity for one peer name from similar_peers, if present."""
    target = str(name or "").strip()
    if not target:
        return None
    for peer in peers or []:
        if str(peer.get("name") or "").strip() != target:
            continue
        try:
            return float(peer.get("sim") or 0)
        except (TypeError, ValueError):
            return 0.0
    return None


def _link_board_ok(
    board: dict[str, Any] | None,
    *,
    main_name: str,
    side_name: str = "",
    allow_side_name: bool = False,
) -> bool:
    """Return True when a hot board is eligible as a soft link target."""
    if not board:
        return False
    name = str(board.get("name") or "").strip()
    if not name or name == main_name:
        return False
    if name == side_name and not allow_side_name:
        return False
    status = str(board.get("status") or "")
    if status in ("尖峰禁追", "退潮"):
        return False
    if _blocks_chi_star_stocks(name):
        return False
    return True


def _pick_link_fallback_board(
    *,
    hot: list[dict[str, Any]] | None,
    main: dict[str, Any],
    side_info: dict[str, Any] | None,
    peers: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, float, str]:
    """Pick a soft link board when similar_peers yields nothing.

    Order: promote side when sim/theme qualifies → same theme_key → score-near.
    Returns (board, synthetic_sim, fallback_tag).
    """
    from market_desk.config import (
        BOARD_LINK_SCORE_FALLBACK_SIM,
        BOARD_LINK_SIM_MIN,
        BOARD_LINK_THEME_FALLBACK_SIM,
    )
    from market_desk.mainline import mainline_score, theme_key

    main_name = str(main.get("name") or "").strip()
    by_name = {
        str(b.get("name") or "").strip(): b
        for b in (hot or [])
        if str(b.get("name") or "").strip()
    }
    side_name = str((side_info or {}).get("name") or "").strip()
    # 1) Side runner-up with real or theme similarity → absorb into link.
    if side_name:
        side_board = by_name.get(side_name) or None
        if side_board is None and side_info:
            side_board = dict(side_info)
        side_sim = _peer_sim_for_name(peers, side_name)
        if side_sim is None and same_theme(main_name, side_name):
            side_sim = float(BOARD_LINK_THEME_FALLBACK_SIM)
        if (
            side_board
            and side_sim is not None
            and float(side_sim) >= float(BOARD_LINK_SIM_MIN)
            and _link_board_ok(
                side_board, main_name=main_name, side_name=side_name, allow_side_name=True
            )
        ):
            return side_board, float(side_sim), "from_side"

    # 2) Same theme_key sibling in the hot pool.
    main_theme = theme_key(main_name)
    if main_theme:
        best_theme: dict[str, Any] | None = None
        best_sc = -1e9
        for board in hot or []:
            if not _link_board_ok(board, main_name=main_name, side_name=side_name):
                continue
            if theme_key(str(board.get("name") or "")) != main_theme:
                continue
            sc = mainline_score(board)
            if sc > best_sc:
                best_sc = sc
                best_theme = board
        if best_theme:
            return best_theme, float(BOARD_LINK_THEME_FALLBACK_SIM), "theme"

    # 3) Score-near runner within side_mainline_gap.
    gap_limit = float(setting("side_mainline_gap", 12.0) or 0.0)
    if gap_limit > 0:
        main_sc = mainline_score(main)
        ranked = sorted(list(hot or []), key=mainline_score, reverse=True)
        for board in ranked:
            if not _link_board_ok(board, main_name=main_name, side_name=""):
                continue
            sc = mainline_score(board)
            if main_sc - sc > gap_limit:
                continue
            return board, float(BOARD_LINK_SCORE_FALLBACK_SIM), "score"
    # Last resort: raw side board even when sim is thin (still observe_only).
    if side_name:
        side_board = by_name.get(side_name)
        if _link_board_ok(
            side_board, main_name=main_name, side_name=side_name, allow_side_name=True
        ):
            sim = _peer_sim_for_name(peers, side_name)
            if sim is None:
                sim = float(BOARD_LINK_SCORE_FALLBACK_SIM)
            return side_board, float(sim), "from_side_soft"
    return None, 0.0, ""


def _build_link_branch(
    *,
    hot: list[dict[str, Any]] | None,
    main: dict[str, Any] | None,
    etfs: list[dict[str, Any]] | None,
    zt: list[dict[str, Any]],
    zb: list[dict[str, Any]],
    bans: list[str],
    stock_block: bool,
    recommend: dict[str, Any] | None,
    side_info: dict[str, Any] | None = None,
    orphan: bool | str = False,
    phase: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Build soft sibling-board cards when sticky mainline lacks a calm entry.

    Does not switch sticky mainline or upgrade hero action; cards stay observation
    path with an extra size damp (BOARD_LINK_SIZE_MULT).

    When ``similar_peers`` is empty, falls back to same-theme / score-near boards.
    A high-sim side runner is absorbed into the link narrative (not dual-empty).
    """
    from market_desk.config import BOARD_LINK_MAX_STOCKS, BOARD_LINK_SIM_MIN, BOARD_LINK_SIZE_MULT

    main = main or {}
    main_name = str(main.get("name") or "").strip()
    if not main_name or stock_block:
        return None, None
    need_link, link_why = _mainline_needs_link(recommend)
    if not need_link:
        return None, None

    peers = list(main.get("similar_peers") or [])
    by_name = {
        str(b.get("name") or "").strip(): b
        for b in (hot or [])
        if str(b.get("name") or "").strip()
    }
    side_name = str((side_info or {}).get("name") or "").strip()
    chosen: dict[str, Any] | None = None
    chosen_sim = 0.0
    fallback = ""
    best = -1.0
    for peer in peers:
        name = str(peer.get("name") or "").strip()
        if not name or name == main_name:
            continue
        # Prefer promoting side into link later; skip it here so we can absorb.
        if name == side_name:
            continue
        try:
            sim = float(peer.get("sim") or 0)
        except (TypeError, ValueError):
            sim = 0.0
        if sim < float(BOARD_LINK_SIM_MIN):
            continue
        board = by_name.get(name)
        if not _link_board_ok(board, main_name=main_name, side_name=side_name):
            continue
        status = str((board or {}).get("status") or "")
        rank = sim + (0.12 if status == "确认中" else 0.0) + min(
            float((board or {}).get("zt_n") or 0) * 0.01, 0.08
        )
        if rank > best:
            best = rank
            chosen = board
            chosen_sim = sim
            fallback = "peer"

    # Promote side into link when it clears the sim floor (or theme fallback).
    if side_name:
        side_sim = _peer_sim_for_name(peers, side_name)
        if side_sim is None and same_theme(main_name, side_name):
            from market_desk.config import BOARD_LINK_THEME_FALLBACK_SIM

            side_sim = float(BOARD_LINK_THEME_FALLBACK_SIM)
        side_board = by_name.get(side_name)
        if (
            side_board
            and side_sim is not None
            and float(side_sim) >= float(BOARD_LINK_SIM_MIN)
            and _link_board_ok(
                side_board, main_name=main_name, side_name=side_name, allow_side_name=True
            )
        ):
            # Prefer side absorption over a weaker peer, or fill empty peer slot.
            if chosen is None or float(side_sim) + 0.05 >= float(chosen_sim):
                chosen = side_board
                chosen_sim = float(side_sim)
                fallback = "from_side"

    if not chosen:
        chosen, chosen_sim, fallback = _pick_link_fallback_board(
            hot=hot, main=main, side_info=side_info, peers=peers
        )
    if not chosen:
        return None, None

    peer_name = str(chosen.get("name") or "").strip()
    exact = etf_spec_for_name(peer_name)
    soft = False
    vehicle = match_mainline_etf(peer_name, etfs) if exact else None
    if not vehicle:
        soft_spec = etf_spec_soft_fallback(peer_name)
        if soft_spec:
            _, code, name = soft_spec
            quote = next((x for x in (etfs or []) if x.get("code") == code), None)
            vehicle = (
                dict(quote)
                if quote
                else {
                    "code": code,
                    "name": name,
                    "price": None,
                    "pct": None,
                    "low": None,
                    "high": None,
                }
            )
            soft = True
    vehicle = vehicle or {}
    bounce = None
    price = vehicle.get("price")
    low = vehicle.get("low")
    if price is not None and low not in (None, 0):
        bounce = (float(price) / float(low) - 1.0) * 100.0

    allow_stocks = not _blocks_chi_star_stocks(peer_name)
    stocks = (
        _stock_candidates(
            chosen, zt, zb, boards=hot or [], orphan=orphan, phase=phase
        )
        if allow_stocks
        else []
    )
    skip = set(_recommend_codes(recommend))
    stocks = [s for s in stocks if normalize_code(s.get("code")) not in skip][
        : int(BOARD_LINK_MAX_STOCKS)
    ]
    if not stocks and not vehicle.get("code"):
        return None, None

    rec = _build_recommend("观察回踩", chosen, vehicle, bounce, stocks, bans)
    for item in rec.get("items") or []:
        item["ready"] = False
        item["link_board"] = True
        item["link_sim"] = round(chosen_sim, 2)
        item["link_fallback"] = fallback or "peer"
        _demote_buy_to_wait(item)
        kind = item.get("kind") or "stock"
        item["role_label"] = (
            f"联动ETF·{peer_name}" if kind == "etf" else f"联动·{peer_name}"
        )
        reason = str(item.get("reason") or "")
        if fallback == "from_side" or fallback == "from_side_soft":
            tip = f"支线并入联动 {peer_name}（相对主线 {main_name}）"
        elif fallback == "theme":
            tip = f"同主题兜底 {peer_name}（相对主线 {main_name}）"
        elif fallback == "score":
            tip = f"分差兜底 {peer_name}（相对主线 {main_name}）"
        else:
            tip = f"相似主线 {main_name}（{int(round(chosen_sim * 100))}%）"
        item["reason"] = f"{tip}；{reason}" if reason else tip
    rec["buy"] = False
    rec["link"] = True
    rec["link_sim"] = round(chosen_sim, 2)
    rec["link_why"] = link_why
    rec["link_fallback"] = fallback or "peer"
    rec["title"] = f"板块联动 · {peer_name}"
    fb_hint = {
        "from_side": "支线并入",
        "from_side_soft": "支线软并入",
        "theme": "同主题兜底",
        "score": "分差兜底",
    }.get(fallback, "")
    rec["size_note"] = _join_hint(
        f"{link_why}；相似板块回踩可小仓（建议再×{BOARD_LINK_SIZE_MULT:g}），不改 sticky 主线",
        f"相似 {int(round(chosen_sim * 100))}%"
        + (f" · {fb_hint}" if fb_hint else "")
        + (f" · 载体近似 {vehicle.get('name')}" if soft and vehicle.get("name") else ""),
    )
    if not (rec.get("items") or []):
        return None, None

    life = classify_lifecycle(chosen)
    info = {
        "name": peer_name,
        "bk": chosen.get("bk"),
        "kind": chosen.get("kind"),
        "status": chosen.get("status"),
        "pct": chosen.get("pct"),
        "zt_n": chosen.get("zt_n"),
        "leader_name": chosen.get("leader_name"),
        "leader_code": chosen.get("leader_code"),
        "lifecycle": life,
        "score": chosen.get("score"),
        "sim": round(chosen_sim, 2),
        "etf_soft": soft,
        "carrier_code": vehicle.get("code"),
        "carrier_name": vehicle.get("name"),
        "pool_codes": _pool_codes_from_board(chosen),
        "mainline_name": main_name,
        "why": link_why,
        "fallback": fallback or "peer",
        "from_side": fallback in ("from_side", "from_side_soft"),
    }
    return info, rec


def _recommend_codes(recommend: dict[str, Any] | None) -> list[str]:
    """Collect codes from a recommend / side_recommend payload."""
    out: list[str] = []
    for item in ((recommend or {}).get("items") or []):
        code = normalize_code(item.get("code"))
        if code and code not in out:
            out.append(code)
    return out
