"""Sell decision post-processing: exit-band tuning and guards that demote a fired sell."""

from __future__ import annotations

from typing import Any

from market_desk.verdict.tags import _sell_wave_adj


def _tune_exit_band(
    band: dict[str, Any],
    *,
    verdict: dict[str, Any],
    similar: dict[str, Any] | None,
    sell_bias: dict[str, Any] | None,
    theme_label: str,
    mainline_fade: bool,
) -> tuple[bool, bool, dict[str, Any]]:
    """Scale the exit band in place for similar-day urgency, theme outflow and wave context.

    Returns ``(flow_pressure, flow_fade, wave_adj)`` for the downstream soft-exit floors.
    """
    # Similar-day sell urgency + theme fund outflow: nudge soft floors earlier.
    sell_gate = str((similar or {}).get("sell_gate") or "")
    theme_yi = None
    for t in verdict.get("sell_themes") or []:
        if str(t.get("name") or "") == theme_label:
            theme_yi = t.get("main_yi")
            break
    flow_pressure = False
    flow_fade = False
    try:
        from market_desk.config import SELL_FLOW_OUT_YI

        if theme_yi is not None and float(theme_yi) <= float(SELL_FLOW_OUT_YI):
            flow_pressure = True
            flow_fade = bool(mainline_fade)
    except (TypeError, ValueError):
        flow_pressure = False
        flow_fade = False
    wave_adj = _sell_wave_adj(verdict)
    if sell_gate == "urgent" or flow_fade:
        from market_desk.config import SELL_SIM_URGENT_FLOOR

        pb_scale = 0.88 if sell_gate == "urgent" else 0.93
        # Soft floor when review already tightened — avoid double-cut.
        if sell_bias and sell_bias.get("tighten"):
            pb_scale = max(float(SELL_SIM_URGENT_FLOOR), pb_scale)
        band["pb_light"] = float(band["pb_light"]) * pb_scale
        band["pb_deep"] = float(band["pb_deep"]) * pb_scale
        band["take_pnl"] = float(band["take_pnl"]) * pb_scale
        if sell_gate == "urgent":
            band["mode_zh"] = f"{band['mode_zh']}·相似日急"
        elif flow_fade:
            band["mode_zh"] = f"{band['mode_zh']}·资金+退潮"
    elif sell_gate == "hold" and band["mode"] != "tight":
        band["pb_light"] = float(band["pb_light"]) * 1.06
        band["take_pnl"] = float(band["take_pnl"]) * 1.06
        band["mode_zh"] = f"{band['mode_zh']}·相似日持"
    # Wave soft take mult (observe-only; does not flip regime).
    try:
        w_mult = float(wave_adj.get("take_mult") or 1.0)
        if abs(w_mult - 1.0) > 0.01:
            for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
                band[key] = float(band[key]) * w_mult
            if wave_adj.get("tag"):
                band["mode_zh"] = f"{band['mode_zh']}·{wave_adj['tag']}"
    except (TypeError, ValueError):
        pass
    if flow_pressure and not flow_fade:
        band["mode_zh"] = f"{band['mode_zh']}·资金流出"
    return flow_pressure, flow_fade, wave_adj


