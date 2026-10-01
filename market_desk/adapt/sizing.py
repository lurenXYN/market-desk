"""Size multipliers: heat, phase/kind, execution, and desk source."""

from __future__ import annotations

from typing import Any
from market_desk.config import (
    ADAPT_CONSEC_LOSS_HARD,
    ADAPT_CONSEC_LOSS_SOFT,
    ADAPT_EXEC_CHASE_RATIO,
    ADAPT_EXEC_MIN_N,
    ADAPT_EXEC_SCORE_HARD,
    ADAPT_EXEC_SCORE_SOFT,
    ADAPT_HEAT_MIN_N,
    ADAPT_HEAT_RECENT_N,
    ADAPT_LOSS_MULT_HARD,
    ADAPT_LOSS_MULT_SOFT,
    ADAPT_META_SIZE_MAX,
    ADAPT_META_SIZE_MIN,
    ADAPT_PHASE_KIND_MIN_N,
    ADAPT_SIZE_MULT_MAX,
    ADAPT_SIZE_MULT_MIN,
    ADAPT_WIN_MULT_CAP,
    ADAPT_WIN_PF_MIN,
    ADAPT_WIN_RATE_MIN,
)

from market_desk.adapt.context import (
    _ADAPT_CACHE,
    _buy_scored,
    _cache_day,
    _clamp,
    _is_win,
    _num,
)


def compose_size_mult(
    factors: list[dict[str, Any]] | None,
    *,
    lo: float | None = None,
    hi: float | None = None,
) -> dict[str, Any]:
    """Multiply named size factors, clamp the product, and return attribution.

    Each factor is ``{key, label, mult}``. Near-1.0 factors are kept in the
    attribution list but omitted from the human note. Soft DSS only — never a ban.
    """
    floor = float(ADAPT_META_SIZE_MIN if lo is None else lo)
    ceil = float(ADAPT_META_SIZE_MAX if hi is None else hi)
    cleaned: list[dict[str, Any]] = []
    raw = 1.0
    for raw_f in factors or []:
        if not isinstance(raw_f, dict):
            continue
        try:
            m = float(raw_f.get("mult") or 1.0)
        except (TypeError, ValueError):
            m = 1.0
        if m <= 0:
            m = 1.0
        key = str(raw_f.get("key") or "f")
        label = str(raw_f.get("label") or key)
        cleaned.append({"key": key, "label": label, "mult": round(m, 4)})
        raw *= m
    clamped = _clamp(raw, floor, ceil)
    hit_floor = raw < floor - 1e-9
    hit_ceil = raw > ceil + 1e-9
    active = [f for f in cleaned if abs(float(f["mult"]) - 1.0) > 0.02]
    bits = [f"{f['label']}×{f['mult']:.2f}" for f in active[:6]]
    if hit_floor:
        bits.append(f"合流夹底→×{clamped:.2f}")
    elif hit_ceil:
        bits.append(f"合流夹顶→×{clamped:.2f}")
    elif abs(clamped - 1.0) > 0.02:
        bits.append(f"合流×{clamped:.2f}")
    return {
        "ok": True,
        "size_mult": round(clamped, 3),
        "raw_product": round(raw, 4),
        "clamped": bool(hit_floor or hit_ceil),
        "floor": floor,
        "ceil": ceil,
        "factors": cleaned,
        "active": active,
        "note": " · ".join(bits) if bits else "仓位合流×1",
    }


