"""Per-position sell decision: stops, break-even shield, takes, sector crack."""

from __future__ import annotations

from typing import Any
from market_desk.filters import normalize_code
from market_desk.lifecycle import classify_lifecycle
from market_desk.mainline import mainline_score, same_theme

from market_desk.verdict.common import (
    _fmt_pct,
    _is_etf_code,
    _pool_codes_from_board,
    _px,
    _theme_entry,
)
from market_desk.verdict.bands import _exit_band_params
from market_desk.verdict.branches import _recommend_codes
from market_desk.verdict.tags import (
    _enrich_sell_next_action,
    _position_exempt_yday_weak,
    _sell_tune_tags,
    _sell_wave_adj,
    _yday_buy_weak,
)


def build_sell_themes(
    *,
    hot: list[dict[str, Any]] | None,
    main: dict[str, Any] | None,
    vehicle: dict[str, Any] | None,
    side_info: dict[str, Any] | None,
    recommend: dict[str, Any] | None = None,
    side_recommend: dict[str, Any] | None = None,
    link_info: dict[str, Any] | None = None,
    link_recommend: dict[str, Any] | None = None,
    life_stage: str | None = None,
) -> list[dict[str, Any]]:
    """Build multi-theme set for sells; buys still follow sticky mainline only.

    Includes sticky primary, observation side branch, soft link peer, and other
    hot boards within ``SELL_THEME_GAP`` of the sticky score (退潮 peers kept so
    residual positions can still soft-exit on their own theme fade).
    """
    from market_desk.config import SELL_THEME_GAP, SELL_THEME_MAX

    main = main or {}
    vehicle = vehicle or {}
    primary = str(main.get("name") or "").strip()
    themes: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(entry: dict[str, Any]) -> None:
        name = str(entry.get("name") or "").strip()
        if not name or name in seen:
            return
        if len(themes) >= int(SELL_THEME_MAX):
            return
        seen.add(name)
        themes.append(entry)

    if primary:
        _add(
            _theme_entry(
                name=primary,
                role="primary",
                status=str(main.get("status") or ""),
                lifecycle=life_stage or classify_lifecycle(main),
                pool_codes=_pool_codes_from_board(main),
                carrier_code=vehicle.get("code"),
                rec_codes=_recommend_codes(recommend),
                score=mainline_score(main) if main else None,
                main_yi=main.get("main_yi"),
            )
        )

    side = side_info or {}
    side_name = str(side.get("name") or "").strip()
    if side_name:
        _add(
            _theme_entry(
                name=side_name,
                role="side",
                status=str(side.get("status") or ""),
                lifecycle=side.get("lifecycle") or "",
                pool_codes=list(side.get("pool_codes") or []),
                carrier_code=side.get("carrier_code"),
                rec_codes=_recommend_codes(side_recommend),
                score=side.get("score"),
                main_yi=side.get("main_yi"),
            )
        )

    link = link_info or {}
    link_name = str(link.get("name") or "").strip()
    if link_name:
        _add(
            _theme_entry(
                name=link_name,
                role="link",
                status=str(link.get("status") or ""),
                lifecycle=link.get("lifecycle") or "",
                pool_codes=list(link.get("pool_codes") or []),
                carrier_code=link.get("carrier_code"),
                rec_codes=_recommend_codes(link_recommend),
                score=link.get("score"),
                main_yi=link.get("main_yi"),
            )
        )

    gap = float(SELL_THEME_GAP)
    boards = list(hot or [])
    industries = [b for b in boards if b.get("kind") == "industry"]
    pool = industries or boards
    main_score = mainline_score(main) if primary else 0.0
    # Pass 1: competitive peers within gap. Pass 2: fading leftovers (any gap).
    ranked = sorted(pool, key=mainline_score, reverse=True)

    def _maybe_add(board: dict[str, Any], *, allow_outside_fade: bool) -> None:
        name = str(board.get("name") or "").strip()
        if not name or name in seen:
            return
        sc = mainline_score(board)
        status = str(board.get("status") or "")
        life = classify_lifecycle(board)
        outside = bool(primary) and (main_score - sc) > gap
        fading = status == "退潮" or life == "ending"
        if outside and not (allow_outside_fade and fading):
            return
        _add(
            _theme_entry(
                name=name,
                role="hot",
                status=status,
                lifecycle=life,
                pool_codes=_pool_codes_from_board(board),
                carrier_code=None,
                score=round(sc, 1),
                main_yi=board.get("main_yi"),
            )
        )

    for board in ranked:
        _maybe_add(board, allow_outside_fade=False)
    for board in ranked:
        _maybe_add(board, allow_outside_fade=True)
    return themes


