"""Per-position sell decision: stops, break-even shield, takes, sector crack."""

from __future__ import annotations

from typing import Any
from market_desk.filters import normalize_code

from market_desk.verdict.common import (
    _fmt_pct,
    _is_etf_code,
    _px,
)
from market_desk.verdict.bands import _exit_band_params
from market_desk.verdict.sell_crack import detect_sector_crack
from market_desk.verdict.sell_guards import _apply_sell_guards, _tune_exit_band
from market_desk.verdict.sell_theme import _sell_theme_context
from market_desk.verdict.tags import (
    _enrich_sell_next_action,
    _position_exempt_yday_weak,
    _sell_tune_tags,
    _yday_buy_weak,
)


def _sell_item(
    row: dict[str, Any],
    verdict: dict[str, Any],
    phase: str,
    *,
    trade_date: str | None = None,
    trend: dict[str, Any] | None = None,
    sell_bias: dict[str, Any] | None = None,
    similar: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Decide whether a held name should be sold, trimmed, or held.

    Exit is layered: stop → clear; deep take / lifecycle ending → clear or half;
    soft mainline fade → half first. ``exit_mode`` drives UI defaults (half/clear).

    Daily trend (once per day): if stop band hits but trend is still up and price
    holds above MA20, soften to half; clear downtrend / MA20 break stays hard stop.

    Prior-day buy + weak session (「昨买今弱」): prefer half first and raise the
    hard-clear bar so open weakness does not panic-exit the whole bag.

    Stop width and pullback thresholds adapt via ``_exit_band_params`` (mainline /
    lifecycle / phase / daily trend).
    """
    from market_desk.db import is_t1_locked, position_buy_day
    from market_desk.lots import clear_sell_qty, half_sell_qty

    code = normalize_code(row.get("code"))
    name = str(row.get("name") or code)
    last = row.get("last")
    buy = float(row.get("buy_price") or 0)
    if not code or not buy:
        return None
    etf = _is_etf_code(code)
    digits = 3 if etf else 2
    high = row.get("high")
    low = row.get("low")
    pct = row.get("last_pct")
    pnl_pct = row.get("pnl_pct")
    if pnl_pct is None and last is not None and buy:
        pnl_pct = (float(last) / buy - 1.0) * 100.0
    # Hold-peak (cross-day) anchors take-profit pullback; day high alone is too myopic.
    peak_candidates = [buy]
    for raw in (row.get("peak_price"), high, last):
        try:
            if raw not in (None, "", 0) and float(raw) > 0:
                peak_candidates.append(float(raw))
        except (TypeError, ValueError):
            pass
    hold_peak = max(peak_candidates)
    pullback = None
    if last is not None and hold_peak > 0:
        pullback = (hold_peak - float(last)) / hold_peak * 100.0

    mainline = verdict.get("mainline") or {}
    sticky_status = str(mainline.get("status") or "")
    sticky_life = str(mainline.get("lifecycle") or "")
    sticky_fade = sticky_status == "退潮" or sticky_life == "ending"
    theme_ctx = _sell_theme_context(row, verdict)
    on_mainline = bool(theme_ctx.get("tied"))
    main_status = str(theme_ctx.get("status") or "") if on_mainline else ""
    life_stage = str(theme_ctx.get("lifecycle") or "") if on_mainline else ""
    ending = life_stage == "ending"
    auction_only = bool(verdict.get("auction_only"))
    # Soft exits: panic always; climax only with higher profit bar later.
    # Theme fade = matched sell-theme 退潮/ending (multi-theme; not sticky-only).
    phase_panic = phase == "恐慌"
    phase_climax = phase == "高潮"
    mainline_fade = (not auction_only) and bool(theme_ctx.get("fade"))
    # Relative strength / tip / day-weak context for soft & light takes.
    day_pct = row.get("last_pct")
    if day_pct is None:
        day_pct = row.get("pct")
    try:
        day_pct_f = float(day_pct) if day_pct is not None else None
    except (TypeError, ValueError):
        day_pct_f = None
    day_pb = None
    try:
        if high not in (None, 0) and last is not None and float(high) > 0:
            day_pb = (float(high) - float(last)) / float(high) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        day_pb = None
    from market_desk.config import (
        SELL_DAY_WEAK_PCT,
        SELL_PANIC_REL_STRONG_PCT,
        SELL_REL_STRONG_PCT,
        SELL_TIP_HOLD_PB,
        SELL_VS_INDEX_EDGE,
    )

    at_tip = day_pb is not None and day_pb < float(SELL_TIP_HOLD_PB)
    rel_strong = False
    if day_pct_f is not None and day_pct_f >= float(SELL_REL_STRONG_PCT):
        rel_strong = True
    else:
        try:
            idx = (metrics or {}).get("hs300_pct")
            if day_pct_f is not None and idx is not None:
                if float(day_pct_f) - float(idx) >= float(SELL_VS_INDEX_EDGE):
                    rel_strong = True
        except (TypeError, ValueError):
            pass
    panic_rel_strong = bool(
        phase_panic
        and day_pct_f is not None
        and day_pct_f >= float(SELL_PANIC_REL_STRONG_PCT)
    )
    day_weak = day_pct_f is not None and day_pct_f <= float(SELL_DAY_WEAK_PCT)
    partial_done = int(row.get("day_sold_qty") or 0) > 0
    carrier_pct = theme_ctx.get("carrier_pct")
    vs_carrier = None
    carrier_rel_strong = False
    carrier_rel_weak = False
    try:
        if day_pct_f is not None and carrier_pct is not None:
            vs_carrier = round(float(day_pct_f) - float(carrier_pct), 2)
            if vs_carrier >= float(SELL_VS_INDEX_EDGE):
                carrier_rel_strong = True
                rel_strong = True
            elif vs_carrier <= -float(SELL_VS_INDEX_EDGE):
                carrier_rel_weak = True
    except (TypeError, ValueError):
        vs_carrier = None
    soft_exit = (phase_panic and not panic_rel_strong) or (mainline_fade and on_mainline)
    t1_locked = is_t1_locked(row, trade_date)
    buy_day = position_buy_day(row)
    yday_weak = _yday_buy_weak(
        buy_day=buy_day,
        trade_date=trade_date,
        t1_locked=t1_locked,
        day_pct=day_pct_f,
    )
    if yday_weak.get("active") and _position_exempt_yday_weak(row):
        yday_weak = dict(yday_weak)
        yday_weak["active"] = False
        yday_weak["exempt"] = "independent_pop"
    # 昨买今弱 repair: price reclaimed cost or day % recovered from the weak print.
    yday_repaired = False
    if yday_weak.get("active"):
        from market_desk.config import SELL_YDAY_RECOVER_PCT

        recovered = False
        try:
            if last is not None and buy > 0 and float(last) >= buy:
                recovered = True
        except (TypeError, ValueError):
            pass
        try:
            if day_pct_f is not None and float(day_pct_f) > float(SELL_YDAY_RECOVER_PCT):
                recovered = True
        except (TypeError, ValueError):
            pass
        if recovered:
            yday_weak = dict(yday_weak)
            yday_weak["active"] = False
            yday_weak["repaired"] = True
            yday_repaired = True
    hold_qty = int(row.get("qty") or 0)
    trend = trend or {}
    ma20 = trend.get("ma20")
    theme_label = str(theme_ctx.get("name") or "")
    theme_role = str(theme_ctx.get("role") or "")

    amp_val = None
    amp_src = "daily"
    try:
        if trend.get("atr_pct") is not None and float(trend["atr_pct"]) > 0:
            amp_val = float(trend["atr_pct"])
        elif high not in (None, 0) and low not in (None, 0) and buy > 0:
            amp_val = (float(high) - float(low)) / buy * 100.0
            amp_src = "intraday"
    except (TypeError, ValueError, ZeroDivisionError):
        amp_val = None

    band = _exit_band_params(
        etf=etf,
        on_mainline=on_mainline,
        ending=ending,
        main_status=str(main_status),
        life_stage=str(life_stage),
        phase=phase,
        soft_exit=soft_exit,
        trend=trend,
        carrier_falling=bool(theme_ctx.get("carrier_falling")),
        sell_bias=sell_bias,
        rel_strong=rel_strong or panic_rel_strong,
        segment_key=str((verdict.get("segment") or {}).get("key") or ""),
        amplitude=amp_val,
        amplitude_source=amp_src,
    )
    flow_pressure, flow_fade, wave_adj = _tune_exit_band(
        band,
        verdict=verdict,
        similar=similar,
        sell_bias=sell_bias,
        theme_label=theme_label,
        mainline_fade=mainline_fade,
    )
    stop = buy * float(band["stop_buy"])
    if low not in (None, 0) and float(low) < buy:
        stop = max(float(low), buy * float(band["stop_floor"]))
    target = buy * (1.03 if etf else 1.05)
    if high not in (None, 0) and float(high) > target:
        target = float(high) * (0.995 if etf else 0.99)

    urgency = "hold"
    ready = False
    exit_mode = "hold"  # hold | half | clear
    role_label = "继续持有"
    sell_price = target
    sell_pct = 0
    reason_parts: list[str] = []

    pb_light = float(band["pb_light"])
    pb_deep = float(band["pb_deep"])
    pnl_stop = float(band["pnl_stop"])
    take_pnl = float(band["take_pnl"])
    take_deep_pnl = float(band["take_deep_pnl"])
    pocket_pnl = float(band["pocket_pnl"])
    # Ending on live mainline: still nudge take-profit earlier on top of tight mode.
    if ending and on_mainline and band["mode"] != "tight":
        pb_light *= 0.75
        pb_deep *= 0.85
        take_pnl = min(take_pnl, 4.0 if not etf else 2.5)
        take_deep_pnl = min(take_deep_pnl, 5.0 if not etf else 3.5)
        pocket_pnl = min(pocket_pnl, 6.0 if not etf else 4.0)

    # 1. Break-even Defense Shield: once peaked > +2.5% (ETF > +1.5%),
    # falling back to cost+0.3% triggers breakeven stop to prevent profits turning into big losses.
    from market_desk.config import (
        SELL_BREAKEVEN_BUFFER_PCT,
        SELL_BREAKEVEN_ETF_TRIGGER_PNL,
        SELL_BREAKEVEN_TRIGGER_PNL,
    )

    be_trigger_pnl = float(SELL_BREAKEVEN_ETF_TRIGGER_PNL if etf else SELL_BREAKEVEN_TRIGGER_PNL)
    be_arm_px = buy * (1.0 + be_trigger_pnl / 100.0)
    peaked_enough = hold_peak >= be_arm_px
    be_stop_px = buy * (1.0 + float(SELL_BREAKEVEN_BUFFER_PCT) / 100.0)
    in_stop_band = last is not None and (
        float(last) <= stop or (pnl_pct is not None and pnl_pct <= pnl_stop)
    )
    breakeven_triggered = False
    breakeven_gap = False
    # Below the stop band the stop branch (MA20 soften / hard clear) owns the exit.
    if (
        not t1_locked
        and peaked_enough
        and last is not None
        and float(last) <= be_stop_px
        and not in_stop_band
    ):
        breakeven_triggered = True
        open_px = row.get("open")
        try:
            # Gapped through the line on a prior-day peak: the shield never had a fill.
            breakeven_gap = (
                open_px not in (None, "", 0)
                and float(open_px) <= be_stop_px
                and (high in (None, 0) or float(high) < be_arm_px)
            )
        except (TypeError, ValueError):
            breakeven_gap = False

    if last is None:
        role_label = "待行情"
        reason_parts.append("尚无现价，先不判卖点")
    elif breakeven_triggered and breakeven_gap:
        urgency = "trim"
        ready = True
        exit_mode = "half"
        role_label = "跳空破保本·先减半"
        sell_price = float(last)
        sell_pct = 50
        reason_parts.append(
            f"前高浮盈已回吐，今日开盘 {float(row.get('open')):.{digits}f} 直接跳空到保本线 "
            f"{be_stop_px:.{digits}f} 下方（浮盈 {_fmt_pct(pnl_pct)}），先减半（开盘缓冲窗内看分时再定），余仓交给止损带"
        )
    elif breakeven_triggered:
        urgency = "stop"
        ready = True
        exit_mode = "clear"
        at_cost = float(last) >= buy
        role_label = "保本防守清仓" if at_cost else "回落防守清仓"
        sell_price = float(last)
        sell_pct = 100
        reason_parts.append(
            f"浮盈冲高回落触及保本线 {be_stop_px:.{digits}f}（+{float(SELL_BREAKEVEN_BUFFER_PCT):.1f}%覆盖规费），"
            + ("锁定本金杜绝盈利变大亏" if at_cost else f"现价已在成本下方（{_fmt_pct(pnl_pct)}），清仓防扩大")
        )
    elif in_stop_band:
        deep_pnl = pnl_pct is not None and pnl_pct <= pnl_stop
        # Soften on MA20 hold unless day-once trend is clearly down (do not require trend.up).
        above_ma20 = ma20 is not None and float(last) > float(ma20)
        soft_ok = above_ma20 and not deep_pnl and not bool(trend.get("down"))
        if soft_ok:
            urgency = "stop"
            ready = True
            exit_mode = "half"
            role_label = "止损带·站上MA20先减"
            sell_price = float(last)
            sell_pct = 50
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}，触及止损带（{band['mode_zh']}），"
                f"现价仍站上 MA20 {ma20}，建议先减一半"
            )
        else:
            urgency = "stop"
            ready = True
            exit_mode = "clear"
            role_label = "止损清仓"
            sell_price = float(last)
            sell_pct = 100
            if deep_pnl and trend.get("up"):
                reason_parts.append(
                    f"浮盈 {_fmt_pct(pnl_pct)}，深亏止损（{band['mode_zh']}）；"
                    f"虽日线标上升仍建议清仓"
                )
            elif trend.get("down"):
                reason_parts.append(
                    f"浮盈 {_fmt_pct(pnl_pct)}，触及止损且日线下降（{band['mode_zh']}），建议清仓"
                )
            elif trend.get("up") and ma20 is not None and float(last) <= float(ma20):
                reason_parts.append(
                    f"浮盈 {_fmt_pct(pnl_pct)}，触及止损且跌破日线 MA20 {ma20}"
                    f"（{band['mode_zh']}），建议清仓"
                )
            else:
                reason_parts.append(
                    f"浮盈 {_fmt_pct(pnl_pct)}，触及止损带（{band['mode_zh']}），建议清仓"
                )
    elif (
        pnl_pct is not None
        and pnl_pct >= take_pnl
        and pullback is not None
        and pullback >= pb_light
        and not (at_tip and rel_strong and not (ending and on_mainline))
    ):
        urgency = "take"
        ready = True
        sell_price = float(last)
        # Peak layering: light pullback → half only; deep / peak-fail → clear.
        deep = pullback >= pb_deep and pnl_pct >= take_deep_pnl
        peak_fail = False
        try:
            last_sell_px = row.get("last_sell_price")
            if (
                partial_done
                and last_sell_px not in (None, "", 0)
                and float(last) < float(last_sell_px)
                and pullback >= pb_light
            ):
                peak_fail = True
            if (
                high not in (None, 0)
                and hold_peak > 0
                and (hold_peak - float(high)) / hold_peak * 100.0
                < float(SELL_TIP_HOLD_PB)
                and pullback >= pb_deep
                and pnl_pct >= take_deep_pnl
            ):
                peak_fail = True
        except (TypeError, ValueError, ZeroDivisionError):
            peak_fail = False
        ending_clear = (
            ending
            and on_mainline
            and pullback >= pb_light
            and pnl_pct >= (3.0 if etf else 5.0)
            and not at_tip
        )
        if deep or peak_fail:
            exit_mode = "clear"
            role_label = (
                "破峰失败清仓"
                if peak_fail and not deep
                else "结构回撤清仓"
            )
            sell_pct = 100
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}，高点回撤 {pullback:.1f}%"
                + ("，破减仓价/破峰失败" if peak_fail else "")
                + f"（{band['mode_zh']}），建议清仓"
            )
        elif ending_clear:
            exit_mode = "clear"
            role_label = "退潮兑现清仓"
            sell_pct = 100
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}，高点回撤 {pullback:.1f}%，生命周期偏衰退"
                f"（{band['mode_zh']}），建议清仓"
            )
        else:
            # Light band: never clear on light pullback alone.
            exit_mode = "half"
            role_label = "冲高回落先减"
            sell_pct = 50
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}，高点回撤 {pullback:.1f}%"
                f"（轻回撤·{band['mode_zh']}），先减一半"
            )
    elif pnl_pct is not None and pnl_pct >= pocket_pnl and not (at_tip and rel_strong):
        urgency = "take"
        ready = True
        sell_price = float(last)
        # Ending pocket clear requires leaving tip (no zero-pullback dump).
        if ending and on_mainline and not at_tip:
            exit_mode = "clear"
            role_label = "衰退落袋清仓"
            sell_pct = 100
            reason_parts.append(f"浮盈 {_fmt_pct(pnl_pct)}，主线生命周期偏衰退，建议清仓")
        else:
            exit_mode = "half"
            role_label = "落袋先减"
            sell_pct = 50
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}（{band['mode_zh']}），建议先减一半，余仓盯止损"
            )
    elif (soft_exit or phase_climax) and pnl_pct is not None and not (at_tip and rel_strong):
        from market_desk.config import (
            SELL_CARRIER_WEAK_SOFT_DELTA,
            SELL_DAY_WEAK_SOFT_DELTA,
            SELL_SOFT_CLIMAX_MIN_PNL_ETF,
            SELL_SOFT_CLIMAX_MIN_PNL_STOCK,
            SELL_SOFT_DEEP_PNL_ETF,
            SELL_SOFT_DEEP_PNL_STOCK,
            SELL_SOFT_MIN_PNL_ENDING_ETF,
            SELL_SOFT_MIN_PNL_ENDING_STOCK,
            SELL_SOFT_MIN_PNL_ETF,
            SELL_SOFT_MIN_PNL_STOCK,
            SELL_SOFT_UPTREND_EXTRA,
        )

        if ending and on_mainline:
            soft_min = SELL_SOFT_MIN_PNL_ENDING_ETF if etf else SELL_SOFT_MIN_PNL_ENDING_STOCK
        else:
            soft_min = SELL_SOFT_MIN_PNL_ETF if etf else SELL_SOFT_MIN_PNL_STOCK
        # Climax alone (no panic / no 退潮) needs a higher profit floor.
        if phase_climax and not soft_exit:
            soft_min = max(
                soft_min,
                SELL_SOFT_CLIMAX_MIN_PNL_ETF if etf else SELL_SOFT_CLIMAX_MIN_PNL_STOCK,
            )
        daily_up = bool(trend.get("up")) and not bool(trend.get("down"))
        if daily_up:
            soft_min = float(soft_min) + float(SELL_SOFT_UPTREND_EXTRA)
        if day_weak:
            soft_min = float(soft_min) + float(SELL_DAY_WEAK_SOFT_DELTA)
        # Flow alone (no theme fade): nudge soft floor only, no pb scale.
        if flow_pressure and not flow_fade:
            soft_min = float(soft_min) + float(SELL_DAY_WEAK_SOFT_DELTA) * 0.5
        # Lagging carrier (not at tip): easier soft trim.
        if carrier_rel_weak and not at_tip:
            soft_min = float(soft_min) + float(SELL_CARRIER_WEAK_SOFT_DELTA)
        # Elliott soft nudge.
        try:
            soft_min = float(soft_min) + float(wave_adj.get("soft_extra") or 0.0)
        except (TypeError, ValueError):
            pass
        # Relative strength: climax soft needs more profit; skip mild climax alone.
        if phase_climax and rel_strong and not soft_exit:
            soft_min = float(soft_min) + 1.0
        # Carrier-strong bags: never soft-clear.
        block_soft_clear = bool(carrier_rel_strong or (at_tip and rel_strong))
        if pnl_pct > soft_min:
            urgency = "trim"
            ready = True
            sell_price = float(last)
            deep_need = SELL_SOFT_DEEP_PNL_ETF if etf else SELL_SOFT_DEEP_PNL_STOCK
            if daily_up:
                deep_need = float(deep_need) + float(SELL_SOFT_UPTREND_EXTRA)
            deep_fade = ending and on_mainline and (
                (pullback is not None and pullback >= pb_light)
                or pnl_pct >= deep_need
            )
            # Uptrend / carrier-strong: soft fade only halves; never clear on soft branch.
            if deep_fade and not daily_up and not block_soft_clear:
                exit_mode = "clear"
                role_label = "衰退清仓"
                sell_pct = 100
                reason_parts.append(
                    f"生命周期/主线偏弱，浮盈 {_fmt_pct(pnl_pct)}"
                    + (f"，回撤 {pullback:.1f}%" if pullback is not None else "")
                    + "，建议清仓"
                )
            else:
                exit_mode = "half"
                role_label = "衰退先减" if ending and on_mainline else "建议先减"
                sell_pct = 50
                why = (
                    "生命周期偏衰退"
                    if ending and on_mainline
                    else (
                        "相位恐慌"
                        if phase_panic and soft_exit
                        else (
                            "相位高潮"
                            if phase_climax
                            else (
                                f"主题「{theme_label}」退潮"
                                if theme_label
                                else "主线退潮"
                            )
                        )
                    )
                )
                reason_parts.append(
                    f"{why}，浮盈 {_fmt_pct(pnl_pct)}（门槛 {soft_min:.1f}%"
                    + ("·日线上升抬高" if daily_up else "")
                    + ("·当日偏弱放宽" if day_weak else "")
                    + ("·弱于载体" if carrier_rel_weak and not at_tip else "")
                    + ("·资金流出" if flow_pressure and not flow_fade else "")
                    + "），先减一半"
                )
        # else: below soft floor — fall through to carrier / ending / hold
    if not ready and (
        not etf
        and on_mainline
        and theme_ctx.get("carrier_falling")
        and pnl_pct is not None
        and pnl_pct > -1.0
        and last is not None
        and not rel_strong
        and not (bool(trend.get("up")) and not bool(trend.get("down")))
    ):
        from market_desk.config import SELL_CARRIER_FALL_PCT

        urgency = "trim"
        ready = True
        exit_mode = "half"
        role_label = "载体走弱先减"
        sell_price = float(last)
        sell_pct = 50
        reason_parts.append(
            f"主线 ETF/载体较上一轮回落≥{float(SELL_CARRIER_FALL_PCT):.2f}%，个股先减一半"
        )
    if not ready and ending and on_mainline and pnl_pct is not None and last is not None:
        from market_desk.config import SELL_ENDING_DEFENSE_PNL

        daily_up = bool(trend.get("up")) and not bool(trend.get("down"))
        if (not daily_up) and pnl_pct <= float(SELL_ENDING_DEFENSE_PNL):
            urgency = "trim"
            ready = True
            exit_mode = "half"
            role_label = "衰退防守先减"
            sell_price = float(last)
            sell_pct = 50
            reason_parts.append(f"生命周期偏衰退且浮盈 {_fmt_pct(pnl_pct)}，先减仓防守")
    # 2. Sector crack: leader blow-off or members diving → half-trim ahead of a cascade.
    from market_desk.config import SELL_SECTOR_CRACK_ENABLED

    sector_crack_reason = ""
    if (
        SELL_SECTOR_CRACK_ENABLED
        and not ready
        and not t1_locked
        and theme_label
        and last is not None
        and not rel_strong
    ):
        matched_theme = next(
            (t for t in verdict.get("sell_themes") or [] if str(t.get("name") or "") == theme_label),
            None,
        )
        sector_crack_reason = detect_sector_crack(matched_theme, code)

    if sector_crack_reason and not ready:
        urgency = "trim"
        ready = True
        exit_mode = "half"
        role_label = "板块塌陷先减"
        sell_price = float(last)
        sell_pct = 50
        reason_parts.append(
            f"所属板块「{theme_label}」出现退潮崩塌先兆（{sector_crack_reason}），先手减半防踩踏"
        )

    # Overnight gap-fade: prior-day bag opens strong then fails from open.
    # On sell-theme: skip when still strong (carrier / tip / daily up); weak members
    # may still half-trim. Hard stops are unaffected (this branch only runs if !ready).
    gap_fade_skipped = False
    gap_fade_note = ""
    if (
        not ready
        and not t1_locked
        and last is not None
        and pnl_pct is not None
        and pnl_pct > -1.5
    ):
        try:
            from market_desk.config import GAP_FADE_DROP_PCT, GAP_FADE_OPEN_PCT

            prev = row.get("prev")
            open_px = row.get("open")
            if prev not in (None, 0) and open_px not in (None, 0):
                open_gap = (float(open_px) / float(prev) - 1.0) * 100.0
                from_open = (float(last) / float(open_px) - 1.0) * 100.0
                if open_gap >= float(GAP_FADE_OPEN_PCT) and from_open <= -float(
                    GAP_FADE_DROP_PCT
                ):
                    daily_up = bool(trend.get("up")) and not bool(trend.get("down"))
                    protect = bool(
                        on_mainline
                        and (carrier_rel_strong or at_tip or daily_up)
                    )
                    if protect:
                        gap_fade_skipped = True
                        gap_fade_note = (
                            f"高开 {open_gap:.1f}% 后回落 {abs(from_open):.1f}%，"
                            "但主线内偏强（强于载体/贴尖/日线上升），先不因形态减"
                        )
                    else:
                        urgency = "trim"
                        ready = True
                        exit_mode = "half"
                        role_label = (
                            "主线内·高开低走先减"
                            if on_mainline and carrier_rel_weak
                            else "隔夜高开低走先减"
                        )
                        sell_price = float(last)
                        sell_pct = 50
                        reason_parts.append(
                            f"开盘相对昨收高开 {open_gap:.1f}% 后回落 "
                            f"{abs(from_open):.1f}%，先减一半"
                        )
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # 「昨买今弱」: if nothing fired yet, start with a half trim (not a full dump).
    if (
        not ready
        and yday_weak.get("active")
        and last is not None
        and not partial_done
        and not rel_strong
        and not (at_tip and day_pct_f is not None and day_pct_f >= 0)
    ):
        urgency = "trim"
        ready = True
        exit_mode = "half"
        role_label = "昨买今弱·先减半"
        sell_price = float(last)
        sell_pct = 50
        reason_parts.append(
            f"昨买窗内（持仓约 {yday_weak.get('age_days')} 日）当日 "
            f"{_fmt_pct(day_pct_f)}≤{yday_weak.get('weak_pct')}%，默认先减一半"
        )

    if not ready and last is not None:
        role_label = "继续持有"
        sell_price = target
        sell_pct = 0
        exit_mode = "hold"
        if not reason_parts:
            reason_parts.append(f"浮盈 {_fmt_pct(pnl_pct)}，未到卖点，盯目标价")
            if pullback is not None:
                reason_parts.append(f"高点回撤 {pullback:.1f}%")
            if soft_exit or phase_climax:
                reason_parts.append("主线/相位偏弱但未过软减门槛")
            elif phase_panic and panic_rel_strong:
                reason_parts.append(
                    f"相位恐慌但当日 {_fmt_pct(day_pct_f)} 偏强，先不因恐慌软减"
                )
            elif at_tip and rel_strong:
                reason_parts.append(
                    f"当日仍贴近高点（回撤 {day_pb:.1f}%）且偏强，先不轻减"
                    if day_pb is not None
                    else "当日仍贴近高点且偏强，先不轻减"
                )
            if ending and on_mainline:
                reason_parts.append(
                    f"主题「{theme_label}」生命周期偏衰退，反抽优先减"
                    if theme_label
                    else "主线生命周期偏衰退，反抽优先减"
                )
            elif sticky_fade and not on_mainline:
                reason_parts.append("非卖侧主题持仓，主线转弱不自动减")
            if band["mode"] != "neutral":
                reason_parts.append(f"波段口径 {band['mode_zh']}")
            if on_mainline and theme_role and theme_role != "primary" and theme_label:
                reason_parts.append(
                    f"卖侧归属「{theme_label}」（"
                    f"{ {'side':'支线','hot':'热点同伴','link':'联动','switch_from':'换防旧主题'}.get(theme_role, theme_role) }）"
                )
            if carrier_rel_strong and vs_carrier is not None:
                reason_parts.append(f"强于主线载体 {vs_carrier:+.1f}pt，先不轻减")
            elif carrier_rel_weak and vs_carrier is not None:
                reason_parts.append(f"弱于主线载体 {vs_carrier:+.1f}pt")
            if gap_fade_skipped and gap_fade_note:
                reason_parts.append(gap_fade_note)

    dec = {
        "urgency": urgency,
        "ready": ready,
        "exit_mode": exit_mode,
        "role_label": role_label,
        "sell_price": sell_price,
        "sell_pct": sell_pct,
        "reason_parts": reason_parts,
    }
    regret_hold, switch_guard_held = _apply_sell_guards(
        dec,
        row=row,
        verdict=verdict,
        theme_ctx=theme_ctx,
        trend=trend,
        yday_weak=yday_weak,
        t1_locked=t1_locked,
        buy_day=buy_day,
        partial_done=partial_done,
        at_tip=at_tip,
        rel_strong=rel_strong,
        carrier_rel_strong=carrier_rel_strong,
        day_weak=day_weak,
        last=last,
        pnl_pct=pnl_pct,
        pullback=pullback,
        pnl_stop=pnl_stop,
        pb_deep=pb_deep,
        take_deep_pnl=take_deep_pnl,
        target=target,
    )
    ready = dec["ready"]
    exit_mode = dec["exit_mode"]
    role_label = dec["role_label"]
    sell_price = dec["sell_price"]
    sell_pct = dec["sell_pct"]

    half_q = half_sell_qty(hold_qty)
    clear_q = clear_sell_qty(hold_qty)
    sell_qty = clear_q if exit_mode == "clear" else (half_q if exit_mode == "half" else 0)
    stop_pct = round((1.0 - float(band["stop_buy"])) * 100.0, 2)
    daily_up = bool(trend.get("up")) and not bool(trend.get("down"))
    tune_tags = _sell_tune_tags(
        band=band,
        daily_up=daily_up,
        at_tip=bool(at_tip),
        rel_strong=bool(rel_strong),
        carrier_rel_strong=bool(carrier_rel_strong),
        carrier_rel_weak=bool(carrier_rel_weak),
        flow_pressure=bool(flow_pressure),
        wave_tag=str(wave_adj.get("tag") or "") or None,
        yday_repaired=bool(yday_repaired),
        regret=bool(regret_hold),
    )
    mfe_info = (band.get("segment_sell") or {}).get("mfe") or {}
    sb = band.get("sell_bias") or {}
    sell_tune = {
        "review_note": sb.get("note"),
        "mfe_note": mfe_info.get("note"),
        "widen": bool(sb.get("widen") or mfe_info.get("widen")),
        "tighten": bool(sb.get("tighten") or mfe_info.get("tighten")),
        "mult": sb.get("mult"),
        "mfe_mult": mfe_info.get("mult"),
    }
    # Soft half / light take need minute fade; stop & deep clear skip the gate.
    minute_gate = bool(
        ready
        and exit_mode == "half"
        and urgency in ("take", "trim")
    )

    out = {
        "id": row.get("id"),
        "kind": "etf" if etf else "stock",
        "kind_label": "ETF" if etf else "个股",
        "role_label": role_label,
        "urgency": urgency,
        "exit_mode": exit_mode,
        "ready": ready,
        "t1_locked": t1_locked,
        "last_buy_date": buy_day or None,
        "daily_trend": None if not trend else trend.get("label"),
        "daily_trend_zh": None if not trend else trend.get("label"),
        "trend": None if not trend else trend.get("label"),
        "trend_ok": bool(trend.get("up")) and not bool(trend.get("down")),
        "trend_down": bool(trend.get("down")),
        "trend_pending": (trend.get("quality") in ("fetch_fail", "thin")) if trend else True,
        "ma5": None if not trend else trend.get("ma5"),
        "ma10": None if not trend else trend.get("ma10"),
        "ma20": None if not trend else trend.get("ma20"),
        "band_mode": band["mode"],
        "band_mode_zh": band["mode_zh"],
        "atr_band_mode": band.get("atr_band_mode", "normal"),
        "atr_amp": band.get("atr_amp"),
        "atr_source": band.get("atr_source"),
        "stop_pct": stop_pct,
        "pb_light": round(pb_light, 2),
        "pb_deep": round(pb_deep, 2),
        "code": code,
        "name": name,
        "qty": hold_qty,
        "last": _px(last, digits),
        "pct": None if pct is None else round(float(pct), 2),
        "pnl_pct": None if pnl_pct is None else round(float(pnl_pct), 2),
        "buy_price": _px(buy, digits),
        "sell_price": _px(sell_price, digits),
        "sell_pct": sell_pct,
        "sell_qty": sell_qty,
        "sell_qty_half": half_q,
        "sell_qty_clear": clear_q,
        "stop_price": _px(stop, digits),
        "target_price": _px(target, digits),
        "reason": "，".join(reason_parts),
        "on_sell_theme": on_mainline,
        "sell_theme": theme_label or None,
        "sell_theme_role": theme_role or None,
        "rel_strong": rel_strong,
        "panic_rel_strong": panic_rel_strong,
        "carrier_falling": bool(theme_ctx.get("carrier_falling")),
        "carrier_rel_strong": carrier_rel_strong,
        "carrier_rel_weak": carrier_rel_weak,
        "vs_carrier": vs_carrier,
        "carrier_compare_absent": bool(
            on_mainline and carrier_pct is None and not theme_ctx.get("carrier_falling")
        ),
        "partial_done": partial_done,
        "tune_tags": tune_tags,
        "sell_tune": sell_tune,
        "minute_gate": minute_gate,
        "regret_hold": regret_hold,
        "switch_guard_held": switch_guard_held,
        "yday_repaired": yday_repaired,
        "hold_peak": round(hold_peak, 4) if hold_peak else None,
        "open": _px(row.get("open"), digits) if row.get("open") not in (None, "") else None,
        "prev": _px(row.get("prev"), digits) if row.get("prev") not in (None, "") else None,
    }
    return _enrich_sell_next_action(
        out,
        hold_peak=float(hold_peak or 0),
        last_sell_price=row.get("last_sell_price"),
        digits=digits,
    )
