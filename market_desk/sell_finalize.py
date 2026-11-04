"""Shared sell-advice post-processing for personal desk and sim exits."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

log = logging.getLogger(__name__)


def _is_hard_sell(item: dict[str, Any]) -> bool:
    """True for stop / clear exits that must stay ready through soft-gate failures."""
    urg = str(item.get("urgency") or "").strip().lower()
    mode = str(item.get("exit_mode") or "").strip().lower()
    return urg == "stop" or mode == "clear"


def demote_soft_sells_on_gate_error(
    advice: dict[str, Any] | None,
    *,
    why: str = "分时闸异常，软卖暂缓",
) -> dict[str, Any]:
    """Demote ready soft take/trim sells; leave hard stops ready."""
    out = dict(advice or {})
    pool_key = "all_items" if out.get("all_items") is not None else "items"
    changed = 0
    new_items: list[dict[str, Any]] = []
    for raw in out.get(pool_key) or []:
        item = dict(raw)
        if item.get("ready") and not _is_hard_sell(item):
            item["ready"] = False
            item["exit_mode"] = "hold"
            item["sell_pct"] = 0
            item["sell_qty"] = 0
            item["gate_error_hold"] = True
            role = str(item.get("role_label") or "卖点")
            if "闸异常" not in role:
                item["role_label"] = f"{role}·闸异常暂缓"
            reason = str(item.get("reason") or "")
            if why not in reason:
                item["reason"] = f"{why}；{reason}" if reason else why
            changed += 1
        new_items.append(item)
    if changed:
        out[pool_key] = new_items
        if pool_key == "all_items":
            out["items"] = new_items[:4]
        try:
            from market_desk.verdict.sell_advice import _refresh_sell_advice_summary

            out = _refresh_sell_advice_summary(out)
        except Exception:
            log.exception("refresh sell summary after gate-error demote failed")
        out["gate_error_demoted"] = changed
    return out


def finalize_sell_advice(
    advice: dict[str, Any] | None,
    *,
    minutes_by_code: dict[str, list] | None = None,
    minutes_for: Callable[[list[str]], dict[str, list]] | None = None,
    now: datetime | None = None,
    verdict: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
    reconcile: bool = True,
    hold_before_match: bool = True,
    ui_top_n: int | None = 4,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Apply open-buffer, minute gates, buy/sell reconcile, and pre-match hold.

    Returns ``(advice, reconciled_verdict)``. ``reconciled_verdict`` is set when
    reconcile ran; otherwise ``None`` (caller keeps its input verdict).

    Always sets ``alert_items`` to the full post-gate pool (for push / diary).
    When ``ui_top_n`` is set, ``items`` is truncated for the desk card strip;
    ``all_items`` remains until the caller pops it after signal recording.
    Hard stops are never demoted by minute-gate failures.
    """
    from market_desk.config import SELL_OPEN_WATCH_MINUTES
    from market_desk.minute_confirm import apply_sell_minute_gates
    from market_desk.sell_open_buffer import apply_sell_open_buffer, hold_sells_before_match
    from market_desk.settings import setting

    out = dict(advice or {})
    pool = list(out.get("all_items") or out.get("items") or [])
    need_codes: list[str] = []
    for it in pool:
        code = str(it.get("code") or "").zfill(6)
        if not code or len(code) != 6:
            continue
        if it.get("minute_gate") or it.get("open_buffer_track") or it.get("ready"):
            need_codes.append(code)
    need_codes = sorted(set(need_codes))

    minutes: dict[str, list] = dict(minutes_by_code or {})
    if need_codes and not minutes and callable(minutes_for):
        try:
            minutes = dict(minutes_for(need_codes) or {})
        except Exception:
            log.exception("minutes_for failed for sell finalize")
            minutes = {}

    try:
        watch_m = int(setting("sell_open_watch_minutes", SELL_OPEN_WATCH_MINUTES))
    except Exception:
        watch_m = 15

    gate_failed = False
    try:
        out = apply_sell_open_buffer(
            out,
            now=now,
            watch_minutes=watch_m,
            minutes_by_code=minutes,
        )
        out = apply_sell_minute_gates(out, minutes)
    except Exception:
        log.exception("sell open-buffer / minute gates failed")
        gate_failed = True
        out = demote_soft_sells_on_gate_error(out)

    reconciled: dict[str, Any] | None = None
    if reconcile and verdict is not None:
        try:
            from market_desk.verdict.sell_advice import reconcile_buy_sell_conflict

            reconciled, out = reconcile_buy_sell_conflict(verdict, out, positions)
        except Exception:
            log.exception("reconcile buy/sell conflict failed")

    if hold_before_match:
        try:
            out = hold_sells_before_match(out)
        except Exception:
            log.exception("hold_sells_before_match failed")

    all_items = list(out.get("all_items") or out.get("items") or [])
    out["alert_items"] = [dict(x) for x in all_items]
    if all_items:
        out["all_items"] = all_items
    if ui_top_n is not None:
        out["items"] = all_items[: max(0, int(ui_top_n))]
    else:
        out["items"] = list(all_items)
    if gate_failed:
        out["gate_error"] = True
    return out, reconciled
