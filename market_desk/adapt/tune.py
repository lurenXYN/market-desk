"""Pullback sweet zone and clamped auto-tune deltas with cache."""

from __future__ import annotations

from typing import Any
from market_desk.config import (
    ADAPT_HEAT_MIN_N,
    ADAPT_MISSED_MIN_N,
    ADAPT_PULLBACK_CLAMP,
    ADAPT_TUNE_CLAMP,
)

from market_desk.adapt.context import (
    _ADAPT_CACHE,
    _buy_scored,
    _cache_day,
    _clamp,
    _is_win,
    _num,
)


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
