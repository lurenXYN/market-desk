"""Learned segment sell multipliers and MFE-based sell bias."""

from __future__ import annotations

from typing import Any
from market_desk.config import (
    ADAPT_TUNE_CLAMP,
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

from market_desk.adapt.context import (
    _ADAPT_CACHE,
    _cache_day,
    _clamp,
    _num,
    context_from_row,
    segment_bucket_from_clock,
)


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
    early = sum(
        1
        for r in sells
        if str(r.get("outcome_label") or "") in ("卖后继续涨", "卖飞")
    )
    rate = round(100.0 * hit / n, 1)
    widen = rate < float(SELL_REVIEW_WIDEN_BELOW)
    tighten = rate >= float(SELL_REVIEW_TIGHTEN_ABOVE)
    mult = 1.0
    note = f"{key}卖后回落{rate}%（n={n}，续涨/卖飞{early}）"
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
        if str(r.get("outcome_label") or "") not in ("卖后继续涨", "卖飞"):
            continue
        mae = _num(r.get("outcome_mae_pct"))
        if mae is None:
            continue
        # MAE ≤0 when price rose after sell; absolute = left on table.
        left = abs(mae) if mae <= 0 else float(mae)
        if not (0.3 <= left <= 20.0):
            continue
        # 卖飞 samples weigh slightly heavier by duplicating into the pool.
        lefts.append(left)
        if str(r.get("outcome_label") or "") == "卖飞":
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
