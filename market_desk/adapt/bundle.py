"""Adapt bundle assembly, trade feedback, and stock reputation nudge."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from market_desk.config import (
    ADAPT_SIZE_MULT_MAX,
    ADAPT_SIZE_MULT_MIN,
    ADAPT_STOCK_REP_ADJ_MAX,
    ADAPT_STOCK_REP_ADJ_MIN,
    ADAPT_STOCK_WATCH_STREAK,
)

from market_desk.adapt.context import (
    _ADAPT_CACHE,
    _clamp,
    count_missed_by_context,
    make_trade_context,
)
from market_desk.adapt.gates import build_context_gate_bias
from market_desk.adapt.sizing import (
    build_desk_source_size_bias,
    build_exec_size_bias,
    build_size_heat,
    compose_size_mult,
    phase_kind_size_mult,
)
from market_desk.adapt.sell_learn import build_sell_mfe_bias, segment_sell_mult
from market_desk.adapt.tune import (
    build_auto_tune_deltas,
    build_pullback_sweet,
    store_tune_cache,
)


# Module-level OHLC pack for adapt same_day_plan remapping (set by engine).
_ADAPT_BARS: dict[str, Any] = {}


def set_adapt_bars(bars: dict[str, Any] | None) -> None:
    """Install code→(dates, closes, ohlc) pack used by adapt remapping."""
    global _ADAPT_BARS
    _ADAPT_BARS = dict(bars or {})


def get_adapt_bars() -> dict[str, Any]:
    """Return the current adapt OHLC pack (may be empty)."""
    return dict(_ADAPT_BARS)


def _remap_rows_for_adapt_standard(
    rows: list[dict[str, Any]] | None,
    standard: str,
    *,
    closes_map: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Soft-remap signal rows so adapt knobs can follow a non-classic standard.

    ``filled``: keep traded + labeled rows (实盘成交样本).
    ``same_day_plan``: re-score buys with ``score_signal_with_closes`` when
    OHLC pack is available (from ``closes_map`` or ``set_adapt_bars``).
    """
    from market_desk.filters import normalize_code
    from market_desk.review import is_buy_signal, score_signal_with_closes

    src = list(rows or [])
    std = str(standard or "classic").strip().lower()
    if std == "filled":
        kept = [
            dict(r)
            for r in src
            if int(r.get("traded") or 0) and str(r.get("outcome_label") or "").strip()
        ]
        return kept, len(kept)
    if std != "same_day_plan":
        return src, 0
    packed = closes_map if closes_map is not None else get_adapt_bars()
    if not packed:
        return src, 0
    remapped: list[dict[str, Any]] = []
    n_hit = 0
    for raw in src:
        row = dict(raw)
        if not is_buy_signal(row.get("signal_type")):
            remapped.append(row)
            continue
        code = normalize_code(row.get("code"))
        triple = packed.get(code) if code else None
        if not triple or len(triple) < 2:
            remapped.append(row)
            continue
        dates = list(triple[0] or [])
        closes = list(triple[1] or [])
        ohlc = triple[2] if len(triple) >= 3 and isinstance(triple[2], dict) else {}
        scored = score_signal_with_closes(
            row,
            closes,
            dates,
            opens=ohlc.get("open"),
            lows=ohlc.get("low"),
            highs=ohlc.get("high"),
            standard="same_day_plan",
        )
        if scored and scored.get("outcome_label"):
            row.update(scored)
            n_hit += 1
        remapped.append(row)
    return remapped, n_hit


