"""Persist live buy/sell signals for the session."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from market_desk.db import upsert_signal
from market_desk.filters import normalize_code
from market_desk.numbers import num

from market_desk.review.signals import (
    _dragon_hide_from_review,
    board_stage_lookup,
    compare_boards_to_mainline,
    is_pre_match_stamp,
    lookup_code_boards,
)


def record_session_signals(snapshot: dict[str, Any]) -> int:
    """Persist buy/sell recommendations for the current session. Return insert/update count.

    Buy sources (all paper cards with a price):
      - recommend → signal_type ``buy`` (含观察回踩全量)
      - side_recommend → ``buy_side``
      - link_recommend → ``buy_link``
      - watch_trial_recommend → ``buy_trial``
      - independent_recommend → ``buy_indep``
      - dragon_recommend → ``buy_dragon``
    Sell: ready items from sell_advice → ``sell`` with ``owner_user_id``.
    Note: shared engine snap keeps sell_advice empty; ready sells are
    recorded via ``record_sell_advice_signals`` after the personal layer.
    """
    trade_date = snapshot.get("trade_date") or ""
    if not trade_date:
        return 0
    try:
        from market_desk.calendar import is_trading_day

        if not is_trading_day(str(trade_date)):
            return 0
    except ValueError:
        return 0
    signaled_at = snapshot.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if is_pre_match_stamp(signaled_at):
        return 0
    phase = snapshot.get("phase") or ""
    verdict = snapshot.get("verdict") or {}
    action = verdict.get("action") or ""
    mainline = ((verdict.get("mainline") or {}).get("name")) or ""
    board_cards = list(snapshot.get("hot_boards") or []) + list(
        snapshot.get("pin_boards") or []
    )
    try:
        from market_desk.adapt import make_trade_context

        seg_key = str((verdict.get("segment") or {}).get("key") or "")
        trade_ctx = make_trade_context(
            segment_key=seg_key,
            signaled_at=signaled_at,
            metrics=snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else None,
        )
    except Exception:
        trade_ctx = {}
    from market_desk.gate_ledger import normalize_gate_notes

    market_gates = normalize_gate_notes(verdict.get("algo_notes"))
    stage_by_name = board_stage_lookup(snapshot)
    n = 0

    def _log_buy_items(
        items: list[dict[str, Any]] | None,
        *,
        signal_type: str,
        desk_source: str,
        action_label: str,
        board_fallback: str = "",
    ) -> int:
        """Upsert one buy-family recommend list; return rows written."""
        written = 0
        for item in items or []:
            code = normalize_code(item.get("code"))
            if not code:
                continue
            if signal_type == "buy_dragon" and _dragon_hide_from_review(item):
                continue
            kind = item.get("kind") or "stock"
            plan = num(item.get("plan_price"))
            price = plan if plan is not None else num(item.get("buy_price"))
            if price is None:
                price = num(item.get("last"))
            if price is None:
                continue
            board_names = lookup_code_boards(code, board_cards)
            fb = board_fallback or mainline
            if not board_names and fb:
                board_names = [fb]
            sticky_cmp = compare_boards_to_mainline(board_names, mainline, role="main")
            source_board = ""
            if desk_source in ("side", "link"):
                source_board = str(board_fallback or "").strip()
                if source_board in ("未明", "—"):
                    source_board = ""
            # Dragon cards may carry their own board (main / side / link).
            scope_role = str(item.get("dragon_scope") or "").strip()
            if (
                desk_source in ("dragon", "emotion_dragon", "mid_army_dragon")
                or scope_role in ("side", "link", "main")
            ):
                sb = str(item.get("source_board") or "").strip()
                if sb and sb not in ("未明", "—"):
                    source_board = sb
            origin_cmp = None
            if desk_source in ("side", "link") and source_board:
                origin_cmp = compare_boards_to_mainline(
                    board_names, source_board, role=desk_source
                )
            elif scope_role in ("side", "link") and source_board:
                origin_cmp = compare_boards_to_mainline(
                    board_names, source_board, role=scope_role
                )
            upsert_signal(
                {
                    "trade_date": trade_date,
                    "signaled_at": signaled_at,
                    "signal_type": signal_type,
                    "action": action_label,
                    "phase": phase,
                    "mainline": mainline,
                    "code": code,
                    "name": item.get("name") or "",
                    "kind": kind,
                    "price": float(price),
                    "last": num(item.get("last")),
                    "ready": 1 if item.get("ready") else 0,
                    "payload": {
                        "desk_source": desk_source,
                        "board_stage": stage_by_name.get(
                            source_board or (board_names[0] if board_names else "") or mainline
                        )
                        or "none",
                        "source_board": source_board or None,
                        "role_label": item.get("role_label"),
                        "plan_price": plan if plan is not None else price,
                        "wait_price": item.get("wait_price"),
                        "stop_price": item.get("stop_price"),
                        "chase_price": item.get("chase_price"),
                        "buy_price": item.get("buy_price"),
                        "qty": int(item.get("qty") or 0) or None,
                        "pct": item.get("pct"),
                        "trend": item.get("trend"),
                        "trend_quality": item.get("trend_quality"),
                        "trend_pending": bool(item.get("trend_pending")),
                        "trend_unknown": bool(item.get("trend_unknown")),
                        "trend_ok": bool(item.get("trend_ok")),
                        "trend_down": bool(item.get("trend_down")),
                        "confirm_fail": list(item.get("confirm_fail") or []),
                        "confirm_soft": list(item.get("confirm_soft") or []),
                        "minute": item.get("minute") if isinstance(item.get("minute"), dict) else None,
                        "reason": str(item.get("reason") or "")[:240] or None,
                        "block_ready": bool(item.get("block_ready")),
                        "near_entry": bool(item.get("near_entry")),
                        "ready_relaxed": bool(item.get("ready_relaxed")),
                        "probe_ok": bool(item.get("probe_ok")),
                        "fly_warn": bool(item.get("fly_warn")),
                        "size_cap_block": bool(item.get("size_cap_block")),
                        "link_board": bool(item.get("link_board")),
                        "watch_trial": bool(item.get("watch_trial")),
                        "dragon_scope": item.get("dragon_scope") or None,
                        "dragon_kind": item.get("dragon_kind") or None,
                        "board_names": sticky_cmp["boards"],
                        # Sticky compare kept for history / whitebox context.
                        "vs_mainline": sticky_cmp["vs_mainline"],
                        "board_match": sticky_cmp["match"],
                        "vs_source": (origin_cmp or {}).get("vs_mainline"),
                        "source_match": (origin_cmp or {}).get("match"),
                        "context": trade_ctx or None,
                        "cv": item.get("cv") if isinstance(item.get("cv"), dict) else None,
                        "market_gates": market_gates,
                    },
                }
            )
            written += 1
        return written

    rec = verdict.get("recommend") or {}
    n += _log_buy_items(
        list(rec.get("items") or []),
        signal_type="buy",
        desk_source="main",
        action_label=action or "观察回踩",
        board_fallback=mainline,
    )

    side_rec = verdict.get("side_recommend") or {}
    side_name = str((verdict.get("side_mainline") or {}).get("name") or "").strip()
    n += _log_buy_items(
        list(side_rec.get("items") or []),
        signal_type="buy_side",
        desk_source="side",
        action_label=f"观察支线·{side_name}" if side_name else "观察支线",
        board_fallback=side_name or mainline,
    )

    link_rec = verdict.get("link_recommend") or {}
    link_name = str((verdict.get("link_mainline") or {}).get("name") or "").strip()
    n += _log_buy_items(
        list(link_rec.get("items") or []),
        signal_type="buy_link",
        desk_source="link",
        action_label=f"板块联动·{link_name}" if link_name else "板块联动",
        board_fallback=link_name or mainline,
    )

    trial_rec = verdict.get("watch_trial_recommend") or {}
    n += _log_buy_items(
        list(trial_rec.get("items") or []),
        signal_type="buy_trial",
        desk_source="watch_trial",
        action_label="自选可试探",
        board_fallback=mainline,
    )

    indep_rec = verdict.get("independent_recommend") or {}
    n += _log_buy_items(
        list(indep_rec.get("items") or []),
        signal_type="buy_indep",
        desk_source="independent_pop",
        action_label="独立人气回踩",
        board_fallback=mainline,
    )

    dragon_rec = verdict.get("dragon_recommend") or {}
    n += _log_buy_items(
        list(dragon_rec.get("items") or []),
        signal_type="buy_dragon",
        desk_source="dragon",
        action_label="龙头排",
        board_fallback=mainline,
    )

    sell = snapshot.get("sell_advice") or {}
    try:
        sell_owner = int(snapshot.get("auth_user_id") or sell.get("owner_user_id") or 0)
    except (TypeError, ValueError):
        sell_owner = 0
    for item in sell.get("items") or []:
        if not item.get("ready"):
            continue
        code = normalize_code(item.get("code"))
        if not code:
            continue
        price = num(item.get("sell_price"))
        if price is None:
            price = num(item.get("last"))
        if price is None:
            continue
        board_names = lookup_code_boards(code, board_cards)
        if not board_names and mainline:
            board_names = [mainline]
        board_cmp = compare_boards_to_mainline(board_names, mainline)
        upsert_signal(
            {
                "trade_date": trade_date,
                "signaled_at": signaled_at,
                "signal_type": "sell",
                "action": item.get("role_label") or "建议卖出",
                "phase": phase,
                "mainline": mainline,
                "code": code,
                "name": item.get("name") or "",
                "kind": item.get("kind") or "stock",
                "price": float(price),
                "last": num(item.get("last")),
                "ready": 1,
                "owner_user_id": sell_owner,
                "payload": {
                    "buy_price": item.get("buy_price"),
                    "stop_price": item.get("stop_price"),
                    "pnl_pct": item.get("pnl_pct"),
                    "qty": item.get("qty"),
                    "exit_mode": item.get("exit_mode"),
                    "urgency": item.get("urgency"),
                    "band_mode": item.get("band_mode"),
                    "band_mode_zh": item.get("band_mode_zh"),
                    "daily_trend": item.get("daily_trend"),
                    "board_names": board_cmp["boards"],
                    "vs_mainline": board_cmp["vs_mainline"],
                    "board_match": board_cmp["match"],
                    "context": trade_ctx or None,
                    "owner_user_id": sell_owner,
                    "open_buffer_track": item.get("open_buffer_track"),
                    "open_buffer_phase": item.get("open_buffer_phase"),
                    "open_buffer_decision_price": item.get("open_buffer_decision_price"),
                },
            }
        )
        n += 1
    return n


def record_sell_advice_signals(snapshot: dict[str, Any]) -> int:
    """Persist ready personal sell_advice items (shared snap has empty sells).

    Sell cards are built per-user in ``attach_personal_layer``; the engine
    snapshot always stores an empty ``sell_advice``. Call this after the
    personal layer is attached so 复盘 can see 建议卖出 rows owned by that user.
    """
    trade_date = snapshot.get("trade_date") or ""
    if not trade_date:
        return 0
    try:
        owner = int(snapshot.get("auth_user_id") or 0)
    except (TypeError, ValueError):
        owner = 0
    if owner <= 0:
        return 0
    sell = snapshot.get("sell_advice") or {}
    items = list(sell.get("all_items") or sell.get("items") or [])
    ready = [x for x in items if x.get("ready")]
    if not ready:
        return 0
    # Reuse the session recorder with a slim payload that only carries sells.
    slim = {
        "trade_date": trade_date,
        "updated_at": snapshot.get("updated_at"),
        "phase": snapshot.get("phase") or "",
        "verdict": snapshot.get("verdict") or {},
        "hot_boards": snapshot.get("hot_boards") or [],
        "pin_boards": snapshot.get("pin_boards") or [],
        "metrics": snapshot.get("metrics"),
        "auth_user_id": owner,
        "sell_advice": {"items": ready, "owner_user_id": owner},
    }
    # Avoid re-logging buys: temporarily strip recommend trees.
    v = dict(slim["verdict"])
    for key in (
        "recommend",
        "side_recommend",
        "link_recommend",
        "watch_trial_recommend",
        "independent_recommend",
        "dragon_recommend",
    ):
        v.pop(key, None)
    slim["verdict"] = v
    return record_session_signals(slim)
