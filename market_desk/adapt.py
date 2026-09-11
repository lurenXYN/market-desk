"""Adaptive soft feedback: size heat, stock/theme trade reputation, segment sell,
learned pullback sweet-zone, and clamped auto-tune deltas.

All outputs are soft multipliers / score nudges — never hard buy bans.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.config import (
    ADAPT_CONSEC_LOSS_HARD,
    ADAPT_CONSEC_LOSS_SOFT,
    ADAPT_CTX_GATE_MIN_N,
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
    ADAPT_MISSED_MIN_N,
    ADAPT_PHASE_KIND_MIN_N,
    ADAPT_PULLBACK_CLAMP,
    ADAPT_SIZE_MULT_MAX,
    ADAPT_SIZE_MULT_MIN,
    ADAPT_STOCK_REP_ADJ_MAX,
    ADAPT_STOCK_REP_ADJ_MIN,
    ADAPT_STOCK_WATCH_STREAK,
    ADAPT_TUNE_CLAMP,
    ADAPT_VOL_AMOUNT_PCTILE,
    ADAPT_VOL_HS300_ABS,
    ADAPT_WIN_MULT_CAP,
    ADAPT_WIN_PF_MIN,
    ADAPT_WIN_RATE_MIN,
    SEGMENT_SELL_LEARN_MIN_N,
    SEGMENT_SELL_MULT,
    SELL_MFE_MIN_N,
    SELL_MFE_TIGHTEN_BELOW,
    SELL_MFE_TIGHTEN_MULT,
    SELL_MFE_WIDEN_ABOVE,
    SELL_MFE_WIDEN_MULT,
    SELL_REVIEW_TIGHTEN_ABOVE,
    SELL_REVIEW_TIGHTEN_MULT,
    SELL_REVIEW_WIDEN_BELOW,
    SELL_REVIEW_WIDEN_MULT,
)
from market_desk.settings import setting

_ADAPT_CACHE: dict[str, Any] = {
    "day": "",
    "bundle": None,
    "sweet": None,
    "heat": None,
    "tune": None,
}

SEG_BUCKET_LABELS = {
    "auction": "竞价",
    "open30": "开盘",
    "morning": "午前",
    "afternoon": "午后",
    "closed": "休市",
    "unknown": "未分时",
}
VOL_BUCKET_LABELS = {
    "high": "高波动",
    "low": "低波动",
    "unknown": "波动未标",
}


def _cache_day() -> str:
    """Return today's cache key."""
    return datetime.now().strftime("%Y-%m-%d")


def _num(v: Any) -> float | None:
    """Parse a numeric field; return None when missing/invalid."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp ``v`` into ``[lo, hi]``."""
    return max(float(lo), min(float(hi), float(v)))


def segment_bucket_from_clock(
    signaled_at: str | None = None,
    *,
    segment_key: str | None = None,
) -> str:
    """Map a clock / segment key into a coarse miss-context bucket."""
    if segment_key:
        key = str(segment_key).strip()
        if key in ("auction", "open30", "morning", "afternoon"):
            return key
        if key == "closed":
            return "closed"
    text = str(signaled_at or "").strip()
    if len(text) >= 16:
        try:
            hh = int(text[11:13])
            mm = int(text[14:16])
            minutes = hh * 60 + mm
        except ValueError:
            return "unknown"
        if 9 * 60 + 15 <= minutes < 9 * 60 + 30:
            return "auction"
        if 9 * 60 + 30 <= minutes < 10 * 60:
            return "open30"
        if 10 * 60 <= minutes < 11 * 60 + 30:
            return "morning"
        if 13 * 60 <= minutes < 15 * 60:
            return "afternoon"
        if 11 * 60 + 30 <= minutes < 13 * 60:
            return "morning"  # lunch inherits morning bucket for labeling
    return "unknown"