def _position_tied_to_mainline(row: dict[str, Any], verdict: dict[str, Any]) -> bool:
    """Return True when a held name is the live carrier or in the mainline pool.

    Soft mainline-fade sells should not fire on unrelated residual positions.
    Prefer ``_sell_theme_context`` for sells (multi-theme); this remains the
    sticky-only fallback when ``sell_themes`` is absent.
    """
    code = normalize_code(row.get("code"))
    if not code:
        return False
    carrier = normalize_code((verdict.get("carrier") or {}).get("code"))
    if carrier and code == carrier:
        return True
    main = verdict.get("mainline") or {}
    pool = {normalize_code(c) for c in (main.get("pool_codes") or []) if c}
    if code in pool:
        return True
    for item in ((verdict.get("recommend") or {}).get("items") or []):
        if normalize_code(item.get("code")) == code:
            return True
    board = str(row.get("board") or row.get("sector") or row.get("industry") or "")
    main_name = str(main.get("name") or "")
    if board and main_name and (board in main_name or main_name in board):
        return True
    if board and main_name and same_theme(board, main_name):
        return True
    return False


def _match_sell_theme(row: dict[str, Any], theme: dict[str, Any]) -> bool:
    """Return True when a position belongs to one sell-theme descriptor."""
    code = normalize_code(row.get("code"))
    if not code:
        return False
    carrier = normalize_code(theme.get("carrier_code"))
    if carrier and code == carrier:
        return True
    pool = {normalize_code(c) for c in (theme.get("pool_codes") or []) if c}
    if code in pool:
        return True
    rec = {normalize_code(c) for c in (theme.get("rec_codes") or []) if c}
    if code in rec:
        return True
    name = str(theme.get("name") or "")
    labels: list[str] = []
    for raw in (
        row.get("board"),
        row.get("sector"),
        row.get("industry"),
        row.get("entry_board"),
    ):
        text = str(raw or "").strip()
        if text:
            labels.append(text)
    for raw in row.get("board_names") or []:
        text = str(raw or "").strip()
        if text:
            labels.append(text)
    for held in labels:
        if name and (held in name or name in held or same_theme(held, name)):
            return True
    return False


