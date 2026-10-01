"""Context gate bias, gate multiplier, and sticky margin bias."""

from __future__ import annotations

from typing import Any
from market_desk.config import ADAPT_CTX_GATE_MIN_N, ADAPT_TUNE_CLAMP
from market_desk.settings import setting

from market_desk.adapt.context import _ADAPT_CACHE, _cache_day, _clamp, context_from_row


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
    from market_desk.review import is_buy_signal

    for row in rows or []:
        if not is_buy_signal(row.get("signal_type")):
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
