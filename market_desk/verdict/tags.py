"""Sell-card tags, next-action enrichment, and position hint merging."""

from __future__ import annotations

from typing import Any
from market_desk.filters import normalize_code

from market_desk.verdict.common import _px


def _position_exempt_yday_weak(row: dict[str, Any]) -> bool:
    """Return True when a position should skip「昨买今弱」half-trim."""
    from market_desk.config import SELL_INDEPENDENT_POP_EXEMPT_YDAY

    if not SELL_INDEPENDENT_POP_EXEMPT_YDAY:
        return False
    note = str(row.get("note") or "")
    if "独立人气" in note or "independent_pop" in note:
        return True
    if str(row.get("desk_source") or "").strip().lower() == "independent_pop":
        return True
    return False


def _yday_buy_weak(
    *,
    buy_day: str | None,
    trade_date: str | None,
    t1_locked: bool,
    day_pct: float | None,
) -> dict[str, Any]:
    """Detect prior-day buys facing a weak session (anti panic full-exit window).

    Active when the bag is first sellable (not T+1), bought within a few calendar
    days, and today's change is at/below the weak threshold.
    """
    from market_desk.config import (
        SELL_YDAY_BUY_MAX_AGE_DAYS,
        SELL_YDAY_BUY_WEAK_PCT,
    )

    out: dict[str, Any] = {
        "active": False,
        "age_days": None,
        "weak_pct": float(SELL_YDAY_BUY_WEAK_PCT),
    }
    if t1_locked or day_pct is None:
        return out
    day = str(trade_date or "").strip()[:10]
    bought = str(buy_day or "").strip()[:10]
    if not day or not bought or bought >= day:
        return out
    try:
        from datetime import datetime

        age = (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(bought, "%Y-%m-%d")).days
    except ValueError:
        return out
    out["age_days"] = age
    if age < 1 or age > int(SELL_YDAY_BUY_MAX_AGE_DAYS):
        return out
    if float(day_pct) > float(SELL_YDAY_BUY_WEAK_PCT):
        return out
    out["active"] = True
    return out


def _sell_wave_adj(verdict: dict[str, Any] | None) -> dict[str, Any]:
    """Soft sell-band nudge from index Elliott primary scenario (observe-only)."""
    from market_desk.config import SELL_WAVE_END_SOFT_DELTA, SELL_WAVE_W3_SOFT_EXTRA

    ew = (verdict or {}).get("elliott") if isinstance(verdict, dict) else None
    if not isinstance(ew, dict) or not ew.get("ok"):
        return {}
    primary = ew.get("primary") or {}
    wid = str(primary.get("id") or "")
    if wid == "imp_up_w3":
        return {
            "soft_extra": float(SELL_WAVE_W3_SOFT_EXTRA),
            "take_mult": 1.08,
            "tag": "浪3持有",
        }
    if wid in ("imp_up_w5", "corr_c"):
        return {
            "soft_extra": float(SELL_WAVE_END_SOFT_DELTA),
            "take_mult": 0.95,
            "tag": "浪末偏紧",
        }
    return {}


def _enrich_sell_next_action(
    item: dict[str, Any],
    *,
    hold_peak: float,
    last_sell_price: Any = None,
    digits: int = 2,
) -> dict[str, Any]:
    """Attach next-action label and trigger prices for position-row guidance.

    Actions: hold / half / clear / watch. After a half-trim, prefer the regret
    window anchors (break last sell price or deep pullback from hold peak).
    """
    ready = bool(item.get("ready"))
    exit_mode = str(item.get("exit_mode") or "hold")
    regret = bool(item.get("regret_hold"))
    partial = bool(item.get("partial_done"))
    t1 = bool(item.get("t1_locked"))
    pb_deep = float(item.get("pb_deep") or 0)
    stop = item.get("stop_price")
    target = item.get("target_price")
    sell_px = item.get("sell_price")

    half_anchor = None
    try:
        if last_sell_price not in (None, "", 0) and partial:
            half_anchor = _px(float(last_sell_price), digits)
    except (TypeError, ValueError):
        half_anchor = None
    if half_anchor is None and item.get("half_anchor_price") not in (None, ""):
        try:
            half_anchor = _px(float(item["half_anchor_price"]), digits)
        except (TypeError, ValueError):
            half_anchor = None

    deep_clear = None
    try:
        peak = float(hold_peak or item.get("hold_peak") or 0)
    except (TypeError, ValueError):
        peak = 0.0
    if peak > 0 and pb_deep > 0:
        deep_clear = _px(peak * (1.0 - pb_deep / 100.0), digits)

    if t1:
        action, zh = "watch", "继续观察"
        trigger = None
        note = "T+1 隔日可卖"
    elif ready and exit_mode == "clear":
        action, zh = "clear", "清仓"
        trigger = sell_px
        note = str(item.get("role_label") or "建议清仓")
    elif ready and exit_mode == "half":
        action, zh = "half", "减半"
        trigger = sell_px
        note = str(item.get("role_label") or "建议先减一半")
    elif regret or (partial and not ready):
        action, zh = "watch", "继续观察"
        trigger = half_anchor if half_anchor is not None else deep_clear
        bits: list[str] = []
        if half_anchor is not None:
            bits.append(f"破减仓价 {half_anchor}")
        if deep_clear is not None:
            bits.append(f"深回撤线 {deep_clear}")
        note = ("再清：" + " / ".join(bits)) if bits else "余仓盯止损"
    else:
        action, zh = "hold", "持有"
        trigger = stop
        bits = []
        if stop is not None:
            bits.append(f"止损 {stop}")
        if target is not None:
            bits.append(f"目标 {target}")
        note = " · ".join(bits) if bits else "继续持有"

    item["next_action"] = action
    item["next_action_zh"] = zh
    item["next_action_note"] = note.strip()
    item["trigger_price"] = trigger
    item["half_anchor_price"] = half_anchor
    item["deep_clear_price"] = deep_clear
    if peak > 0:
        item["hold_peak"] = round(peak, 4)
    return item


