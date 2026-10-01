"""Volume Absorption: active-buy share and VWAP support at the buy band."""

from __future__ import annotations

from typing import Any


def _series(minutes: list[dict[str, Any]] | None) -> tuple[list[float], list[float], list[float]]:
    """Split minute rows into aligned price / volume / avg lists (bad rows skipped)."""
    prices: list[float] = []
    volumes: list[float] = []
    avgs: list[float] = []
    for row in minutes or []:
        try:
            px = float(row.get("price"))
        except (TypeError, ValueError, AttributeError):
            continue
        if px <= 0:
            continue
        try:
            vol = max(0.0, float(row.get("volume") or 0.0))
        except (TypeError, ValueError):
            vol = 0.0
        try:
            avg = float(row.get("avg") or 0.0)
        except (TypeError, ValueError):
            avg = 0.0
        prices.append(px)
        volumes.append(vol)
        avgs.append(avg if avg > 0 else 0.0)
    return prices, volumes, avgs


def tick_rule_buy_share(
    prices: list[float], volumes: list[float], lookback: int
) -> tuple[float | None, int]:
    """Estimate the active-buy volume share with the minute tick rule.

    An up bar counts as buyer-initiated, a down bar as seller-initiated, and a
    flat bar inherits the previous non-flat direction (split evenly when none).

    Args:
        prices: Minute close prices, oldest first.
        volumes: Minute volumes aligned with ``prices``.
        lookback: Number of trailing bars to classify.

    Returns:
        ``(share, bars)`` where ``share`` is buy / (buy + sell) over bars with
        volume, or ``None`` when no such bar exists; ``bars`` counts them.
    """
    n = len(prices)
    if n < 2:
        return None, 0
    start = max(1, n - int(lookback))
    direction = 0
    for i in range(1, start):
        if prices[i] > prices[i - 1]:
            direction = 1
        elif prices[i] < prices[i - 1]:
            direction = -1
    buy = sell = 0.0
    bars = 0
    for i in range(start, n):
        if prices[i] > prices[i - 1]:
            direction = 1
        elif prices[i] < prices[i - 1]:
            direction = -1
        vol = volumes[i]
        if vol <= 0:
            continue
        bars += 1
        if direction > 0:
            buy += vol
        elif direction < 0:
            sell += vol
        else:
            buy += vol / 2.0
            sell += vol / 2.0
    total = buy + sell
    if total <= 0:
        return None, bars
    return round(buy / total, 3), bars


