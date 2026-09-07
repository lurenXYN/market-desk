"""Intraday session segments and mainline-switch helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any


# Ordered segments used for the day strip on the desk.
SEGMENT_ORDER = ("auction", "open30", "morning", "afternoon")
SEGMENT_LABELS = {
    "auction": "竞价",
    "open30": "开盘半小时",
    "open_mute": "开盘静音",
    "morning": "午前",
    "afternoon": "午后",
    "closed": "休市",
}


def session_segment(
    now: datetime,
    *,
    open_mute_minutes: int = 5,
) -> dict[str, Any]:
    """Classify the current clock into an intraday trading segment."""
    minutes = now.hour * 60 + now.minute
    hhmm = now.hour * 100 + now.minute
    weekday = now.weekday() < 5
    mute_m = max(0, int(open_mute_minutes or 0))
    open_mute = False
    if not weekday:
        key = "closed"
        note = "周末休市"
    elif 9 * 60 + 15 <= minutes < 9 * 60 + 30:
        key = "auction"
        note = "竞价认主线，不定价买入"
    elif 9 * 60 + 30 <= minutes < 10 * 60:
        key = "open30"
        mute_until = 9 * 60 + 30 + mute_m
        open_mute = bool(mute_m and minutes < mute_until)
        if open_mute:
            left = max(0, mute_until - minutes)
            note = f"开盘静音 {mute_m} 分钟（剩约 {left} 分），只看不买"
        else:
            note = "开盘半小时波动大，确认后也宜小仓"
    elif 10 * 60 <= minutes < 11 * 60 + 30:
        key = "morning"
        note = "午前可按主线正常定价"
    elif 11 * 60 + 30 <= minutes < 13 * 60:
        key = "closed"
        note = "午休，沿用午前结论"
    elif 13 * 60 <= minutes < 15 * 60:
        key = "afternoon"
        note = "午后盯退潮与高潮，买卖更谨慎"
    else:
        key = "closed"
        note = "已收盘或未开盘"
    display_key = key
    if key == "closed":
        if 11 * 60 + 30 <= minutes < 13 * 60:
            display_key = "morning"
            note = "午休，高亮午前结论"
        elif minutes >= 15 * 60 or minutes < 9 * 60 + 15:
            display_key = "afternoon"
            note = "休市，高亮午后/最近结论"
    label_key = "open_mute" if open_mute else key
    return {
        "key": key,
        "display_key": display_key,
        "label": SEGMENT_LABELS.get(label_key, key),
        "note": note,
        "hhmm": hhmm,
        "open_mute": open_mute,
        "open_mute_minutes": mute_m,
        "active": key in SEGMENT_ORDER,
    }


def apply_segment_bias(
    action: str,
    reason: str,
    *,
    segment_key: str,
    status: str,
    phase: str,
    open_mute: bool = False,
    open_mute_minutes: int = 5,
) -> tuple[str, str, str]:
    """Soften or tighten the live action by intraday segment.

    Returns (action, reason, size_hint).
    """
    size_hint = ""
    if segment_key == "auction":
        return "观望", reason if "竞价" in reason else f"竞价阶段：{reason}", "竞价不做买入"
    if open_mute and segment_key == "open30":
        mins = max(0, int(open_mute_minutes or 0))
        size_hint = f"开盘前 {mins} 分钟静音，只看不买"
        if action == "可买入":
            return "观察回踩", f"开盘静音，不定价买入：{reason}", size_hint
        return action, reason if "静音" in reason else f"开盘静音：{reason}", size_hint
    if segment_key == "open30":
        size_hint = "开盘半小时建议更小仓"
        if action == "可买入" and status != "确认中":
            return "观察回踩", f"开盘半小时，结构未完全确认：{reason}", size_hint
        if action == "可买入":
            return action, f"开盘半小时已确认，但仍宜小仓：{reason}", size_hint
        return action, reason, size_hint
    if segment_key == "afternoon":
        size_hint = "午后优先兑现/控风险"
        if action == "可买入" and phase in ("高潮", "恐慌"):
            return (
                "观察回踩",
                f"午后相位={phase}，新开仓降级为观察回踩：{reason}",
                size_hint,
            )
        if action == "可买入" and status == "尖峰禁追":
            return "观察回踩", f"午后尖峰不追：{reason}", size_hint
        return action, reason, size_hint
    if segment_key == "morning":
        size_hint = "午前可按建议仓执行"
        return action, reason, size_hint
    return action, reason, "休市仅回顾"


def segment_snapshot_row(
    trade_date: str,
    segment: dict[str, Any],
    verdict: dict[str, Any],
    phase: str,
    temperature: int | None,
    updated_at: str,
) -> dict[str, Any]:
    """Build a compact row for persisting one segment conclusion."""
    main = verdict.get("mainline") or {}
    return {
        "trade_date": trade_date,
        "segment": segment.get("key"),
        "label": segment.get("label"),
        "action": verdict.get("action"),
        "mainline": main.get("name") or "",
        "phase": phase,
        "temperature": temperature,
        "reason": verdict.get("reason") or "",
        "size_hint": verdict.get("segment_size_hint") or "",
        "updated_at": updated_at,
        "current": True,
    }
