"""MA stickiness → upward fan pattern scoring on daily bars (pure functions)."""

from __future__ import annotations

from typing import Any


MA_PERIODS = (5, 10, 20, 30, 60)
MA_FAN_FORMULA_VERSION = 7


def _sma(closes: list[float], end: int, n: int) -> float | None:
    """Simple moving average ending at ``end`` (inclusive), length ``n``."""
    if end < n - 1 or end >= len(closes):
        return None
    window = closes[end - n + 1 : end + 1]
    if len(window) < n:
        return None
    return sum(window) / n


def _ma_bundle(closes: list[float], i: int) -> list[float] | None:
    """Return [MA5, MA10, MA20, MA30, MA60] at bar ``i``, or None."""
    out: list[float] = []
    for n in MA_PERIODS:
        v = _sma(closes, i, n)
        if v is None or v <= 0:
            return None
        out.append(v)
    return out


def _spread_pct(mas: list[float]) -> float:
    """(max-min)/mean of the MA bundle, in percent."""
    mean = sum(mas) / len(mas)
    if mean <= 0:
        return 999.0
    return (max(mas) - min(mas)) / mean * 100.0


def _slope_up(closes: list[float], i: int, n: int, look: int = 5) -> bool:
    """True when MA(n) at ``i`` is above MA(n) at ``i-look``."""
    a = _sma(closes, i, n)
    b = _sma(closes, i - look, n)
    if a is None or b is None:
        return False
    return a > b * 1.001