def vwap_series(prices: list[float], volumes: list[float], avgs: list[float]) -> list[float]:
    """Return the session VWAP path (official avg when present, else cumulative)."""
    if sum(1 for a in avgs if a > 0) >= max(5, len(avgs) // 2):
        out: list[float] = []
        last = 0.0
        for a in avgs:
            last = a if a > 0 else last
            out.append(last)
        return [x for x in out if x > 0]
    out = []
    cum_pv = cum_v = 0.0
    for px, vol in zip(prices, volumes):
        cum_pv += px * vol
        cum_v += vol
        if cum_v > 0:
            out.append(cum_pv / cum_v)
    return out


def vwap_slope_pct(vwap: list[float], bars: int) -> float | None:
    """Return the VWAP slope in percent per minute over the trailing ``bars``."""
    if len(vwap) < 3:
        return None
    span = min(int(bars), len(vwap) - 1)
    base = vwap[-1 - span]
    if base <= 0 or span <= 0:
        return None
    return round((vwap[-1] - base) / base * 100.0 / span, 4)


def outer_share(quote: dict[str, Any] | None) -> float | None:
    """Return day outer / (outer + inner) volume from a Tencent quote."""
    q = quote or {}
    try:
        outer = float(q.get("outer_vol") or 0.0)
        inner = float(q.get("inner_vol") or 0.0)
    except (TypeError, ValueError):
        return None
    total = outer + inner
    if outer <= 0 or inner <= 0 or total <= 0:
        return None
    return round(outer / total, 3)


def evaluate_absorption(
    minutes: list[dict[str, Any]] | None,
    quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Judge whether active buyers are absorbing supply at the buy band.

    Separates a shakeout drift (price easing while buyers keep lifting offers
    and VWAP holds) from a real distribution leg (sellers dominate and VWAP
    rolls over). Three outcomes keep the soft-scoring contract:

    - ``ok=True``: clear absorption; ready may light.
    - ``ok=False``: clear absence of active buying; ready must stay off.
    - ``ok=None``: thin or mixed evidence; soft hint only, never a kill.

    Args:
        minutes: Today's minute rows with ``price`` / ``volume`` / optional ``avg``.
        quote: Optional Tencent quote carrying ``outer_vol`` / ``inner_vol``.

    Returns:
        A verdict dict with ``ok``, ``label`` and the supporting metrics.
    """
    from market_desk.config import (
        ABSORB_BUY_SHARE_FAIL,
        ABSORB_BUY_SHARE_OK,
        ABSORB_LOOKBACK,
        ABSORB_MIN_BARS,
        ABSORB_OUTER_FAIL,
        ABSORB_VWAP_BARS,
        ABSORB_VWAP_SLOPE_FAIL,
    )

    prices, volumes, avgs = _series(minutes)
    share, bars = tick_rule_buy_share(prices, volumes, int(ABSORB_LOOKBACK))
    vwap = vwap_series(prices, volumes, avgs)
    slope = vwap_slope_pct(vwap, int(ABSORB_VWAP_BARS))
    o_share = outer_share(quote)
    last = prices[-1] if prices else None
    vw_last = vwap[-1] if vwap else None
    above_vwap = (
        None if last is None or vw_last is None else bool(last >= vw_last * 0.999)
    )
    window = prices[-int(ABSORB_LOOKBACK):] if prices else []
    drifting = bool(len(window) >= 2 and window[-1] < window[0])
    out: dict[str, Any] = {
        "ok": None,
        "label": "承接数据不足",
        "buy_share": share,
        "outer_share": o_share,
        "vwap": None if vw_last is None else round(vw_last, 3),
        "vwap_slope": slope,
        "above_vwap": above_vwap,
        "bars": bars,
        "drifting": drifting,
    }
    if bars < int(ABSORB_MIN_BARS) or share is None:
        if o_share is None:
            return out
        share_eff = o_share
    else:
        share_eff = share

    rolling = slope is not None and slope <= float(ABSORB_VWAP_SLOPE_FAIL)
    weak_share = share_eff < float(ABSORB_BUY_SHARE_FAIL)
    weak_outer = o_share is not None and o_share < float(ABSORB_OUTER_FAIL)
    if weak_share and rolling:
        out.update(ok=False, label="均价下拐无主动承接")
        return out
    if weak_share and weak_outer:
        out.update(ok=False, label="主动买盘承接不足")
        return out
    strong_share = share_eff >= float(ABSORB_BUY_SHARE_OK)
    outer_ok = o_share is None or o_share >= float(ABSORB_OUTER_FAIL)
    if strong_share and outer_ok and not rolling:
        out.update(
            ok=True,
            label="阴跌有承接（假阴跌）" if drifting else "主动买盘承接",
        )
        return out
    out["label"] = "承接待确认"
    return out


ABSORB_FAIL_LABELS = frozenset({"均价下拐无主动承接", "主动买盘承接不足"})


def apply_absorption_gates(
    recommend: dict[str, Any] | None,
    minutes_by_code: dict[str, list[dict[str, Any]]] | None,
    quotes_by_code: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Keep ready off at the buy band until active buyers absorb supply.

    Only cards at the band (``ready`` or ``near_entry``) are gated. A clear
    absorption fail clears ``ready`` and records a hard ``confirm_fail`` so the
    later near-entry re-arm cannot light it again; inconclusive evidence only
    adds a soft label. Cards without minute data are left untouched.

    Args:
        recommend: A recommend box with ``items``.
        minutes_by_code: Minute rows keyed by six-digit code.
        quotes_by_code: Optional Tencent quotes with outer / inner volume.

    Returns:
        A copy of ``recommend`` with ``absorption`` attached per evaluated item.
    """
    from market_desk.verdict.common import _demote_buy_to_wait, _join_hint

    rec = dict(recommend or {})
    items = [dict(x) for x in (rec.get("items") or [])]
    if not items or not minutes_by_code:
        return rec
    quotes_by_code = quotes_by_code or {}
    killed = False
    unconfirmed: list[str] = []
    for item in items:
        code = str(item.get("code") or "").zfill(6)
        if code not in minutes_by_code:
            continue
        verdict = evaluate_absorption(minutes_by_code.get(code), quotes_by_code.get(code))
        item["absorption"] = verdict
        item.pop("absorb_unconfirmed", None)
        item.pop("absorb_warn", None)
        at_band = bool(item.get("ready") or item.get("near_entry"))
        if not at_band:
            continue
        label = str(verdict.get("label") or "")
        if verdict.get("ok") is False:
            fails = list(item.get("confirm_fail") or [])
            if label not in fails:
                fails.append(label)
            item["confirm_fail"] = fails
            if item.get("ready"):
                killed = True
                item["ready"] = False
                _demote_buy_to_wait(item)
                kind = item.get("kind") or "stock"
                item["role_label"] = "ETF 盯承接" if kind == "etf" else "个股盯承接"
            item["reason"] = _join_hint(str(item.get("reason") or ""), f"确认失败：{label}")
        elif verdict.get("ok") is None:
            soft = list(item.get("confirm_soft") or [])
            if label and label not in soft:
                soft.append(label)
            item["confirm_soft"] = soft
            if item.get("ready"):
                warn = unconfirmed_warning(verdict)
                item["absorb_unconfirmed"] = True
                item["absorb_warn"] = warn
                item["reason"] = _join_hint(f"⚠ {warn}", str(item.get("reason") or ""))
                unconfirmed.append(str(item.get("name") or code))
    rec["items"] = items
    if killed and rec.get("buy") and not any(x.get("ready") for x in items):
        rec["buy"] = False
        rec["title"] = "盯回踩价，先不追"
        rec["size_note"] = _join_hint(
            str(rec.get("size_note") or ""), "主动买盘未承接，先等接盘"
        )
    rec["absorb_unconfirmed"] = unconfirmed
    if unconfirmed:
        head = "⚠ 承接未确认：" + "、".join(unconfirmed[:3]) + "（仅凭形态点亮，宜小仓或等主买确认）"
        rec["size_note"] = _join_hint(head, str(rec.get("size_note") or ""))
    return rec


def unconfirmed_warning(verdict: dict[str, Any] | None) -> str:
    """Build the loud one-line warning for a ready card lacking absorption proof.

    Args:
        verdict: ``evaluate_absorption`` output with ``ok`` left as ``None``.

    Returns:
        A short Chinese line naming the missing evidence and the next step.
    """
    from market_desk.config import ABSORB_BUY_SHARE_OK, ABSORB_MIN_BARS

    v = verdict or {}
    bits: list[str] = []
    share = v.get("buy_share")
    if share is None:
        share = v.get("outer_share")
    if int(v.get("bars") or 0) < int(ABSORB_MIN_BARS) and v.get("outer_share") is None:
        bits.append(f"分时不足{int(ABSORB_MIN_BARS)}根")
    elif share is not None:
        bits.append(f"主买{float(share) * 100:.0f}%")
    slope = v.get("vwap_slope")
    if slope is not None:
        bits.append(f"均价{float(slope):+.3f}%/分")
    detail = "，".join(bits) or str(v.get("label") or "证据混杂")
    need = f"主买≥{float(ABSORB_BUY_SHARE_OK) * 100:.0f}%"
    return f"承接未确认（{detail}）：ready 仅凭形态，宜小仓或等{need}"