def _sell_theme_context(row: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """Resolve which sell-theme(s) a position belongs to and the active context.

    Buys ignore this. Sells use multi-theme membership so a side/hot peer fade
    can trim the right bags without requiring them to be today's sticky name.
    When both strong and fading themes match, fade wins (safer exit bias).
    """
    themes = list(verdict.get("sell_themes") or [])
    if not themes:
        tied = _position_tied_to_mainline(row, verdict)
        main = verdict.get("mainline") or {}
        status = str(main.get("status") or "")
        life = str(main.get("lifecycle") or "")
        fade = status == "退潮" or life == "ending"
        return {
            "tied": tied,
            "name": main.get("name") if tied else None,
            "role": "primary" if tied else None,
            "status": status if tied else "",
            "lifecycle": life if tied else "",
            "fade": bool(tied and fade),
            "carrier_falling": bool(tied and (verdict.get("carrier") or {}).get("falling")),
            "carrier_pct": (verdict.get("carrier") or {}).get("pct") if tied else None,
            "matched_names": [main.get("name")] if tied and main.get("name") else [],
        }

    matches = [t for t in themes if _match_sell_theme(row, t)]
    if not matches:
        return {
            "tied": False,
            "name": None,
            "role": None,
            "status": "",
            "lifecycle": "",
            "fade": False,
            "carrier_falling": False,
            "carrier_pct": None,
            "matched_names": [],
        }

    def _is_fade(t: dict[str, Any]) -> bool:
        return str(t.get("status") or "") == "退潮" or str(t.get("lifecycle") or "") == "ending"

    def _is_strong(t: dict[str, Any]) -> bool:
        return str(t.get("status") or "") == "确认中" and str(t.get("lifecycle") or "") != "ending"

    fade_hits = [t for t in matches if _is_fade(t)]
    strong_hits = [t for t in matches if _is_strong(t)]
    # Prefer primary among equals so sticky context stays visible in reasons.
    def _rank(t: dict[str, Any]) -> tuple[int, float]:
        role_rank = {
            "primary": 0,
            "side": 1,
            "switch_from": 2,
            "link": 3,
            "hot": 4,
        }.get(str(t.get("role") or ""), 9)
        try:
            sc = -float(t.get("score") or 0)
        except (TypeError, ValueError):
            sc = 0.0
        return (role_rank, sc)

    fade_hits.sort(key=_rank)
    strong_hits.sort(key=_rank)
    matches_sorted = sorted(matches, key=_rank)
    ctx = fade_hits[0] if fade_hits else (strong_hits[0] if strong_hits else matches_sorted[0])
    tied_primary = any(str(t.get("role") or "") == "primary" for t in matches)
    return {
        "tied": True,
        "name": ctx.get("name"),
        "role": ctx.get("role"),
        "status": str(ctx.get("status") or ""),
        "lifecycle": str(ctx.get("lifecycle") or ""),
        "fade": bool(fade_hits),
        "carrier_falling": bool(
            tied_primary and (verdict.get("carrier") or {}).get("falling")
        ),
        "carrier_pct": (verdict.get("carrier") or {}).get("pct") if tied_primary else None,
        "matched_names": [str(t.get("name")) for t in matches_sorted if t.get("name")],
    }


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
    try:
        if high not in (None, 0) and low not in (None, 0) and buy > 0:
            amp_val = (float(high) - float(low)) / buy * 100.0
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
    )
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
    peaked_enough = hold_peak >= buy * (1.0 + be_trigger_pnl / 100.0)
    be_stop_px = buy * (1.0 + float(SELL_BREAKEVEN_BUFFER_PCT) / 100.0)
    breakeven_triggered = False
    if not t1_locked and peaked_enough and last is not None and float(last) <= be_stop_px:
        # Armed break-even stop
        breakeven_triggered = True

    if last is None:
        role_label = "待行情"
        reason_parts.append("尚无现价，先不判卖点")
    elif breakeven_triggered:
        urgency = "stop"
        ready = True
        exit_mode = "clear"
        role_label = "保本防守清仓"
        sell_price = float(last)
        sell_pct = 100
        reason_parts.append(
            f"浮盈冲高回落触及保本线 {be_stop_px:.{digits}f}（+{float(SELL_BREAKEVEN_BUFFER_PCT):.1f}%覆盖规费），"
            f"锁定本金杜绝盈利变大亏"
        )
    elif float(last) <= stop or (pnl_pct is not None and pnl_pct <= pnl_stop):
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
    # 2. Sector De-sync & Crack Alert: Dragon blow-off or multiple members diving
    # prompts a half-trim to front-run sector collapse cascades.
    from market_desk.config import (
        SELL_SECTOR_CRACK_DIVERGENT_DOWN_N,
        SELL_SECTOR_CRACK_DIVERGENT_DOWN_PCT,
        SELL_SECTOR_CRACK_DRAGON_BLOW_DROP,
        SELL_SECTOR_CRACK_ENABLED,
    )

    sector_crack = False
    sector_crack_reason = ""
    if (
        SELL_SECTOR_CRACK_ENABLED
        and not ready
        and not t1_locked
        and theme_label
        and last is not None
        and not rel_strong
    ):
        matched_theme = None
        for t in verdict.get("sell_themes") or []:
            if str(t.get("name") or "") == theme_label:
                matched_theme = t
                break
        if matched_theme:
            # Check dragon blow-off (e.g. leader failed / dropped hard from high)
            ld_pct = matched_theme.get("leader_pct")
            ld_pb = matched_theme.get("leader_pullback")
            if ld_pb is not None and float(ld_pb) >= abs(float(SELL_SECTOR_CRACK_DRAGON_BLOW_DROP)):
                sector_crack = True
                sector_crack_reason = f"板块核心龙头炸板回撤 {float(ld_pb):.1f}%"
            elif ld_pct is not None and float(ld_pct) <= float(SELL_SECTOR_CRACK_DRAGON_BLOW_DROP):
                # Leader in the negative
                if matched_theme.get("leader_boards") and int(matched_theme.get("leader_boards") or 0) >= 2:
                    sector_crack = True
                    sector_crack_reason = f"板块连板核心走弱（涨跌幅 {float(ld_pct):.1f}%）"

            # Check multiple members diving
            pool = matched_theme.get("pool") or matched_theme.get("members") or []
            diving_n = 0
            for m in pool:
                m_pct = m.get("pct")
                if m_pct is not None and float(m_pct) <= float(SELL_SECTOR_CRACK_DIVERGENT_DOWN_PCT):
                    diving_n += 1
            if diving_n >= int(SELL_SECTOR_CRACK_DIVERGENT_DOWN_N):
                sector_crack = True
                sector_crack_reason = (
                    f"板块内有 {diving_n} 只个股跌幅≥{abs(float(SELL_SECTOR_CRACK_DIVERGENT_DOWN_PCT)):.0f}%跳水"
                )

    if sector_crack and not ready:
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