def vol_bucket_from_metrics(metrics: dict[str, Any] | None) -> str:
    """Classify high/low volatility from live desk metrics (no ATR dependency)."""
    m = metrics or {}
    hs = _num(m.get("hs300_pct"))
    cyb = _num(m.get("cyb_pct"))
    amt = _num(m.get("amount_pctile"))
    big_drop = bool(m.get("big_drop"))
    if hs is None and cyb is None and amt is None and not big_drop:
        return "unknown"
    high = False
    if hs is not None and abs(hs) >= float(ADAPT_VOL_HS300_ABS):
        high = True
    if cyb is not None and abs(cyb) >= float(ADAPT_VOL_HS300_ABS) + 0.3:
        high = True
    if amt is not None and amt >= float(ADAPT_VOL_AMOUNT_PCTILE):
        high = True
    if big_drop:
        high = True
    return "high" if high else "low"


def make_trade_context(
    *,
    segment_key: str | None = None,
    signaled_at: str | None = None,
    metrics: dict[str, Any] | None = None,
    vol: str | None = None,
) -> dict[str, Any]:
    """Build a situational tag: segment × vol (never generalizes across buckets)."""
    seg = segment_bucket_from_clock(signaled_at, segment_key=segment_key)
    vol_b = str(vol or "").strip() or vol_bucket_from_metrics(metrics)
    if vol_b not in ("high", "low", "unknown"):
        vol_b = "unknown"
    key = f"{seg}|{vol_b}"
    label = f"{SEG_BUCKET_LABELS.get(seg, seg)}·{VOL_BUCKET_LABELS.get(vol_b, vol_b)}"
    tagged = seg != "unknown" and vol_b != "unknown"
    return {
        "key": key,
        "segment": seg,
        "vol": vol_b,
        "label": label,
        "tagged": tagged,
    }