def attach_position_sell_hints(
    positions: list[dict[str, Any]] | None,
    sell_items: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge sell next-action / half-anchor fields onto position rows."""
    by_id: dict[Any, dict[str, Any]] = {}
    by_code: dict[str, dict[str, Any]] = {}
    for it in sell_items or []:
        if it.get("id") is not None:
            by_id[it.get("id")] = it
        code = normalize_code(it.get("code"))
        if code:
            by_code[code] = it
    out: list[dict[str, Any]] = []
    for row in positions or []:
        item = dict(row)
        if item.get("closed") or int(item.get("qty") or 0) <= 0:
            # Still surface today's trim price on closed / flat rows.
            try:
                if item.get("last_sell_price") not in (None, "") and int(
                    item.get("day_sold_qty") or 0
                ) > 0:
                    item["half_anchor_price"] = float(item["last_sell_price"])
            except (TypeError, ValueError):
                pass
            out.append(item)
            continue
        hint = by_id.get(item.get("id"))
        if hint is None:
            hint = by_code.get(normalize_code(item.get("code")))
        if not hint:
            out.append(item)
            continue
        item["next_action"] = hint.get("next_action")
        item["next_action_zh"] = hint.get("next_action_zh")
        item["next_action_note"] = hint.get("next_action_note")
        item["trigger_price"] = hint.get("trigger_price")
        item["half_anchor_price"] = hint.get("half_anchor_price")
        item["deep_clear_price"] = hint.get("deep_clear_price")
        item["exit_mode"] = hint.get("exit_mode")
        item["sell_ready"] = bool(hint.get("ready"))
        item["regret_hold"] = bool(hint.get("regret_hold"))
        item["role_label"] = hint.get("role_label")
        item["open_buffer_track"] = hint.get("open_buffer_track")
        item["open_buffer_phase"] = hint.get("open_buffer_phase")
        out.append(item)
    return out


def _sell_tune_tags(
    *,
    band: dict[str, Any],
    daily_up: bool,
    at_tip: bool,
    rel_strong: bool,
    carrier_rel_strong: bool,
    carrier_rel_weak: bool,
    flow_pressure: bool,
    wave_tag: str | None,
    yday_repaired: bool,
    regret: bool,
) -> list[str]:
    """Build up to four short attribution chips for the sell card."""
    tags: list[str] = []
    sb = band.get("sell_bias") or {}
    if sb.get("widen"):
        tags.append("复盘卖早")
    elif sb.get("tighten"):
        tags.append("复盘卖准")
    mfe = (band.get("segment_sell") or {}).get("mfe") or {}
    if mfe.get("widen"):
        tags.append("MFE放宽")
    elif mfe.get("tighten"):
        tags.append("MFE收紧")
    if daily_up:
        tags.append("日线↑")
    if at_tip:
        tags.append("贴尖")
    if rel_strong or carrier_rel_strong:
        tags.append("相对强")
    elif carrier_rel_weak:
        tags.append("弱于载体")
    if flow_pressure:
        tags.append("资金流出")
    if wave_tag:
        tags.append(str(wave_tag))
    if yday_repaired:
        tags.append("今弱已修复")
    if regret:
        tags.append("反悔窗")
    # De-dupe preserve order, cap 4.
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= 4:
            break
    return out
