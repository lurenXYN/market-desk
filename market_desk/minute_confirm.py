"""Intraday minute-structure checks for recommendation ready flags."""

from __future__ import annotations

from typing import Any


def evaluate_minute_structure(minutes: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Judge whether the minute chart supports a ready buy.

    Prefer fewer false chase buys:
    - Enough samples.
    - Not sitting on the recent minute tip / grinding highs.
    - Price back on/above the short minute MA after some pullback.
    - When volume is available: recent bars should cool vs the prior window.
    """
    rows = list(minutes or [])
    prices: list[float] = []
    volumes: list[float] = []
    for row in rows:
        try:
            px = float(row.get("price"))
        except (TypeError, ValueError, AttributeError):
            continue
        if px <= 0:
            continue
        prices.append(px)
        try:
            vol = float(row.get("volume"))
        except (TypeError, ValueError, AttributeError):
            vol = 0.0
        volumes.append(max(0.0, vol))

    if len(prices) < 25:
        return {
            "ok": None,
            "label": "分时样本不足",
            "ma": None,
            "pullback_pct": None,
            "at_tip": False,
            "vol_ratio": None,
            "vol_ok": None,
        }

    last = prices[-1]
    window = prices[-20:]
    ma = sum(window) / len(window)
    look_n = 40 if len(prices) >= 40 else len(prices)
    look = prices[-look_n:]
    hi = max(look)
    lo = min(look)
    pullback = (hi - last) / hi * 100.0 if hi > 0 else 0.0
    span = (hi - lo) / hi * 100.0 if hi > 0 else 0.0
    tip_thr = 0.25 if span < 1.0 else 0.40
    at_tip = pullback < tip_thr
    prior = look[:-5] if len(look) > 8 else look[:-2]
    prior_hi = max(prior) if prior else hi
    grinding_high = bool(last >= prior_hi * 0.999 and pullback < 0.55)
    above_ma = last >= ma * 0.999
    shallow = pullback < 0.30

    vol_ratio: float | None = None
    vol_ok: bool | None = None
    vol_vals = [v for v in volumes[-look_n:] if v > 0]
    if len(vol_vals) >= 15:
        recent = volumes[-5:]
        base = volumes[-20:-5]
        recent_pos = [v for v in recent if v > 0]
        base_pos = [v for v in base if v > 0]
        if recent_pos and base_pos:
            r_avg = sum(recent_pos) / len(recent_pos)
            b_avg = sum(base_pos) / len(base_pos)
            if b_avg > 0:
                vol_ratio = round(r_avg / b_avg, 2)
                # Cool-down on the latest window; hot tip = chase.
                vol_ok = vol_ratio <= 0.95

    fails: list[str] = []
    if at_tip:
        fails.append("分时贴近近期高点")
    if grinding_high:
        fails.append("分时仍在抬高点")
    if not above_ma:
        fails.append("分时仍在均线下方")
    if shallow and not at_tip:
        fails.append("分时回撤过浅")
    if vol_ok is False and (at_tip or grinding_high or shallow or pullback < 0.6):
        fails.append("分时回踩量能未缩")
    elif vol_ok is False and above_ma and not at_tip and not grinding_high:
        # Price looks OK but volume still expanding — treat as chase risk.
        fails.append("分时放量未冷却")

    ok = len(fails) == 0
    if ok:
        label = "分时回踩缩量站稳" if vol_ok is True else "分时回踩站稳"
    else:
        label = fails[0]

    return {
        "ok": ok,
        "label": label,
        "ma": round(ma, 3),
        "pullback_pct": round(pullback, 2),
        "at_tip": at_tip,
        "vol_ratio": vol_ratio,
        "vol_ok": vol_ok,
        "fails": fails,
    }


def apply_minute_confirmations(
    recommend: dict[str, Any] | None,
    minutes_by_code: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """Downgrade ready cards that fail the minute-structure check."""
    rec = dict(recommend or {})
    items = [dict(x) for x in (rec.get("items") or [])]
    if not items:
        return rec
    minutes_by_code = minutes_by_code or {}
    changed = False
    for item in items:
        code = str(item.get("code") or "").zfill(6)
        if not code or code not in minutes_by_code:
            continue
        verdict = evaluate_minute_structure(minutes_by_code.get(code))
        item["minute"] = verdict
        if not item.get("ready"):
            continue
        if verdict.get("ok") is False:
            changed = True
            item["ready"] = False
            if item.get("wait_price") is not None:
                item["buy_price"] = item.get("wait_price")
            kind = item.get("kind") or "stock"
            item["role_label"] = "ETF 盯回踩" if kind == "etf" else "个股盯回踩"
            flag = str(verdict.get("label") or "分时未确认")
            fails = list(item.get("confirm_fail") or [])
            fails.append(flag)
            item["confirm_fail"] = fails
            item["reason"] = (str(item.get("reason") or "") + f"；确认失败：{flag}").strip("；")
    if not changed:
        rec["items"] = items
        return rec
    rec["items"] = items
    if rec.get("buy") and not any(x.get("ready") for x in items):
        rec["buy"] = False
        rec["title"] = "盯回踩价，先不追"
        note = str(rec.get("size_note") or "")
        extra = "分时未确认，先等回踩缩量"
        rec["size_note"] = note if extra in note else (f"{note}；{extra}" if note else extra)
    return rec
