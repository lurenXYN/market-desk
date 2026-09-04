"""Intraday session segments and mainline-switch helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any


# Ordered segments used for the day strip on the desk.
SEGMENT_ORDER = ("auction", "open30", "morning", "afternoon")
SEGMENT_LABELS = {
    "auction": "竞价",
    "open30": "开盘半小时",
    "morning": "午前",
    "afternoon": "午后",
    "closed": "休市",
}


def session_segment(now: datetime) -> dict[str, Any]:
    """Classify the current clock into an intraday trading segment."""
    hhmm = now.hour * 100 + now.minute
    weekday = now.weekday() < 5
    if not weekday:
        key = "closed"
        note = "周末休市"
    elif 915 <= hhmm < 930:
        key = "auction"
        note = "竞价认主线，不定价买入"
    elif 930 <= hhmm < 1000:
        key = "open30"
        note = "开盘半小时波动大，确认后也宜小仓"
    elif 1000 <= hhmm < 1130:
        key = "morning"
        note = "午前可按主线正常定价"
    elif 1130 <= hhmm < 1300:
        key = "closed"
        note = "午休，沿用午前结论"
    elif 1300 <= hhmm < 1500:
        key = "afternoon"
        note = "午后盯退潮与高潮，买卖更谨慎"
    else:
        key = "closed"
        note = "已收盘或未开盘"
    display_key = key
    if key == "closed":
        if 1130 <= hhmm < 1300:
            display_key = "morning"
            note = "午休，高亮午前结论"
        elif hhmm >= 1500 or hhmm < 915:
            display_key = "afternoon"
            note = "休市，高亮午后/最近结论"
    return {
        "key": key,
        "display_key": display_key,
        "label": SEGMENT_LABELS.get(key, key),
        "note": note,
        "hhmm": hhmm,
        "active": key in SEGMENT_ORDER,
    }


def apply_segment_bias(
    action: str,
    reason: str,
    *,
    segment_key: str,
    status: str,
    phase: str,
) -> tuple[str, str, str]:
    """Soften or tighten the live action by intraday segment.

    Returns (action, reason, size_hint).
    """
    size_hint = ""
    if segment_key == "auction":
        return "观望", reason if "竞价" in reason else f"竞价阶段：{reason}", "竞价不做买入"
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
