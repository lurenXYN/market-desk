"""Hot / pinned / ice board ranking and per-board enrichment."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
import httpx
from market_desk.config import ICE_BOARD_COUNT
from market_desk.calendar import is_trading_day
from market_desk.lifecycle import build_mainline_lifecycle
from market_desk.eastmoney import fetch_board_members
from market_desk.filters import is_limit_down, is_main_board, normalize_code
from market_desk.sentiment import (
    board_cycle_tags,
    board_headline,
    board_note,
    board_status,
    cluster_path,
    cycle_flags,
    ice_status,
    spark_values,
)
from market_desk.mainline import etf_spec_for_name, etf_spec_soft_fallback

from market_desk.engine.util import _minutes, _prev_trading_day

log = logging.getLogger("market_desk")


def _pick_pin_board(
    industry: list[dict[str, Any]], aliases: tuple[str, ...]
) -> dict[str, Any] | None:
    """Prefer an exact industry name, then a clean prefix match."""
    exact = [b for b in industry if b["name"] in aliases]
    if exact:
        return max(exact, key=lambda b: b.get("pct") or 0)
    prefixed = [
        b
        for b in industry
        if any(b["name"].startswith(alias) for alias in aliases) and not b["name"].startswith("其他")
    ]
    if prefixed:
        return max(prefixed, key=lambda b: b.get("pct") or 0)
    contains = [
        b
        for b in industry
        if any(alias in b["name"] for alias in aliases) and not b["name"].startswith("其他")
    ]
    if contains:
        return max(contains, key=lambda b: b.get("pct") or 0)
    return None


def _mark_favorite_flags(
    cards: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    """Stamp in_favorite / favorite_id onto board cards."""
    by_bk = {
        str(r.get("bk") or "").upper(): r
        for r in rows
        if r.get("bk")
    }
    for card in cards:
        bk = str(card.get("bk") or "").upper()
        row = by_bk.get(bk)
        card["in_favorite"] = bool(row)
        if row:
            card["favorite_id"] = row.get("id")
            if row.get("note") and not card.get("favorite_note"):
                card["favorite_note"] = row.get("note")


def _join_board_note(base: Any, extra: str) -> str:
    """Append a short note fragment without duplicating text."""
    text = str(base or "").strip()
    bit = str(extra or "").strip()
    if not bit:
        return text
    if bit in text:
        return text
    return f"{text}；{bit}" if text else bit


def _rank_ice_boards(
    boards: list[dict[str, Any]], hot_names: set[str]
) -> list[dict[str, Any]]:
    """Pick the coldest industries; hot names are deprioritized but still allowed."""
    industry = [b for b in boards if b.get("kind") == "industry"]
    ranked = sorted(
        industry,
        key=lambda b: (
            1 if b.get("name") in hot_names else 0,
            b.get("pct") if b.get("pct") is not None else 0.0,
            -(b.get("down_count") or 0),
            b.get("up_count") or 0,
        ),
    )
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for board in ranked:
        name = board.get("name") or ""
        if not name or name in seen or name.startswith("其他"):
            continue
        seen.add(name)
        picked.append(board)
        if len(picked) >= ICE_BOARD_COUNT:
            break
    return picked


async def _enrich_board(
    client: httpx.AsyncClient,
    board: dict[str, Any],
    ctx: dict[str, Any],
    weakest: bool = False,
) -> dict[str, Any]:
    zt = ctx.get("zt") or []
    zb = ctx.get("zb") or []
    yzt = ctx.get("yzt") or []
    hist = (ctx.get("hist") or {}).get(board.get("bk") or "", [])
    members = await fetch_board_members(client, board["bk"], weakest=weakest)
    zt_by_code = {x["code"]: x for x in zt}
    zb_codes = {x["code"] for x in zb}
    yzt_codes = {x["code"] for x in yzt}
    zt_n = sum(1 for m in members if m["code"] in zt_by_code)
    dt_n = sum(1 for m in members if is_limit_down(m.get("name"), m.get("pct")))
    ranked_leaders = sorted(
        [zt_by_code[m["code"]] for m in members if m["code"] in zt_by_code],
        key=lambda x: int(x.get("boards") or 0),
        reverse=True,
    )
    leader = ranked_leaders[0] if ranked_leaders else None
    slot = ranked_leaders[1] if len(ranked_leaders) > 1 else None
    main_leader_ok = is_main_board(board.get("leader_code"))
    leader_name = (leader or {}).get("name") or (
        board.get("leader_name") if main_leader_ok else (members[0]["name"] if members else "")
    )
    leader_code = (leader or {}).get("code") or (
        board.get("leader_code") if main_leader_ok else (members[0]["code"] if members else "")
    )
    leader_boards = int((leader or {}).get("boards") or (1 if leader_code in zt_by_code else 0))
    tiandi = any(
        m["code"] in yzt_codes and (is_limit_down(m.get("name"), m.get("pct")) or m["code"] in zb_codes)
        for m in members
    )
    giveback = 0
    for m in members:
        high = m.get("high")
        price = m.get("price")
        if high and price and high > 0 and (high - price) / high >= 0.05 and (m.get("pct") or 0) < 1:
            giveback += 1
    zt_in_board = [zt_by_code[m["code"]] for m in members if m["code"] in zt_by_code]
    zb_n = sum(1 for m in members if m["code"] in zb_codes)
    explode_sum = sum(int(x.get("explode_count") or 0) for x in zt_in_board)
    late_seal_n = 0
    ladder = {
        "ge2": 0,
        "ladder_fill": 0.0,
        "ladder_gap": False,
        "rungs_filled": 0,
        "rungs_span": 0,
        "ladder_missing": 0,
    }
    try:
        from market_desk.sentiment import is_late_first_seal, ladder_stats

        late_seal_n = sum(1 for x in zt_in_board if is_late_first_seal(x.get("first_seal")))
        ladder = ladder_stats(zt_in_board)
    except Exception:
        late_seal_n = 0
    pct = board.get("pct") or 0
    card = dict(board)
    card["members"] = members[:5]
    card["pool"] = members
    card["zt_n"] = zt_n
    card["zb_n"] = zb_n
    card["explode_sum"] = explode_sum
    card["late_seal_n"] = late_seal_n
    card["ge2"] = ladder.get("ge2", 0)
    card["ladder_fill"] = ladder.get("ladder_fill", 0.0)
    card["ladder_gap"] = bool(ladder.get("ladder_gap"))
    card["rungs_filled"] = ladder.get("rungs_filled", 0)
    card["ladder_missing"] = ladder.get("ladder_missing", 0)
    card["dt_n"] = dt_n
    card["leader_name"] = leader_name
    card["leader_code"] = leader_code
    card["leader_boards"] = leader_boards
    card["slot_name"] = (slot or {}).get("name")
    card["slot_code"] = (slot or {}).get("code")
    card["slot_boards"] = int((slot or {}).get("boards") or 0)
    card["ice"] = weakest
    if weakest:
        card["status"] = ice_status(
            pct, dt_n, int(board.get("up_count") or 0), int(board.get("down_count") or 0)
        )
    else:
        card["status"] = board_status(pct, zt_n, leader_boards)
    ice = weakest or card["status"] in ("冰点", "冷冻", "相对最冷", "传染预警")
    contagion = card["status"] == "传染预警"
    flags = cycle_flags(
        pct,
        zt_n,
        leader_boards,
        dt_n=dt_n,
        ice=ice,
        contagion=contagion,
        tiandi=tiandi,
        hist=hist,
        giveback=giveback,
    )
    headline, tone = board_headline(card["status"], flags, ice)
    card["flags"] = flags
    card["tags"] = board_cycle_tags(flags, ice=ice)
    card["headline"] = headline
    card["tone"] = tone
    card["cluster"] = cluster_path(hist, zt_n)
    card["spark"] = spark_values(hist, zt_n)
    card["hist"] = hist
    card["note"] = board_note(flags, leader_name, leader_boards, card.get("slot_name"), ice)
    card["focus"] = round(zt_n / max(len(zt), 1) * 100.0, 1) if zt else 0.0
    return card


def _board_etf_codes(boards: list[dict[str, Any]] | None) -> list[str]:
    """Collect exact + soft-mapped carrier ETF codes from board cards."""
    out: list[str] = []
    for board in boards or []:
        name = str(board.get("name") or "")
        spec = etf_spec_for_name(name) or etf_spec_soft_fallback(name)
        if not spec:
            continue
        code = normalize_code(spec[1])
        if code:
            out.append(code)
    return list(dict.fromkeys(out))


def _stable_mainline_lifecycle(
    hot_cards: list[dict[str, Any]],
    pin_cards: list[dict[str, Any]],
    hist_map: dict[str, list[dict[str, Any]]] | None,
    *,
    now: datetime,
    side_live: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the lifecycle panel so columns change at most once per trading day.

    After the close the live classification is final and stored as
    ``lifecycle_close:{date}``; before that the previous close is reused and
    ``side_live`` cards refresh frozen boards missing from today's hot list.
    """
    from market_desk.db import load_setting, save_setting

    today = now.strftime("%Y-%m-%d")
    final = is_trading_day(now) and _minutes(now) >= 15 * 60
    if final:
        out = build_mainline_lifecycle(hot_cards, pin_cards, final=True)
        if out.get("stage_map"):
            try:
                save_setting(f"lifecycle_close:{today}", out["stage_map"])
            except Exception:
                log.exception("save lifecycle close failed")
        return out
    frozen = None
    prev_day = _prev_trading_day(now)
    if prev_day:
        try:
            got = load_setting(f"lifecycle_close:{prev_day}")
            frozen = got if isinstance(got, dict) and got else None
        except Exception:
            frozen = None
    return build_mainline_lifecycle(
        hot_cards,
        pin_cards,
        hist_map=hist_map or {},
        frozen=frozen,
        final=False,
        side_live=side_live,
    )