def _apply_sell_guards(
    dec: dict[str, Any],
    *,
    row: dict[str, Any],
    verdict: dict[str, Any],
    theme_ctx: dict[str, Any],
    trend: dict[str, Any],
    yday_weak: dict[str, Any],
    t1_locked: bool,
    buy_day: str | None,
    partial_done: bool,
    at_tip: bool,
    rel_strong: bool,
    carrier_rel_strong: bool,
    day_weak: bool,
    last: Any,
    pnl_pct: float | None,
    pullback: float | None,
    pnl_stop: float,
    pb_deep: float,
    take_deep_pnl: float,
    target: float,
) -> tuple[bool, bool]:
    """Demote a fired sell decision through the post-decision guard chain.

    Order: 昨买今弱 clear→half, already-trimmed-today, regret window, fresh
    mainline-switch guard, then T+1 lock. ``dec`` carries ``urgency``, ``ready``,
    ``exit_mode``, ``role_label``, ``sell_price``, ``sell_pct`` and ``reason_parts``
    and is updated in place. Returns ``(regret_hold, switch_guard_held)``.
    """
    urgency = dec["urgency"]
    ready = dec["ready"]
    exit_mode = dec["exit_mode"]
    role_label = dec["role_label"]
    sell_price = dec["sell_price"]
    sell_pct = dec["sell_pct"]
    reason_parts = dec["reason_parts"]
    mainline = verdict.get("mainline") or {}
    theme_role = str(theme_ctx.get("role") or "")

    # 「昨买今弱」: demote panic clears to half unless loss is clearly deeper.
    if ready and yday_weak.get("active") and exit_mode == "clear":
        from market_desk.config import SELL_YDAY_BUY_CLEAR_EXTRA

        keep_clear = False
        clear_floor = float(pnl_stop) + float(SELL_YDAY_BUY_CLEAR_EXTRA)
        if urgency == "stop" and pnl_pct is not None and float(pnl_pct) <= clear_floor:
            keep_clear = True
        elif (
            urgency == "take"
            and pullback is not None
            and pullback >= pb_deep
            and pnl_pct is not None
            and float(pnl_pct) >= take_deep_pnl
        ):
            keep_clear = True
        if not keep_clear:
            exit_mode = "half"
            sell_pct = 50
            if "清仓" in role_label:
                role_label = role_label.replace("清仓", "先减")
            if "昨买今弱" not in role_label:
                role_label = f"昨买今弱·{role_label}"
            reason_parts.insert(
                0,
                f"昨买今弱：默认先减一半；清仓需更深亏（约≤{clear_floor:.1f}%）或深结构回撤",
            )

    # Already trimmed today: do not keep nagging half on soft/take; keep stop/clear.
    if (
        ready
        and partial_done
        and exit_mode == "half"
        and urgency in ("trim", "take")
    ):
        ready = False
        exit_mode = "hold"
        sell_pct = 0
        role_label = "今日已减·盯止损"
        sell_price = target
        reason_parts.insert(0, "今日已减，余仓盯止损")

    # Regret window: after a half-trim, don't escalate soft/take clears while still strong.
    regret_hold = False
    if ready and partial_done and exit_mode == "clear" and urgency in ("take", "trim"):
        from market_desk.config import SELL_REGRET_ENABLED

        if SELL_REGRET_ENABLED:
            still_strong = bool(at_tip or rel_strong or carrier_rel_strong)
            allow_clear = False
            if (
                pullback is not None
                and pullback >= pb_deep
                and pnl_pct is not None
                and float(pnl_pct) >= take_deep_pnl
            ):
                allow_clear = True
            try:
                last_sell_px = row.get("last_sell_price")
                if last_sell_px not in (None, "", 0) and float(last) < float(last_sell_px):
                    allow_clear = True
            except (TypeError, ValueError):
                pass
            if day_weak and not rel_strong:
                allow_clear = True
            if still_strong and not allow_clear:
                ready = False
                exit_mode = "hold"
                sell_pct = 0
                regret_hold = True
                role_label = "今日已减·继续观察"
                sell_price = target
                reason_parts.insert(
                    0,
                    "反悔窗：已减半后仍贴尖/相对强，暂不清仓；破减仓价或深结构再清",
                )

    # Fresh mainline switch: protect strong NEW-theme bags from soft half/trim chops.
    switch_guard_held = False
    sg = verdict.get("switch_guard") or {}
    if ready and sg.get("active") and urgency != "stop":
        ml_name = str(mainline.get("name") or "").strip()
        matched = [str(x) for x in (theme_ctx.get("matched_names") or []) if x]
        on_new_mainline = bool(ml_name) and (
            ml_name in matched
            or (theme_ctx.get("tied") and str(theme_ctx.get("name") or "") == ml_name)
            or str(theme_role or "") == "primary"
        )
        daily_up_sg = bool(trend.get("up")) and not bool(trend.get("down"))
        strong_bag = bool(rel_strong or at_tip or daily_up_sg)
        deep_structure = bool(
            exit_mode == "clear"
            and urgency == "take"
            and pullback is not None
            and pullback >= pb_deep
            and pnl_pct is not None
            and float(pnl_pct) >= take_deep_pnl
        )
        soft_chop = (
            urgency in ("trim", "take")
            and exit_mode == "half"
        ) or (
            urgency == "trim" and exit_mode == "clear" and not deep_structure
        )
        if on_new_mainline and strong_bag and soft_chop and not deep_structure:
            ready = False
            exit_mode = "hold"
            sell_pct = 0
            switch_guard_held = True
            role_label = "换防护栏·继续持有"
            sell_price = target
            reason_parts.insert(
                0,
                "主线刚换防：新主题强票暂不因软减/非主线误砍（止损与深结构仍可卖）",
            )

    if t1_locked and ready:
        ready = False
        role_label = f"T+1锁定·{role_label}"
        sell_pct = 0
        exit_mode = "hold"
        reason_parts.insert(0, f"当日买入（{buy_day or '今'}）隔日才能卖")
    elif t1_locked:
        role_label = "T+1锁定"
        exit_mode = "hold"
        reason_parts.insert(0, f"当日买入（{buy_day or '今'}）隔日才能卖")

    dec.update(
        ready=ready,
        exit_mode=exit_mode,
        role_label=role_label,
        sell_price=sell_price,
        sell_pct=sell_pct,
    )
    return regret_hold, switch_guard_held