def build_adapt_bundle(
    *,
    phase: str = "",
    segment_key: str = "",
    rows: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Assemble day-scoped adaptive soft controls for the battle desk.

    Outcome-based soft knobs default to **persisted classic** labels on
    ``signals``. When ``adapt_follow_outcome`` is on:
      - ``filled`` → traded+labeled rows
      - ``same_day_plan`` → re-score with OHLC pack from ``set_adapt_bars``
    """
    from market_desk.settings import setting

    follow = bool(setting("adapt_follow_outcome", False))
    ui_std = str(setting("outcome_standard", "classic") or "classic").strip().lower()
    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=240)
    except Exception:
        rows = []
    basis = "classic"
    basis_note = "调参反哺固定用落库 classic 标签"
    if follow and ui_std in ("same_day_plan", "filled"):
        remapped, n_hit = _remap_rows_for_adapt_standard(rows, ui_std)
        if n_hit > 0:
            rows = remapped
            basis = ui_std
            if ui_std == "same_day_plan":
                basis_note = (
                    f"调参跟随当日plan·隔日（已用日线重算 {n_hit} 笔）"
                )
            else:
                basis_note = (
                    f"调参跟随实盘成交（已用已交易样本 {n_hit} 笔）"
                )
        else:
            basis_note = (
                f"跟随开关已开且界面为 {ui_std}，但暂无可用重算样本，仍用 classic"
            )
    elif follow and ui_std != "classic":
        basis_note = f"跟随开关已开；界面 {ui_std} 暂无重映路径，仍用 classic"
    heat = build_size_heat(rows)
    sweet = build_pullback_sweet(rows)
    sell_mfe = build_sell_mfe_bias(rows, use_cache=False)
    seg = segment_sell_mult(segment_key, rows)
    pk_stock = phase_kind_size_mult(phase, "stock", rows)
    pk_etf = phase_kind_size_mult(phase, "etf", rows)
    current_ctx = make_trade_context(segment_key=segment_key, metrics=metrics)
    context_gate = build_context_gate_bias(current_ctx, rows)
    _ADAPT_CACHE["context_gate"] = context_gate
    missed_by: dict[str, int] = {}
    missed_n = 0
    desk_hits: list[dict[str, Any]] = []
    try:
        from market_desk.review import (
            build_desk_source_hit_rates,
            build_kind_hit_rates,
            build_missed_buys,
            cached_buy_gate_bias,
        )

        gate = cached_buy_gate_bias()
        kinds = build_kind_hit_rates(rows)
        desk_hits = build_desk_source_hit_rates(rows)
        day = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
        # Prefer dash date; signals may also use YYYYMMDD.
        day_alt = day.replace("-", "")
        missed = build_missed_buys(rows, trade_date=day)
        if not missed and len(day_alt) == 8:
            missed = build_missed_buys(rows, trade_date=day_alt)
        # Also scan recent days in rows for same-context historical misses.
        # Use all missed on loaded window matching any trade_date for bucket counts.
        all_missed: list[dict[str, Any]] = []
        seen_days: set[str] = set()
        for r in rows or []:
            d = str(r.get("trade_date") or "")[:10]
            if not d or d in seen_days:
                continue
            seen_days.add(d)
            all_missed.extend(build_missed_buys(rows, trade_date=d))
            if len(d) == 10:
                all_missed.extend(build_missed_buys(rows, trade_date=d.replace("-", "")))
        # Dedupe by id
        by_id: dict[Any, dict[str, Any]] = {}
        for m in all_missed:
            by_id[m.get("id") or id(m)] = m
        all_missed = list(by_id.values())
        missed_by = count_missed_by_context(all_missed)
        missed_n = len(missed)
    except Exception:
        gate = {}
        kinds = []
        desk_hits = []
    desk_src_bias = build_desk_source_size_bias(desk_hits)
    tune = build_auto_tune_deltas(
        gate_bias=gate,
        kind_hits=kinds,
        missed_n=missed_n,
        current_context=current_ctx,
        missed_by_context=missed_by,
    )
    _ADAPT_CACHE["missed_by_context"] = dict(missed_by)
    store_tune_cache(tune, current_ctx)
    heat_m = float(heat.get("size_mult") or 1.0)
    # Soft blend of phase×kind into a dedicated factor (not pre-multiplied into heat).
    pk_m = 1.0
    if pk_stock.get("ok"):
        pk_m = 0.65 + 0.35 * float(pk_stock.get("size_mult") or 1.0)
        pk_m = _clamp(pk_m, float(ADAPT_SIZE_MULT_MIN), float(ADAPT_SIZE_MULT_MAX))
    exec_bias = build_exec_size_bias(rows, use_cache=False)
    exec_m = float(exec_bias.get("size_mult") or 1.0)
    size_factors = [
        {"key": "heat", "label": "仓位热度", "mult": heat_m},
        {"key": "phase_kind", "label": "相位×品种", "mult": pk_m},
        {"key": "exec", "label": "执行分", "mult": exec_m},
    ]
    composed = compose_size_mult(size_factors)
    try:
        from market_desk.whitebox import fit_whitebox

        whitebox = fit_whitebox(rows)
    except Exception:
        whitebox = {"ok": False, "note": "白盒不可用"}
    notes = [
        n
        for n in (
            composed.get("note") if abs(float(composed.get("size_mult") or 1.0) - 1.0) > 0.02 else "",
            heat.get("note"),
            pk_stock.get("note") if pk_stock.get("ok") else "",
            exec_bias.get("note") if abs(exec_m - 1.0) > 0.02 else "",
            sell_mfe.get("note") if sell_mfe.get("ok") else "",
            context_gate.get("note") if context_gate.get("ok") else "",
            seg.get("note"),
            sweet.get("note") if sweet.get("ok") else "",
            tune.get("note") if tune.get("ok") else "",
            desk_src_bias.get("note") if desk_src_bias.get("ok") else "",
            whitebox.get("note") if whitebox.get("ok") else "",
        )
        if n
    ]
    return {
        "ok": True,
        "outcome_basis": basis,
        "adapt_follow_outcome": follow,
        "ui_outcome_standard": ui_std,
        "outcome_basis_note": basis_note,
        "size_heat": heat,
        "size_mult": float(composed.get("size_mult") or 1.0),
        "size_compose": composed,
        "size_factors": size_factors,
        "phase_kind": {"stock": pk_stock, "etf": pk_etf},
        "exec_size": exec_bias,
        "context_gate": context_gate,
        "segment_sell": seg,
        "sell_mfe": sell_mfe,
        "pullback_sweet": sweet,
        "auto_tune": tune,
        "desk_source_bias": desk_src_bias,
        "desk_hits": desk_hits,
        "context": current_ctx,
        "whitebox": whitebox,
        "note": " · ".join(notes[:6]),
    }


def record_trade_feedback(
    *,
    code: str,
    name: str = "",
    pnl_pct: float | None,
    entry_board: str = "",
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Update stock (and theme) reputation from a realized trade PnL."""
    from market_desk.db import upsert_stock_reputation, bump_theme_trade_adj
    from market_desk.filters import normalize_code
    from market_desk.mainline import theme_key

    c = normalize_code(code)
    if len(c) != 6:
        return {"ok": False, "error": "bad code"}
    day = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    pct = float(pnl_pct) if pnl_pct is not None else None
    win = bool(pct is not None and pct > 0.15)
    loss = bool(pct is not None and pct < -0.15)
    fake = bool(pct is not None and pct < -1.5)
    stock_row = upsert_stock_reputation(
        code=c,
        name=name,
        win=win,
        loss=loss,
        fake=fake,
        pnl_pct=pct,
        trade_date=day,
    )
    theme = theme_key(entry_board) if entry_board else ""
    theme_row = None
    if theme and pct is not None:
        # Soft theme trade nudge: wins +0.6, losses -1.2, hard loss -2.0.
        if pct >= 1.0:
            delta = 0.8
        elif pct > 0.15:
            delta = 0.4
        elif pct <= -3.0:
            delta = -2.0
        elif pct < -0.15:
            delta = -1.2
        else:
            delta = 0.0
        if abs(delta) > 1e-9:
            theme_row = bump_theme_trade_adj(theme, delta)
    return {"ok": True, "stock": stock_row, "theme": theme_row, "pnl_pct": pct}


def stock_rep_score_adj(code: str) -> dict[str, Any]:
    """Return soft score adjustment and watch flag for one ticker."""
    from market_desk.db import load_stock_reputation_one
    from market_desk.filters import normalize_code

    c = normalize_code(code)
    row = load_stock_reputation_one(c) or {}
    adj = float(row.get("score_adj") or 0)
    adj = _clamp(adj, float(ADAPT_STOCK_REP_ADJ_MIN), float(ADAPT_STOCK_REP_ADJ_MAX))
    streak = int(row.get("streak_loss") or 0)
    watch = streak >= int(ADAPT_STOCK_WATCH_STREAK) or str(row.get("label") or "") == "观察名单"
    return {
        "ok": bool(row),
        "score_adj": adj,
        "watch": watch,
        "label": row.get("label") or "",
        "streak_loss": streak,
        "note": (
            f"个股信誉{row.get('label') or ''} {adj:+.1f}"
            + ("·观察名单加严" if watch else "")
        ).strip(),
    }
