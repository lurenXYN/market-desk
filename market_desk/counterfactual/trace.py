"""Per-refresh gate trace: which gates stopped a buy card while price sat at plan."""

from __future__ import annotations

from typing import Any

# Observe-only channels never arm ready by design (policy, not a market judgement).
_CHANNEL_KEYS = {
    "side": "通道·支线观察",
    "link": "通道·联动观察",
    "independent_pop": "通道·独立人气观察",
    "watch_trial": "通道·自选试探",
}
UNKNOWN_KEY = "未明"


def block_arm_reasons(verdict: dict[str, Any] | None) -> list[str]:
    """Return verdict-level reasons that forced every card to watch-only this refresh.

    Args:
        verdict: Live verdict (``segment``, ``auction_only``, ``auction_open_bridge``).

    Returns:
        Zero or more of 开盘静默 / 仅竞价 / 竞价开盘桥.
    """
    v = verdict or {}
    out: list[str] = []
    if (v.get("segment") or {}).get("open_mute"):
        out.append("开盘静默")
    if v.get("auction_only"):
        out.append("仅竞价")
    if (v.get("auction_open_bridge") or {}).get("revoke_probe"):
        out.append("竞价开盘桥")
    return out


def gate_kind(key: str) -> str:
    """Classify a blocker key as 通道 / 开盘 / 个股 / 未明 for grouping."""
    if key.startswith("通道·"):
        return "通道"
    if key in ("开盘静默", "仅竞价", "竞价开盘桥"):
        return "开盘"
    if key == UNKNOWN_KEY:
        return "未明"
    return "个股"


def item_blockers(
    item: dict[str, Any],
    *,
    desk_source: str | None = None,
    arm_block: list[str] | None = None,
) -> list[str]:
    """List the gates that keep one buy card (or stored payload) from arming ready.

    Mirrors the arm checks in ``mark_pullback_entries`` plus post-arm hard fails:
    observe-only channel, verdict-level arm block, daily downtrend, ``block_ready``
    (size cap / sell conflict / orphan map), minute not confirmed, and hard
    ``confirm_fail`` labels bucketed like the gate-kill stats.

    Args:
        item: Live recommend item, or a stored signal payload (uses
            ``confirm_fail_hist`` / ``final_fail`` when present).
        desk_source: Recording channel (main / side / link / dragon / ...).
        arm_block: Verdict-level reasons from ``block_arm_reasons``.

    Returns:
        De-duplicated blocker keys; ``["未明"]`` when nothing explains the miss.
    """
    from market_desk.review.hit_rates import _gate_bucket
    from market_desk.verdict.common import hard_confirm_fails

    keys: list[str] = []

    def _add(key: str) -> None:
        if key and key not in keys:
            keys.append(key)

    src = str(desk_source or item.get("desk_source") or "").strip()
    if src in _CHANNEL_KEYS:
        _add(_CHANNEL_KEYS[src])
    elif str(item.get("dragon_scope") or "main") in ("side", "link"):
        _add("通道·非主线龙头")
    for reason in arm_block or []:
        _add(str(reason))
    trend_down = bool(item.get("trend_down"))
    if trend_down:
        _add("日线下降")
    if item.get("block_ready"):
        if item.get("size_cap_block"):
            _add("禁亮灯·仓位上限")
        elif item.get("sell_conflict_block"):
            _add("禁亮灯·持仓冲突")
        elif item.get("crowd_block"):
            _add("禁亮灯·极端拥挤")
        else:
            _add("禁亮灯·映射/其它")
    minute = item.get("minute")
    if isinstance(minute, dict) and minute and minute.get("ok") is not True:
        _add("分时未确认")
    fails = item.get("confirm_fail_hist") or item.get("final_fail") or item.get("confirm_fail") or []
    for flag in hard_confirm_fails(list(fails)):
        bucket = _gate_bucket(flag)
        if bucket == "日线" and trend_down:
            continue
        if bucket == "分时" and "分时未确认" in keys:
            continue
        if "拥挤" in bucket and "禁亮灯·极端拥挤" in keys:
            continue
        if bucket == "总仓上限" and "禁亮灯·仓位上限" in keys:
            continue
        _add(f"卡·{bucket}")
    return keys or [UNKNOWN_KEY]


def trace_now(
    item: dict[str, Any],
    *,
    desk_source: str,
    arm_block: list[str] | None = None,
) -> dict[str, Any]:
    """Snapshot this refresh's touch / ready / blockers for one buy card.

    "Touch" = the card's price sat in its entry band (``near_entry``) or at /
    under ``plan_price`` — the moment a fill would actually have happened.

    Returns:
        ``{"touch", "ready", "px", "blk"}``; ``blk`` is empty unless touched and gated.
    """
    last = item.get("last")
    touch = bool(item.get("near_entry"))
    if not touch:
        try:
            touch = 0 < float(last) <= float(item.get("plan_price") or item.get("buy_price"))
        except (TypeError, ValueError):
            touch = False
    ready = bool(item.get("ready") or item.get("probe_ok"))
    blk = item_blockers(item, desk_source=desk_source, arm_block=arm_block) if touch and not ready else []
    return {"touch": touch, "ready": ready, "px": last, "blk": blk}


def merge_trace(old: dict[str, Any] | None, now: dict[str, Any] | None, at: str) -> dict[str, Any] | None:
    """Fold one refresh snapshot into the same-day accumulated trace.

    Accumulated fields: ``seen`` (traced refreshes — marks the row as traced),
    ``lo_px`` / ``lo_at`` (lowest live price seen after the card existed),
    ``touch_n`` / ``touch_at`` / ``touch_px``, ``ready_n``,
    ``blocked_n``, ``blk`` (blocker → touched-and-gated refresh count) and
    ``first_blk`` / ``first_blk_at`` (the blockers at the first gated touch —
    the fill that the gates actually cost).

    Args:
        old: Previously stored ``payload.cf`` (may be None).
        now: Output of ``trace_now`` for this refresh (may be None).
        at: Refresh timestamp (``YYYY-MM-DD HH:MM:SS``).

    Returns:
        Updated trace, or None when nothing has been observed yet.
    """
    cf = dict(old or {})
    if not now:
        return cf or None
    cf["seen"] = int(cf.get("seen") or 0) + 1
    hms = str(at or "")[11:19] or str(at or "")
    try:
        px = float(now.get("px"))
    except (TypeError, ValueError):
        px = 0.0
    if px > 0 and (cf.get("lo_px") is None or px < float(cf["lo_px"])):
        cf["lo_px"] = px
        cf["lo_at"] = hms
    if not now.get("touch"):
        return cf
    cf["touch_n"] = int(cf.get("touch_n") or 0) + 1
    cf.setdefault("touch_at", hms)
    cf.setdefault("touch_px", now.get("px"))
    if now.get("ready"):
        cf["ready_n"] = int(cf.get("ready_n") or 0) + 1
        return cf
    cf["blocked_n"] = int(cf.get("blocked_n") or 0) + 1
    blk = dict(cf.get("blk") or {})
    for key in now.get("blk") or []:
        blk[key] = int(blk.get(key) or 0) + 1
    cf["blk"] = dict(sorted(blk.items(), key=lambda kv: -kv[1])[:12])
    if not cf.get("first_blk"):
        cf["first_blk"] = list(now.get("blk") or [])[:8]
        cf["first_blk_at"] = hms
    return cf