def build_size_heat(
    rows: list[dict[str, Any]] | None = None,
    *,
    recent_n: int | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Compute a soft size multiplier from recent win-rate and loss streak.

    Consecutive losses force defense; hot recent window may lightly raise size.
    """
    if use_cache and rows is None and _ADAPT_CACHE.get("day") == _cache_day() and _ADAPT_CACHE.get("heat"):
        return dict(_ADAPT_CACHE["heat"])
    n_want = int(recent_n or ADAPT_HEAT_RECENT_N)
    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=max(240, n_want * 3))
    except Exception:
        rows = []
    scored = _buy_scored(rows)
    recent = scored[-n_want:] if scored else []
    if len(recent) < int(ADAPT_HEAT_MIN_N):
        out = {
            "ok": False,
            "n": len(recent),
            "size_mult": 1.0,
            "win_rate": None,
            "consec_loss": 0,
            "profit_factor": None,
            "note": f"仓位热度样本不足（n={len(recent)}，需≥{ADAPT_HEAT_MIN_N}）",
        }
        if use_cache and rows is not None:
            pass
        elif use_cache:
            _ADAPT_CACHE["day"] = _cache_day()
            _ADAPT_CACHE["heat"] = out
        return out
    wins = [r for r in recent if _is_win(str(r.get("outcome_label") or ""))]
    losses = [r for r in recent if not _is_win(str(r.get("outcome_label") or ""))]
    win_rate = 100.0 * len(wins) / len(recent)
    win_pnl = sum(max(0.0, _num(r.get("outcome_day3_pct")) or 0.0) for r in wins)
    loss_pnl = sum(abs(min(0.0, _num(r.get("outcome_day3_pct")) or 0.0)) for r in losses)
    if loss_pnl <= 1e-9:
        pf = 99.0 if win_pnl > 0 else 1.0
    else:
        pf = win_pnl / loss_pnl
    consec = 0
    for r in reversed(recent):
        if _is_win(str(r.get("outcome_label") or "")):
            break
        consec += 1
    mult = 1.0
    bits: list[str] = [f"近{len(recent)}笔胜率{win_rate:.0f}%"]
    if consec >= int(ADAPT_CONSEC_LOSS_HARD):
        mult = float(ADAPT_LOSS_MULT_HARD)
        bits.append(f"连亏{consec}→×{mult}")
    elif consec >= int(ADAPT_CONSEC_LOSS_SOFT):
        mult = float(ADAPT_LOSS_MULT_SOFT)
        bits.append(f"连亏{consec}→×{mult}")
    elif (
        win_rate >= float(ADAPT_WIN_RATE_MIN)
        and pf >= float(ADAPT_WIN_PF_MIN)
        and consec == 0
    ):
        # Mild heat boost only when not coming off a loss.
        heat = 1.0 + min(0.35, (win_rate - 50.0) / 100.0 + (pf - 1.0) * 0.08)
        mult = min(float(ADAPT_WIN_MULT_CAP), heat)
        bits.append(f"PF{pf:.2f}→×{mult:.2f}")
    mult = _clamp(mult, float(ADAPT_SIZE_MULT_MIN), float(ADAPT_SIZE_MULT_MAX))
    out = {
        "ok": True,
        "n": len(recent),
        "hit_n": len(wins),
        "size_mult": round(mult, 3),
        "win_rate": round(win_rate, 1),
        "consec_loss": consec,
        "profit_factor": round(pf, 2),
        "note": "；".join(bits),
    }
    if use_cache and rows is None:
        _ADAPT_CACHE["day"] = _cache_day()
        _ADAPT_CACHE["heat"] = out
    return out


def phase_kind_size_mult(
    phase: str,
    kind: str,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Soft size/score nudge from phase×kind hit-rate (never a hard ban)."""
    from market_desk.review import build_phase_kind_hit_rates

    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=240)
        hits = build_phase_kind_hit_rates(rows)
    except Exception:
        hits = []
    kind_f = "etf" if str(kind or "") == "etf" else "stock"
    phase_f = str(phase or "").strip() or "未标"
    row = next(
        (
            h
            for h in hits
            if str(h.get("phase") or "") == phase_f and str(h.get("kind") or "") == kind_f
        ),
        None,
    )
    if not row or int(row.get("scored_n") or 0) < int(ADAPT_PHASE_KIND_MIN_N):
        return {"ok": False, "size_mult": 1.0, "score_adj": 0.0, "note": ""}
    rate = float(row.get("hit_rate") or 0)
    n = int(row.get("scored_n") or 0)
    mult = 1.0
    score_adj = 0.0
    note = f"{phase_f}×{kind_f}命中{rate:.0f}%（n={n}）"
    if rate < 35:
        mult = 0.65
        score_adj = -4.0
        note += "→缩仓"
    elif rate < 45:
        mult = 0.85
        score_adj = -2.0
        note += "→偏小仓"
    elif rate >= 55 and n >= 8:
        mult = 1.12
        score_adj = 2.0
        note += "→轻加仓"
    mult = _clamp(mult, float(ADAPT_SIZE_MULT_MIN), float(ADAPT_SIZE_MULT_MAX))
    return {
        "ok": True,
        "size_mult": round(mult, 3),
        "score_adj": score_adj,
        "hit_rate": rate,
        "n": n,
        "note": note,
    }


