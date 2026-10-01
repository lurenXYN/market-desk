"""Exit band selection with regime, review-bias, session, and ATR scaling."""

from __future__ import annotations

from typing import Any


def _exit_band_params(
    *,
    etf: bool,
    on_mainline: bool,
    ending: bool,
    main_status: str,
    life_stage: str,
    phase: str,
    soft_exit: bool,
    trend: dict[str, Any] | None,
    carrier_falling: bool,
    sell_bias: dict[str, Any] | None = None,
    rel_strong: bool = False,
    segment_key: str | None = None,
    amplitude: float | None = None,
    amplitude_source: str = "daily",
) -> dict[str, Any]:
    """Pick stop / pullback / take-profit bands from multi-module context.

    Regimes:
    - give: tied to live mainline that is still confirming/rising, daily up,
      and not in soft-exit — wider stop, tolerate deeper pullback.
    - tight: fade/ending/panic/climax/daily down/carrier falling — tighter.
    - neutral: default fixed bands.

    Optional ``sell_bias`` from review sell outcomes scales pb/take/pocket.
    ``rel_strong`` (day green / beats index) blocks panic-alone tight bands.
    Session ``segment_key`` soft-widens open / soft-tightens afternoon.
    ``amplitude`` (volatility %) adjusts exit bandwidth for high/low beta assets;
    ``amplitude_source`` is ``daily`` (14-day ATR%, may widen or tighten) or
    ``intraday`` (today's range, may only widen). ETFs use their own thresholds.
    """
    from market_desk.config import (
        SELL_ATR_ETF_HIGH_BETA_AMP,
        SELL_ATR_ETF_LOW_BETA_AMP,
        SELL_ATR_EXIT_ENABLED,
        SELL_ATR_HIGH_BETA_AMP,
        SELL_ATR_HIGH_BETA_MULT,
        SELL_ATR_LOW_BETA_AMP,
        SELL_ATR_LOW_BETA_MULT,
        SELL_BAND_ETF,
        SELL_BAND_STOCK,
        SELL_UPTREND_BAND_MULT,
    )

    trend = trend or {}
    daily_up = bool(trend.get("up")) and not bool(trend.get("down"))
    daily_down = bool(trend.get("down"))
    # Lifecycle keys: starting | ongoing | ending (see lifecycle.py).
    rising_life = life_stage in ("starting", "ongoing")
    confirming = main_status == "确认中"
    strong_hold = bool(
        on_mainline
        and confirming
        and rising_life
        and daily_up
        and not soft_exit
        and not ending
    )
    # Daily uptrend: do not let soft fade / carrier tick alone force tight bands.
    fade_pressure = bool(
        soft_exit
        or (on_mainline and carrier_falling)
        or (on_mainline and main_status == "退潮")
    )
    panic_tight = phase == "恐慌" and not rel_strong
    weak_context = bool(
        daily_down
        or ending
        or panic_tight
        or (fade_pressure and not daily_up)
    )
    if strong_hold:
        mode = "give"
        mode_zh = "主升放宽"
    elif weak_context:
        mode = "tight"
        mode_zh = "退潮收紧"
    else:
        mode = "neutral"
        mode_zh = "标准"
    if rel_strong and mode == "neutral":
        mode_zh = f"{mode_zh}·相对偏强"
    table = SELL_BAND_ETF if etf else SELL_BAND_STOCK
    band = dict(table[mode])
    if daily_up:
        up_mult = float(SELL_UPTREND_BAND_MULT)
        for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
            band[key] = float(band[key]) * up_mult
        # More negative stop = wider room (e.g. -3.0 → -3.54).
        band["pnl_stop"] = float(band["pnl_stop"]) * up_mult
        mode_zh = f"{mode_zh}·日线上升"
    bias = sell_bias or {}
    mult = float(bias.get("mult") or 1.0)
    if bias.get("widen") and mult > 1.0:
        for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
            band[key] = float(band[key]) * mult
        # Slightly wider stop floor when historically selling too early.
        band["pnl_stop"] = float(band["pnl_stop"]) * (1.0 + (mult - 1.0) * 0.5)
        mode_zh = f"{mode_zh}·卖早放宽"
    elif bias.get("tighten") and 0 < mult < 1.0:
        for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
            band[key] = float(band[key]) * mult
        mode_zh = f"{mode_zh}·卖准收紧"
    seg_info: dict[str, Any] = {}
    try:
        from market_desk.adapt import build_sell_mfe_bias, segment_sell_mult

        seg_info = segment_sell_mult(segment_key)
        seg_m = float(seg_info.get("mult") or 1.0)
        # Stack with review bias but clamp product so bands stay sane.
        if abs(seg_m - 1.0) > 0.02:
            stacked = max(0.75, min(1.25, mult * seg_m))
            residual = stacked / max(mult, 1e-6) if mult > 0 else seg_m
            for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
                band[key] = float(band[key]) * residual
            if seg_info.get("widen"):
                band["pnl_stop"] = float(band["pnl_stop"]) * (1.0 + (residual - 1.0) * 0.4)
                mode_zh = f"{mode_zh}·{seg_info.get('note')}"
            elif seg_info.get("tighten"):
                mode_zh = f"{mode_zh}·{seg_info.get('note')}"
        mfe_info = build_sell_mfe_bias()
        mfe_m = float(mfe_info.get("mult") or 1.0)
        if mfe_info.get("ok") and abs(mfe_m - 1.0) > 0.02:
            for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
                band[key] = float(band[key]) * mfe_m
            mode_zh = f"{mode_zh}·{mfe_info.get('note')}"
            seg_info = dict(seg_info)
            seg_info["mfe"] = mfe_info
    except Exception:
        seg_info = {}
    atr_band_mode = "normal"
    daily_src = amplitude_source == "daily"
    hi_amp = float(SELL_ATR_ETF_HIGH_BETA_AMP if etf else SELL_ATR_HIGH_BETA_AMP)
    lo_amp = float(SELL_ATR_ETF_LOW_BETA_AMP if etf else SELL_ATR_LOW_BETA_AMP)
    if SELL_ATR_EXIT_ENABLED and amplitude is not None and amplitude > 0:
        if amplitude >= hi_amp:
            atr_band_mode = "high_beta"
            atr_mult = float(SELL_ATR_HIGH_BETA_MULT)
            for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
                band[key] = float(band[key]) * atr_mult
            band["pnl_stop"] = float(band["pnl_stop"]) * 1.15
            mode_zh = f"{mode_zh}·{'高波动ATR' if daily_src else '日内高波动'}"
        # Intraday range only grows through the session, so it may widen but never tighten.
        elif daily_src and amplitude <= lo_amp:
            atr_band_mode = "low_beta"
            atr_mult = float(SELL_ATR_LOW_BETA_MULT)
            for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
                band[key] = float(band[key]) * atr_mult
            band["pnl_stop"] = float(band["pnl_stop"]) * 0.85
            mode_zh = f"{mode_zh}·低波动ATR"
    band["atr_band_mode"] = atr_band_mode
    band["atr_amp"] = None if amplitude is None else round(float(amplitude), 2)
    band["atr_source"] = amplitude_source if amplitude is not None else None
    band["mode"] = mode
    band["mode_zh"] = mode_zh
    band["daily_up"] = daily_up
    band["rel_strong"] = bool(rel_strong)
    band["sell_bias"] = {
        "widen": bool(bias.get("widen")),
        "tighten": bool(bias.get("tighten")),
        "mult": mult,
        "hit_rate": bias.get("hit_rate"),
        "n": bias.get("n"),
        "note": bias.get("note"),
    }
    band["segment_sell"] = seg_info
    return band