def score_pattern(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Score one name; return diagnostics or None when pattern fails hard gates.

    Observation layer: hard gates stay permissive; quality is expressed via
    ``score``, ``stage``, and structured ``tags`` rather than exclusion.
    """
    if len(bars) < 80:
        return None
    closes = [float(b["close"]) for b in bars]
    highs = [float(b.get("high") or b["close"]) for b in bars]
    lows = [float(b.get("low") or b["close"]) for b in bars]
    vols = [float(b.get("volume") or 0) for b in bars]
    i = len(bars) - 1

    # Stickiness: MA spread required; price amplitude is soft quality (P1 softened).
    sticky_max = 6.2
    sticky_len = 10
    sticky_amp_soft = 20.0   # quieter is better; not a hard cutoff
    sticky_amp_hard = 28.0   # only extreme chop drops the window
    best: tuple[float, float, int, int] | None = None  # avg, amp, s, e
    lo = max(60, i - 55)
    hi = i - 4
    for end in range(lo + (sticky_len - 1), hi + 1):
        start = end - (sticky_len - 1)
        spreads: list[float] = []
        ok = True
        for j in range(start, end + 1):
            mas = _ma_bundle(closes, j)
            if not mas:
                ok = False
                break
            sp = _spread_pct(mas)
            if sp > sticky_max:
                ok = False
                break
            spreads.append(sp)
        if not ok or not spreads:
            continue
        hi_w = max(highs[start : end + 1])
        lo_w = min(lows[start : end + 1])
        mid_w = (hi_w + lo_w) / 2.0
        amp = ((hi_w - lo_w) / mid_w * 100.0) if mid_w > 0 else 999.0
        if amp > sticky_amp_hard:
            continue
        avg = sum(spreads) / len(spreads)
        # Prefer tighter MA + quieter price; amp is a soft tie-break.
        key = avg + amp * 0.04
        if best is None or key < (best[0] + best[1] * 0.04):
            best = (avg, amp, start, end)
    if best is None:
        return None
    sticky_avg, sticky_amp, sticky_s, sticky_e = best

    fan_pick: dict[str, Any] | None = None
    for k in range(max(i - 6, sticky_e + 1), i + 1):
        mas = _ma_bundle(closes, k)
        if not mas:
            continue
        # Allow one adjacent inversion (near-bull stack) instead of strict order.
        inversions = sum(
            1 for t in range(len(mas) - 1) if mas[t] < mas[t + 1] * 0.998
        )
        if inversions > 1:
            continue
        ordered = inversions == 0
        slopes = sum(1 for n in (5, 10, 20, 30) if _slope_up(closes, k, n, 5))
        if slopes < 2:
            continue
        fan_sp = _spread_pct(mas)
        if fan_sp < sticky_avg * 1.18 and fan_sp < 3.6:
            continue
        if closes[k] < mas[0] * 0.985:
            continue
        fan_pick = {
            "i": k,
            "mas": mas,
            "spread": fan_sp,
            "slopes": slopes,
            "ordered": ordered,
            "inversions": inversions,
        }
    if not fan_pick:
        return None

    k = int(fan_pick["i"])
    fan_sp = float(fan_pick["spread"])
    mas = list(fan_pick["mas"])
    fan_ratio = fan_sp / max(sticky_avg, 0.15)
    slopes = int(fan_pick["slopes"])
    ordered = bool(fan_pick["ordered"])

    # P1: progressive fan — MA spread should rise over recent sessions.
    spread_hist: list[float] = []
    for t in range(max(sticky_e + 1, k - 4), k + 1):
        m = _ma_bundle(closes, t)
        if m:
            spread_hist.append(_spread_pct(m))
    rising_steps = 0
    if len(spread_hist) >= 2:
        rising_steps = sum(
            1 for a, b in zip(spread_hist, spread_hist[1:]) if b + 0.08 >= a
        )
    progressive = len(spread_hist) >= 3 and rising_steps >= max(1, len(spread_hist) - 2)
    one_day_pop = (
        len(spread_hist) >= 2
        and spread_hist[-1] >= spread_hist[0] * 1.55
        and rising_steps <= 1
    )

    # P1: clear MA60 downtrend hard-drops; mild weakness stays with a loud tag.
    ma60_now = _sma(closes, k, 60)
    ma60_prev = _sma(closes, k - 10, 60) if k >= 70 else None
    ma60_ok = True
    ma60_strong = False
    if ma60_now is not None and ma60_prev is not None and ma60_prev > 0:
        if ma60_now < ma60_prev * 0.985:
            return None  # clear MA60 downtrend — noise / down-leg squeeze
        ma60_ok = ma60_now >= ma60_prev * 0.995
        ma60_strong = ma60_now >= ma60_prev * 1.002

    sticky_vols = [vols[j] for j in range(sticky_s, sticky_e + 1) if vols[j] > 0]
    if len(sticky_vols) < 4:
        return None
    sticky_vol = sorted(sticky_vols)[len(sticky_vols) // 2]
    recent = [vols[j] for j in range(k - 4, k + 1) if j >= 0 and vols[j] > 0]
    if len(recent) < 3 or sticky_vol <= 0:
        return None
    recent_vol = sum(recent) / len(recent)
    vol_ratio = recent_vol / sticky_vol
    if vol_ratio < 0.95 or vol_ratio > 5.5:
        return None
    day_spike = vols[k] / max(recent_vol, 1.0)

    sticky_mid = sum(closes[sticky_s : sticky_e + 1]) / (sticky_e - sticky_s + 1)
    ext = (closes[k] / sticky_mid - 1.0) * 100.0 if sticky_mid > 0 else 0.0
    if ext > 150:
        return None

    if ext <= 28:
        stage = "初期"
    elif ext <= 55:
        stage = "中期"
    else:
        stage = "后期"

    days_since_sticky = max(0, k - sticky_e)
    freshness = "刚发散" if days_since_sticky <= 2 else (
        "续发散" if days_since_sticky <= 5 else "远端发散"
    )

    score = 0.0
    score += max(0.0, (sticky_max - sticky_avg) * 7)
    score += max(0.0, (sticky_amp_soft - sticky_amp) * 0.35)
    score += min(32.0, (fan_ratio - 1.3) * 11)
    if 1.25 <= vol_ratio <= 2.9:
        score += 12
    elif 0.95 <= vol_ratio < 1.25:
        score += 5
    elif 2.9 < vol_ratio <= 3.8:
        score += 3
    else:
        score -= 5
    if day_spike > 3.5:
        score -= 4
    score += slopes * 2.5
    if ordered:
        score += 4
    else:
        score -= 2
    if stage == "初期":
        score += 12
    elif stage == "中期":
        score += 5
    else:
        score -= (ext - 55) * 0.35
    if freshness == "刚发散":
        score += 4
    elif freshness == "远端发散":
        score -= 3
    if progressive:
        score += 6
    elif one_day_pop:
        score -= 5
    if ma60_strong:
        score += 3
    elif not ma60_ok:
        score -= 4

    tags: list[str] = [stage, freshness]
    # Surface MA60 first so the list chip is hard to miss.
    if ma60_strong:
        tags.append("MA60稳升")
    elif ma60_ok:
        tags.append("MA60稳")
    else:
        tags.append("⚠MA60偏弱")
    if sticky_avg <= 2.8:
        tags.append("粘连很紧")
    elif sticky_avg <= 4.5:
        tags.append("粘连够")
    else:
        tags.append("粘连偏松")
    if sticky_amp <= 12:
        tags.append("振幅小")
    elif sticky_amp <= 16:
        tags.append("振幅可控")
    else:
        tags.append("振幅偏大")
    if progressive:
        tags.append("渐进发散")
    elif one_day_pop:
        tags.append("一日拉开")
    if 1.25 <= vol_ratio <= 2.9:
        tags.append("量能温和")
    elif vol_ratio < 1.25:
        tags.append("量起步")
    elif vol_ratio <= 3.8:
        tags.append("量偏猛")
    else:
        tags.append("放量过猛")
    if day_spike > 3.2:
        tags.append("单日放量")
    if slopes >= 4:
        tags.append("坡度强")
    elif slopes >= 3:
        tags.append("坡度够")
    else:
        tags.append("坡度弱")
    if ordered:
        tags.append("多头排列")
    else:
        tags.append("近多头")
    if ext >= 70:
        tags.append(f"已拉{ext:.0f}%")
    elif stage == "中期":
        tags.append(f"离开{ext:.0f}%")

    return {
        "score": round(score, 1),
        "close": round(closes[k], 2),
        "pct": bars[k].get("pct"),
        "sticky_end": str(bars[sticky_e].get("date") or ""),
        "sticky_spread": round(sticky_avg, 2),
        "sticky_amp": round(sticky_amp, 1),
        "fan_spread": round(fan_sp, 2),
        "fan_ratio": round(fan_ratio, 2),
        "vol_ratio": round(vol_ratio, 2),
        "ma_order": ">".join(f"{v:.2f}" for v in mas),
        "note": " · ".join(tags) or "命中",
        "ext_pct": round(ext, 1),
        "stage": stage,
        "tags": tags,
        "freshness": freshness,
        "slopes": slopes,
        "progressive": progressive,
        "ma60_ok": ma60_ok,
    }


def amount_band_for_rank(rank: int | None) -> str:
    """Map a 1-based amount rank into a short liquidity-band tag."""
    try:
        r = int(rank or 0)
    except (TypeError, ValueError):
        r = 0
    if r <= 0:
        return ""
    if r <= 100:
        return "额档·头百"
    if r <= 400:
        return "额档·前400"
    if r <= 800:
        return "额档·400-800"
    return "额档·800+"