def build_exec_size_bias(
    rows: list[dict[str, Any]] | None = None,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Soft size multiplier from recent plan-execution quality (chase / score).

    Never bans buys — only shrinks suggested risk when fills chase the band.
    Uses shared paper signals only (not cross-user exec diary) so one account's
    manual fills cannot skew desk-wide size bias.
    """
    if use_cache and rows is None and _ADAPT_CACHE.get("day") == _cache_day() and _ADAPT_CACHE.get("exec_bias"):
        return dict(_ADAPT_CACHE["exec_bias"])
    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=240)
    except Exception:
        rows = []
    from market_desk.review import build_exec_score, is_buy_signal

    traded = [
        r
        for r in (rows or [])
        if is_buy_signal(r.get("signal_type")) and int(r.get("traded") or 0)
    ]
    # Prefer chronological tail so recent discipline matters most.
    traded = sorted(
        traded,
        key=lambda r: (str(r.get("signaled_at") or ""), str(r.get("id") or 0)),
    )
    recent = traded[-20:]
    ex = build_exec_score(recent)
    n = int(ex.get("traded_buy_n") or 0)
    score = ex.get("score")
    chase_n = int(ex.get("chase_n") or 0)
    min_n = int(ADAPT_EXEC_MIN_N)
    if n < min_n or score is None:
        out = {
            "ok": False,
            "n": n,
            "score": score,
            "chase_n": chase_n,
            "size_mult": 1.0,
            "note": f"执行分样本不足（n={n}，需≥{min_n}）",
        }
    else:
        mult = 1.0
        bits: list[str] = [f"近{n}笔执行分{score}"]
        if float(score) < float(ADAPT_EXEC_SCORE_HARD):
            mult = 0.70
            bits.append("执行偏弱→×0.70")
        elif float(score) < float(ADAPT_EXEC_SCORE_SOFT):
            mult = 0.85
            bits.append("执行一般→×0.85")
        chase_ratio = chase_n / max(n, 1)
        if chase_ratio >= float(ADAPT_EXEC_CHASE_RATIO):
            mult = min(mult, 0.75)
            bits.append(f"追高占比{chase_ratio:.0%}→≤×0.75")
        mult = _clamp(mult, float(ADAPT_SIZE_MULT_MIN), 1.0)
        out = {
            "ok": abs(mult - 1.0) > 0.02,
            "n": n,
            "score": score,
            "chase_n": chase_n,
            "chase_ratio": round(chase_ratio, 3),
            "size_mult": round(mult, 3),
            "note": "；".join(bits),
        }
    if use_cache and rows is None:
        _ADAPT_CACHE["day"] = _cache_day()
        _ADAPT_CACHE["exec_bias"] = out
    return out


def build_desk_source_size_bias(
    desk_hits: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Soft-damp link card size when its review hit-rate lags mainline.

    Does not touch sticky mainline buys; only BOARD_LINK / WATCH_TRIAL multipliers.
    """
    from market_desk.config import DESK_SRC_HIT_DAMP, DESK_SRC_HIT_GAP_PP, DESK_SRC_HIT_MIN_N

    by = {str(r.get("source") or ""): r for r in (desk_hits or []) if r.get("source")}
    main = by.get("main") or {}
    main_n = int(main.get("scored_n") or 0)
    main_rate = main.get("hit_rate")
    out: dict[str, Any] = {
        "ok": False,
        "link_mult": 1.0,
        "trial_mult": 1.0,
        "note": "",
    }
    if main_rate is None or main_n < int(DESK_SRC_HIT_MIN_N):
        return out
    notes: list[str] = []
    gap = float(DESK_SRC_HIT_GAP_PP)
    damp = float(DESK_SRC_HIT_DAMP)

    def _lag(src: str) -> bool:
        row = by.get(src) or {}
        n = int(row.get("scored_n") or 0)
        rate = row.get("hit_rate")
        if rate is None or n < int(DESK_SRC_HIT_MIN_N):
            return False
        return float(rate) <= float(main_rate) - gap

    if _lag("link"):
        out["link_mult"] = damp
        notes.append(f"联动命中落后主线→联动仓×{damp:g}")
    if _lag("watch_trial"):
        out["trial_mult"] = damp
        notes.append(f"自选试探命中落后→试探仓×{damp:g}")
    if notes:
        out["ok"] = True
        out["note"] = "；".join(notes)
    return out