def context_from_row(row: dict[str, Any] | None) -> dict[str, Any]:
    """Recover context from a signal / missed-buy row (payload or infer)."""
    row = row or {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    saved = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    if saved.get("key") and saved.get("segment"):
        seg = str(saved.get("segment") or "unknown")
        vol = str(saved.get("vol") or "unknown")
        tagged = seg not in ("", "unknown") and vol in ("high", "low")
        return {
            "key": str(saved.get("key")),
            "segment": seg,
            "vol": vol,
            "label": str(saved.get("label") or saved.get("key")),
            "tagged": tagged,
        }
    return make_trade_context(
        segment_key=saved.get("segment") or row.get("segment"),
        signaled_at=str(row.get("signaled_at") or ""),
        vol=saved.get("vol"),
        metrics=None,
    )


def count_missed_by_context(missed: list[dict[str, Any]] | None) -> dict[str, int]:
    """Count missed buys per context key; untagged rows are excluded from write-back."""
    out: dict[str, int] = {}
    for row in missed or []:
        ctx = row.get("context") if isinstance(row.get("context"), dict) else context_from_row(row)
        if not ctx.get("tagged"):
            continue
        key = str(ctx.get("key") or "")
        if not key:
            continue
        out[key] = int(out.get(key) or 0) + 1
    return out


def _buy_scored(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return scored buy rows respecting hit_rate_mode (newest last)."""
    mode = str(setting("hit_rate_mode", "traded") or "traded").strip().lower()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if str(row.get("signal_type") or "") != "buy":
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        out.append(row)
    # Prefer chronological order by signal_at / id.
    out.sort(key=lambda r: (str(r.get("signal_at") or ""), int(r.get("id") or 0)))
    return out


def _is_win(label: str) -> bool:
    """Return True for positive buy outcome labels."""
    return label in {"次日红", "三日红"}


def build_context_gate_bias(
    current_context: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Contextual false/true-kill nudges for gate thresholds (segment×vol only).

    Never generalizes across buckets. Mults are clamped by ADAPT_TUNE_CLAMP around 1.0.
    """
    from market_desk.config import (
        BUY_GATE_FALSE_KILL_MIN,
        BUY_GATE_LOOSEN_MULT,
        BUY_GATE_TIGHTEN_MULT,
        BUY_GATE_TRUE_KILL_MIN,
    )
    from market_desk.review import _gate_bucket

    ctx = current_context or {}
    if not ctx.get("tagged"):
        return {
            "ok": False,
            "mult": 1.0,
            "gates": {},
            "context": ctx or None,
            "note": "情景未标签·闸门阈值用全局",
        }
    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=300)
    except Exception:
        rows = []
    seg = str(ctx.get("segment") or "")
    vol = str(ctx.get("vol") or "")
    kills: dict[str, int] = {}
    false_kills: dict[str, int] = {}
    true_kills: dict[str, int] = {}
    matched = 0
    for row in rows or []:
        if str(row.get("signal_type") or "") != "buy":
            continue
        if int(row.get("skipped") or 0):
            continue
        row_ctx = context_from_row(row)
        if not row_ctx.get("tagged"):
            continue
        if str(row_ctx.get("segment") or "") != seg:
            continue
        if vol in ("high", "low") and str(row_ctx.get("vol") or "") != vol:
            continue
        matched += 1
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        fails = [
            str(x)
            for x in (
                payload.get("confirm_fail_hist")
                or payload.get("final_fail")
                or payload.get("confirm_fail")
                or []
            )
            if str(x).strip()
        ]
        if not fails:
            continue
        label = str(row.get("outcome_label") or "")
        ready_now = int(row.get("ready") or 0)
        for bucket in {_gate_bucket(f) for f in fails}:
            kills[bucket] = kills.get(bucket, 0) + 1
            if ready_now or not label:
                continue
            if label in {"次日红", "三日红"}:
                false_kills[bucket] = false_kills.get(bucket, 0) + 1
            elif label in {"次日绿", "三日绿"}:
                true_kills[bucket] = true_kills.get(bucket, 0) + 1

    target = {
        "分时": "minute",
        "离日高": "off_high",
        "薄确认/共振": "thin",
        "相对强弱": "rel_strength",
    }
    clamp = float(ADAPT_TUNE_CLAMP)
    gates: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    mult = 1.0
    min_n = int(ADAPT_CTX_GATE_MIN_N)
    for gate_zh, key in target.items():
        kn = int(kills.get(gate_zh) or 0)
        fk = int(false_kills.get(gate_zh) or 0)
        tk = int(true_kills.get(gate_zh) or 0)
        if kn < min_n:
            continue
        g_mult = 1.0
        mode = "neutral"
        if fk >= int(BUY_GATE_FALSE_KILL_MIN) and fk >= tk:
            g_mult = float(BUY_GATE_LOOSEN_MULT)
            mode = "loosen"
        elif tk >= int(BUY_GATE_TRUE_KILL_MIN) and tk > fk:
            g_mult = float(BUY_GATE_TIGHTEN_MULT)
            mode = "tighten"
        else:
            continue
        g_mult = _clamp(g_mult, 1.0 - clamp, 1.0 + clamp)
        gates[key] = {
            "gate": gate_zh,
            "mult": round(g_mult, 3),
            "false_kill_n": fk,
            "true_kill_n": tk,
            "kill_n": kn,
            "mode": mode,
        }
        if mode == "loosen":
            mult = min(mult, g_mult)
        else:
            mult = max(mult, g_mult)
        notes.append(f"{ctx.get('label') or seg}·{gate_zh}{mode}×{g_mult:.2f}")
    return {
        "ok": bool(gates),
        "mult": round(mult, 3),
        "gates": gates,
        "context": ctx,
        "matched_n": matched,
        "note": "；".join(notes) if notes else f"{ctx.get('label') or seg}情景闸门样本不足",
    }


def resolve_gate_mult(gate_key: str, default: float = 1.0) -> float:
    """Merge global buy-gate bias with contextual overlay for one gate key."""
    mult = float(default)
    try:
        from market_desk.review import cached_buy_gate_bias

        bias = cached_buy_gate_bias() or {}
        gates = bias.get("gates") or {}
        g = gates.get(gate_key) or {}
        if g.get("mult") is not None:
            mult = float(g["mult"])
        elif bias.get("mult") is not None and gate_key in ("minute", "off_high"):
            mult = float(bias.get("mult") or 1.0)
    except Exception:
        pass
    try:
        ctx_bias = _ADAPT_CACHE.get("context_gate")
        if isinstance(ctx_bias, dict):
            cg = (ctx_bias.get("gates") or {}).get(gate_key) or {}
            if cg.get("mult") is not None:
                # Soft stack then clamp ±ADAPT_TUNE_CLAMP around 1.0 for the product.
                stacked = mult * float(cg["mult"])
                clamp = float(ADAPT_TUNE_CLAMP)
                # Prefer the more extreme of global vs context when they agree;
                # otherwise take geometric mean-ish via product then clamp.
                mult = _clamp(stacked, 1.0 - clamp, 1.0 + clamp)
    except Exception:
        pass
    return max(0.75, min(1.25, float(mult)))


def build_sticky_margin_bias(
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Soft-adjust sticky switch margin from recent mainline churn.

    High same-day flip rate → stickier (larger margin); very calm days → slightly
    easier switches. Result is a multiplier around 1.0 clamped by ADAPT_TUNE_CLAMP.
    """
    if use_cache and _ADAPT_CACHE.get("day") == _cache_day() and _ADAPT_CACHE.get("sticky"):
        return dict(_ADAPT_CACHE["sticky"])
    try:
        from market_desk.db import load_recent_mainline_switch_stats

        stats = load_recent_mainline_switch_stats(8)
    except Exception:
        stats = {"days": 0, "avg_per_day": 0.0, "max_day": 0, "total": 0}
    days = int(stats.get("days") or 0)
    avg = float(stats.get("avg_per_day") or 0.0)
    mx = int(stats.get("max_day") or 0)
    base = float(setting("sticky_margin", 12.0) or 12.0)
    clamp = float(ADAPT_TUNE_CLAMP)
    mult = 1.0
    note = f"换防样本日{days}"
    if days < 3:
        out = {
            "ok": False,
            "mult": 1.0,
            "margin": round(base, 1),
            "base_margin": base,
            "note": "换防学习样本不足",
            "stats": stats,
        }
    else:
        if avg >= 2.5 or mx >= 5:
            mult = 1.0 + clamp * 0.75  # stickier
            note = f"换防偏频(均{avg:.1f}/日)·粘性↑"
        elif avg <= 0.6 and mx <= 2:
            mult = 1.0 - clamp * 0.5
            note = f"换防偏稳(均{avg:.1f}/日)·粘性↓"
        else:
            note = f"换防正常(均{avg:.1f}/日)"
        mult = _clamp(mult, 1.0 - clamp, 1.0 + clamp)
        margin = round(base * mult, 1)
        out = {
            "ok": abs(mult - 1.0) > 0.02,
            "mult": round(mult, 3),
            "margin": margin,
            "base_margin": base,
            "note": note,
            "stats": stats,
        }
    if use_cache:
        _ADAPT_CACHE["day"] = _cache_day()
        _ADAPT_CACHE["sticky"] = out
    return out


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


def _sell_segment_of(row: dict[str, Any]) -> str:
    """Resolve session segment for a sell signal (payload context or clock)."""
    ctx = context_from_row(row)
    seg = str(ctx.get("segment") or "").strip()
    if seg and seg != "unknown":
        return seg
    return segment_bucket_from_clock(str(row.get("signaled_at") or ""))


def build_segment_sell_learned(
    segment_key: str | None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Learn a sell-band mult from 卖后回落 hit-rate inside one session segment.

    Low hit-rate → historically sold too early → widen; high → tighten. Falls
    back to ``ok=False`` when the segment sample is thin.
    """
    key = str(segment_key or "morning").strip() or "morning"
    mode = str(setting("hit_rate_mode", "traded") or "traded").strip().lower()
    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=300)
    except Exception:
        rows = []
    sells: list[dict[str, Any]] = []
    for r in rows or []:
        if str(r.get("signal_type") or "") != "sell":
            continue
        if int(r.get("skipped") or 0):
            continue
        if not r.get("outcome_label"):
            continue
        if mode == "traded" and not int(r.get("traded") or 0):
            continue
        if _sell_segment_of(r) != key:
            continue
        sells.append(r)
    n = len(sells)
    min_n = int(SEGMENT_SELL_LEARN_MIN_N)
    if n < min_n:
        return {
            "ok": False,
            "segment": key,
            "n": n,
            "hit_rate": None,
            "mult": 1.0,
            "widen": False,
            "tighten": False,
            "note": f"{key}卖点样本不足（n={n}，需≥{min_n}）",
        }
    hit = sum(1 for r in sells if str(r.get("outcome_label") or "") == "卖后回落")
    early = sum(1 for r in sells if str(r.get("outcome_label") or "") == "卖后继续涨")
    rate = round(100.0 * hit / n, 1)
    widen = rate < float(SELL_REVIEW_WIDEN_BELOW)
    tighten = rate >= float(SELL_REVIEW_TIGHTEN_ABOVE)
    mult = 1.0
    note = f"{key}卖后回落{rate}%（n={n}，续涨{early}）"
    if widen:
        mult = float(SELL_REVIEW_WIDEN_MULT)
        note += "·偏早→放宽"
    elif tighten:
        mult = float(SELL_REVIEW_TIGHTEN_MULT)
        note += "·偏准→收紧"
    else:
        note += "·中性"
    return {
        "ok": True,
        "segment": key,
        "n": n,
        "hit_rate": rate,
        "hit_n": hit,
        "early_n": early,
        "mult": round(mult, 3),
        "widen": widen,
        "tighten": tighten,
        "note": note,
    }


def segment_sell_mult(
    segment_key: str | None,
    rows: list[dict[str, Any]] | None = None,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Return sell-band mult: learned per-segment when n enough, else static prior.

    Learned mult is blended with the static prior and clamped ±ADAPT_TUNE_CLAMP
    around that prior so afternoon does not flip overnight.
    """
    key = str(segment_key or "morning").strip() or "morning"
    cache_key = f"seg_sell|{key}"
    if (
        use_cache
        and rows is None
        and _ADAPT_CACHE.get("day") == _cache_day()
        and isinstance(_ADAPT_CACHE.get(cache_key), dict)
    ):
        return dict(_ADAPT_CACHE[cache_key])

    table = SEGMENT_SELL_MULT or {}
    prior = float(table.get(key, table.get("morning", 1.0)))
    labels = {
        "auction": "竞价",
        "open30": "开盘",
        "morning": "午前",
        "afternoon": "午后",
        "closed": "收盘",
    }
    learned = build_segment_sell_learned(key, rows)
    clamp = float(ADAPT_TUNE_CLAMP)
    if learned.get("ok"):
        # Learned mult is relative to 1.0 (widen 1.12 / tighten 0.96); scale the prior.
        nudged = prior * float(learned.get("mult") or 1.0)
        mult = _clamp(nudged, prior * (1.0 - clamp), prior * (1.0 + clamp))
        mult = _clamp(mult, 0.75, 1.25)
        note = f"{labels.get(key, key)}学习×{mult:.2f}（先验{prior:.2f}；{learned.get('note')}）"
        source = "learned"
        widen = mult > prior + 0.02 or mult > 1.02
        tighten = mult < prior - 0.02 or mult < 0.98
    else:
        mult = prior
        note = f"{labels.get(key, key)}先验×{mult:.2f}（{learned.get('note')}）"
        source = "prior"
        widen = mult > 1.02
        tighten = mult < 0.98

    out = {
        "ok": True,
        "segment": key,
        "mult": round(mult, 3),
        "prior": round(prior, 3),
        "widen": widen,
        "tighten": tighten,
        "source": source,
        "learned": learned,
        "note": note,
    }
    if use_cache and rows is None:
        _ADAPT_CACHE["day"] = _cache_day()
        _ADAPT_CACHE[cache_key] = out
    return out


def build_exec_size_bias(
    rows: list[dict[str, Any]] | None = None,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Soft size multiplier from recent plan-execution quality (chase / score).

    Never bans buys — only shrinks suggested risk when fills chase the band.
    """
    if use_cache and rows is None and _ADAPT_CACHE.get("day") == _cache_day() and _ADAPT_CACHE.get("exec_bias"):
        return dict(_ADAPT_CACHE["exec_bias"])
    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=240)
    except Exception:
        rows = []
    from market_desk.review import build_exec_score

    traded = [
        r
        for r in (rows or [])
        if str(r.get("signal_type") or "") == "buy" and int(r.get("traded") or 0)
    ]
    # Prefer chronological tail so recent discipline matters most.
    traded = sorted(
        traded,
        key=lambda r: (str(r.get("signaled_at") or ""), int(r.get("id") or 0)),
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


def build_sell_mfe_bias(
    rows: list[dict[str, Any]] | None = None,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Soft take-profit widen/tighten from early-sell left-on-table (|MAE|).

    ``卖后继续涨`` sells store MAE as price/peak−1 (≤0 when price kept rising).
    Large median left-on-table → historically sold too early → widen take/pb.
    """
    if use_cache and rows is None and _ADAPT_CACHE.get("day") == _cache_day() and _ADAPT_CACHE.get("sell_mfe"):
        return dict(_ADAPT_CACHE["sell_mfe"])
    mode = str(setting("hit_rate_mode", "traded") or "traded").strip().lower()
    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=300)
    except Exception:
        rows = []
    lefts: list[float] = []
    for r in rows or []:
        if str(r.get("signal_type") or "") != "sell":
            continue
        if int(r.get("skipped") or 0):
            continue
        if mode == "traded" and not int(r.get("traded") or 0):
            continue
        if str(r.get("outcome_label") or "") != "卖后继续涨":
            continue
        mae = _num(r.get("outcome_mae_pct"))
        if mae is None:
            continue
        # MAE ≤0 when price rose after sell; absolute = left on table.
        left = abs(mae) if mae <= 0 else float(mae)
        if 0.3 <= left <= 20.0:
            lefts.append(left)
    n = len(lefts)
    if n < int(SELL_MFE_MIN_N):
        out = {
            "ok": False,
            "n": n,
            "mult": 1.0,
            "widen": False,
            "tighten": False,
            "note": f"卖点MFE样本不足（n={n}，需≥{SELL_MFE_MIN_N}）",
        }
    else:
        lefts.sort()
        p50 = lefts[len(lefts) // 2]
        clamp = float(ADAPT_TUNE_CLAMP)
        mult = 1.0
        widen = False
        tighten = False
        note = f"早卖留下涨幅中位{p50:.1f}%（n={n}）"
        if p50 >= float(SELL_MFE_WIDEN_ABOVE):
            mult = float(SELL_MFE_WIDEN_MULT)
            widen = True
            note += "·偏早→放宽止盈/回撤"
        elif p50 <= float(SELL_MFE_TIGHTEN_BELOW):
            mult = float(SELL_MFE_TIGHTEN_MULT)
            tighten = True
            note += "·走弱快→略收紧"
        mult = _clamp(mult, 1.0 - clamp, 1.0 + clamp)
        out = {
            "ok": True,
            "n": n,
            "p50": round(p50, 2),
            "mult": round(mult, 3),
            "widen": widen,
            "tighten": tighten,
            "note": note,
        }
    if use_cache and rows is None:
        _ADAPT_CACHE["day"] = _cache_day()
        _ADAPT_CACHE["sell_mfe"] = out
    return out


def build_pullback_sweet(
    rows: list[dict[str, Any]] | None = None,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Derive pullback sweet-max from winning trades' MAE distribution.

    Uses |MAE| percentiles of 次日红/三日红 buys; clamps vs config defaults.
    """
    if use_cache and rows is None and _ADAPT_CACHE.get("day") == _cache_day() and _ADAPT_CACHE.get("sweet"):
        return dict(_ADAPT_CACHE["sweet"])
    from market_desk.config import STOCK_PULLBACK_BAND_MAX, STOCK_PULLBACK_SWEET_MAX

    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=240)
    except Exception:
        rows = []
    scored = _buy_scored(rows)
    wins = [r for r in scored if _is_win(str(r.get("outcome_label") or ""))]
    maes: list[float] = []
    for r in wins:
        mae = _num(r.get("outcome_mae_pct"))
        if mae is None:
            continue
        # MAE is typically ≤0 (adverse); use absolute depth.
        depth = abs(mae) if mae <= 0 else 0.0
        if 0.3 <= depth <= 12.0:
            maes.append(depth)
    base_sweet = float(STOCK_PULLBACK_SWEET_MAX)
    base_max = float(STOCK_PULLBACK_BAND_MAX)
    if len(maes) < int(ADAPT_HEAT_MIN_N):
        out = {
            "ok": False,
            "n": len(maes),
            "sweet_max": base_sweet,
            "band_max": base_max,
            "note": f"MAE甜区样本不足（n={len(maes)}）",
        }
    else:
        maes.sort()
        p50 = maes[len(maes) // 2]
        p80 = maes[min(len(maes) - 1, int(len(maes) * 0.8))]
        clamp = float(ADAPT_PULLBACK_CLAMP)
        sweet = _clamp(p80, base_sweet * (1.0 - clamp), base_sweet * (1.0 + clamp))
        band = _clamp(
            max(p80 * 1.25, sweet + 0.6),
            base_max * (1.0 - clamp),
            base_max * (1.0 + clamp),
        )
        out = {
            "ok": True,
            "n": len(maes),
            "mae_p50": round(p50, 2),
            "mae_p80": round(p80, 2),
            "sweet_max": round(sweet, 2),
            "band_max": round(band, 2),
            "note": f"胜单MAE p50={p50:.1f}% p80={p80:.1f}%→甜区≤{sweet:.1f}%",
        }
    if use_cache and rows is None:
        _ADAPT_CACHE["day"] = _cache_day()
        _ADAPT_CACHE["sweet"] = out
    return out


def build_auto_tune_deltas(
    *,
    gate_bias: dict[str, Any] | None = None,
    kind_hits: list[dict[str, Any]] | None = None,
    missed_n: int = 0,
    current_context: dict[str, Any] | None = None,
    missed_by_context: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Clamped soft deltas for pullback sensitivity (shared ±ADAPT_TUNE_CLAMP).

    Channels (gate / miss / kind) are computed separately then multiplied. When
    gate wants loosen and kind wants tighten, conflict damping sets both to 1.0.
    Missed-buy nudges only apply for a fully tagged current context bucket.
    """
    clamp = float(ADAPT_TUNE_CLAMP)
    notes: list[str] = []
    gb = gate_bias or {}
    gates = gb.get("gates") or {}

    gate_mult = 1.0
    g_mult = float(gb.get("mult") or 1.0)
    if gb.get("loosen") and g_mult < 1.0:
        gate_mult = g_mult
        notes.append(f"闸门假杀→×{g_mult}")
    elif gb.get("tighten") and g_mult > 1.0:
        gate_mult = min(1.0 + clamp, g_mult)
        notes.append(f"闸门真杀→×{gate_mult:.2f}")
    off = gates.get("off_high") or {}
    if off.get("mode") == "loosen":
        gate_mult = min(gate_mult, float(off.get("mult") or 0.9))

    miss_mult = 1.0
    ctx = current_context or {}
    bucket_n = 0
    if ctx.get("tagged") and ctx.get("key"):
        bucket_n = int((missed_by_context or {}).get(str(ctx["key"])) or 0)
        if bucket_n >= int(ADAPT_MISSED_MIN_N):
            miss_mult = 1.0 - clamp * 0.5
            notes.append(
                f"{ctx.get('label') or ctx['key']}漏买{bucket_n}"
                f"→略放宽（夹紧±{int(clamp * 100)}%）"
            )
    elif int(missed_n or 0) >= int(ADAPT_MISSED_MIN_N):
        notes.append(
            f"漏买{int(missed_n)}笔未分情景·不调参（需时段×波动标签）"
        )

    kind_mult = 1.0
    for row in kind_hits or []:
        rate = row.get("hit_rate")
        n = int(row.get("scored_n") or 0)
        if rate is None or n < 5:
            continue
        if str(row.get("kind") or "") != "stock":
            continue
        if float(rate) < 35:
            kind_mult = max(kind_mult, 1.0 + clamp * 0.5)
            notes.append("个股命中低→回踩更严")
        elif float(rate) >= 55 and n >= 8:
            kind_mult = min(kind_mult, 1.0 - clamp * 0.25)

    # Conflict damp: gate loosen vs kind tighten (or reverse) → neutral both.
    conflict = False
    if (gate_mult < 0.999 and kind_mult > 1.001) or (gate_mult > 1.001 and kind_mult < 0.999):
        gate_mult = 1.0
        kind_mult = 1.0
        conflict = True
        notes.append("闸门×品种冲突取中")

    pb_min_mult = _clamp(gate_mult * miss_mult * kind_mult, 1.0 - clamp, 1.0 + clamp)
    return {
        "ok": bool(notes),
        "pb_min_mult": round(pb_min_mult, 3),
        "gate_mult": round(gate_mult, 3),
        "miss_mult": round(miss_mult, 3),
        "kind_mult": round(kind_mult, 3),
        "conflict": conflict,
        "clamp": clamp,
        "context": ctx or None,
        "missed_bucket_n": bucket_n,
        "missed_by_context": dict(missed_by_context or {}),
        "note": "；".join(notes) if notes else "暂无自动调参",
    }


def cache_tune_key(context: dict[str, Any] | None = None) -> str:
    """Build day|segment|vol cache key for contextual auto-tune."""
    ctx = context or {}
    return f"{_cache_day()}|{ctx.get('segment') or 'na'}|{ctx.get('vol') or 'na'}"


def store_tune_cache(tune: dict[str, Any], context: dict[str, Any] | None = None) -> None:
    """Persist tune under a situational cache key."""
    _ADAPT_CACHE["day"] = _cache_day()
    _ADAPT_CACHE["tune_key"] = cache_tune_key(context)
    _ADAPT_CACHE["tune"] = tune
    _ADAPT_CACHE["context"] = dict(context or {})


def get_cached_tune(context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return cached tune only when day+segment+vol matches."""
    if _ADAPT_CACHE.get("day") != _cache_day():
        return None
    want = cache_tune_key(context)
    if _ADAPT_CACHE.get("tune_key") != want:
        return None
    tune = _ADAPT_CACHE.get("tune")
    return dict(tune) if isinstance(tune, dict) else None


def resolve_auto_tune(
    *,
    current_context: dict[str, Any] | None = None,
    gate_bias: dict[str, Any] | None = None,
    kind_hits: list[dict[str, Any]] | None = None,
    missed_n: int = 0,
    missed_by_context: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return situational auto-tune, preferring matching cache then rebuilding."""
    cached = get_cached_tune(current_context)
    if cached is not None:
        return cached
    gb = gate_bias
    kinds = kind_hits
    if gb is None:
        try:
            from market_desk.review import cached_buy_gate_bias

            gb = cached_buy_gate_bias()
        except Exception:
            gb = {}
    if kinds is None:
        try:
            from market_desk.db import load_signals
            from market_desk.review import build_kind_hit_rates

            kinds = build_kind_hit_rates(load_signals(limit=240))
        except Exception:
            kinds = []
    # Preserve missed_by_context from prior bundle cache when only context changes.
    missed_map = missed_by_context
    if missed_map is None and isinstance(_ADAPT_CACHE.get("missed_by_context"), dict):
        missed_map = dict(_ADAPT_CACHE["missed_by_context"])
    tune = build_auto_tune_deltas(
        gate_bias=gb or {},
        kind_hits=kinds or [],
        missed_n=missed_n,
        current_context=current_context,
        missed_by_context=missed_map,
    )
    store_tune_cache(tune, current_context)
    return tune


def build_adapt_bundle(
    *,
    phase: str = "",
    segment_key: str = "",
    rows: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Assemble day-scoped adaptive soft controls for the battle desk."""
    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=240)
    except Exception:
        rows = []
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
    try:
        from market_desk.review import (
            build_kind_hit_rates,
            build_missed_buys,
            cached_buy_gate_bias,
        )

        gate = cached_buy_gate_bias()
        kinds = build_kind_hit_rates(rows)
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
            whitebox.get("note") if whitebox.get("ok") else "",
        )
        if n
    ]
    return {
        "ok": True,
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
        "context": current_ctx,
        "whitebox": whitebox,
        "note": " · ".join(notes[:5]),
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
