"""Intraday minute-structure checks for recommendation ready flags."""

from __future__ import annotations

from typing import Any


def evaluate_minute_structure(minutes: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Judge whether the minute chart supports a ready buy.

    Rules (no volume field required):
    - Need enough samples.
    - Prefer price back above the short minute MA after a pullback.
    - Reject when sitting on the recent minute tip (chase risk).
    """
    rows = list(minutes or [])
    prices = []
    for row in rows:
        try:
            px = float(row.get("price"))
        except (TypeError, ValueError, AttributeError):
            continue
        if px > 0:
            prices.append(px)
    if len(prices) < 20:
        return {
            "ok": None,
            "label": "分时样本不足",
            "ma": None,
            "pullback_pct": None,
            "at_tip": False,
        }

    last = prices[-1]
    window = prices[-20:]
    ma = sum(window) / len(window)
    look = prices[-30:] if len(prices) >= 30 else prices
    hi = max(look)
    lo = min(look)
    pullback = (hi - last) / hi * 100.0 if hi > 0 else 0.0
    span = (hi - lo) / hi * 100.0 if hi > 0 else 0.0
    at_tip = pullback < (0.15 if span < 0.8 else 0.25)
    above_ma = last >= ma * 0.998
    # Soft pullback-then-hold: not tip, and not clearly under MA.
    ok = bool(above_ma and not at_tip)
    if at_tip:
        label = "分时贴近近期高点"
    elif not above_ma:
        label = "分时仍在均线下方"
    else:
        label = "分时回踩站稳"
    return {
        "ok": ok,
        "label": label,
        "ma": round(ma, 3),
        "pullback_pct": round(pullback, 2),
        "at_tip": at_tip,
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
        extra = "分时未站稳，先等回踩"
        rec["size_note"] = note if extra in note else (f"{note}；{extra}" if note else extra)
    return rec
