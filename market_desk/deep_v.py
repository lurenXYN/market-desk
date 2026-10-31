"""Deep-V bounce shadow: CSI1000 intraday V-day chase vs wait (display only).

Locked definition (TODO 2026-10-28):
* Deep-V day: index max drawdown from open ≥ ``DEEP_V_DD_PCT``, and close
  recovers ≥ ``DEEP_V_RECOVER_FRAC`` of that drawdown from the session low.
* Group A: first ready at/after 14:00 on a deep-V day (``deep_v_chase``).
* Group B: first ready on the next trading day after a deep-V day (``deep_v_lag1``).
Compare three-day excess (minus bench) A−B; soft prompts only after the
pre-registered sample gate — this module never touches ready / gates.
"""

from __future__ import annotations

from typing import Any

from market_desk.numbers import num


def classify_deep_v_day(
    points: list[dict[str, Any]] | None,
    *,
    dd_pct: float | None = None,
    recover_frac: float | None = None,
) -> dict[str, Any]:
    """Classify one session's minute path as a deep-V day.

    Args:
        points: Minute bars with ``price`` (open = first, close = last).
        dd_pct / recover_frac: Threshold overrides; default from config.

    Returns:
        ``{ok, deep_v, dd_pct, recover, open, low, close, n}``.
    """
    from market_desk.config import DEEP_V_DD_PCT, DEEP_V_RECOVER_FRAC

    thr_dd = float(DEEP_V_DD_PCT if dd_pct is None else dd_pct)
    thr_rec = float(DEEP_V_RECOVER_FRAC if recover_frac is None else recover_frac)
    px = [float(num(p.get("price")) or 0.0) for p in (points or []) if num(p.get("price"))]
    px = [p for p in px if p > 0]
    if len(px) < 30:
        return {
            "ok": False,
            "deep_v": False,
            "dd_pct": None,
            "recover": None,
            "open": None,
            "low": None,
            "close": None,
            "n": len(px),
            "note": "分时点不足",
        }
    open_px, low_px, close_px = px[0], min(px), px[-1]
    dd_amt = open_px - low_px
    dd = (dd_amt / open_px) * 100.0 if open_px > 0 else 0.0
    recover = ((close_px - low_px) / dd_amt) if dd_amt > 1e-9 else 0.0
    deep = bool(dd >= thr_dd and recover >= thr_rec)
    return {
        "ok": True,
        "deep_v": deep,
        "dd_pct": round(dd, 3),
        "recover": round(recover, 3),
        "open": round(open_px, 4),
        "low": round(low_px, 4),
        "close": round(close_px, 4),
        "n": len(px),
        "thr_dd": thr_dd,
        "thr_recover": thr_rec,
    }


def _hhmm_from_stamp(stamp: str | None) -> str:
    s = str(stamp or "")
    if len(s) >= 16 and s[10] == " ":
        return s[11:16]
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return ""


def freeze_deep_v_fields(
    *,
    trade_date: str,
    signaled_at: str | None,
    ready: bool,
    day_row: dict[str, Any] | None,
    prev_deep: bool,
) -> dict[str, Any]:
    """Build payload freeze keys for one buy signal (first insert wins upstream)."""
    from market_desk.config import DEEP_V_CHASE_HHMM, DEEP_V_SHADOW_SINCE

    day = str(trade_date or "")[:10]
    out: dict[str, Any] = {}
    if not day or day < str(DEEP_V_SHADOW_SINCE)[:10]:
        return out
    is_dv = bool(day_row and day_row.get("ok") and day_row.get("deep_v"))
    if is_dv:
        out["deep_v_day"] = True
        out["deep_v_dd"] = day_row.get("dd_pct")
        out["deep_v_recover"] = day_row.get("recover")
        hhmm = _hhmm_from_stamp(signaled_at)
        if ready and hhmm and hhmm >= str(DEEP_V_CHASE_HHMM):
            out["deep_v_chase"] = True
    if prev_deep:
        out["deep_v_lag1"] = True
    return out


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row.get("payload")
    return p if isinstance(p, dict) else {}


def _excess(row: dict[str, Any]) -> float | None:
    """Prefer bench-excess day3 when present, else raw outcome_day3_pct."""
    p = _payload(row)
    for key in ("bench_excess_d3", "excess_d3", "d3_excess"):
        v = num(p.get(key) if key in p else row.get(key))
        if v is not None:
            return float(v)
    raw = num(row.get("outcome_day3_pct"))
    return float(raw) if raw is not None else None


def _group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [v for v in (_excess(r) for r in rows) if v is not None]
    days = {str(r.get("trade_date") or "")[:10] for r in rows if r.get("trade_date")}
    mean = round(sum(vals) / len(vals), 2) if vals else None
    win = round(100.0 * sum(1 for v in vals if v > 0) / len(vals), 1) if vals else None
    return {"n": len(vals), "days": len(days), "d3": mean, "win": win}


def build_deep_v_shadow(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Summarize A (chase) vs B (lag1 wait) for the review page."""
    from market_desk.config import DEEP_V_SHADOW_SINCE

    since = str(DEEP_V_SHADOW_SINCE)[:10]
    buys = [
        r
        for r in (rows or [])
        if str(r.get("signal_type") or "").startswith("buy")
        and str(r.get("trade_date") or "")[:10] >= since
    ]
    a = [r for r in buys if _payload(r).get("deep_v_chase")]
    b = [r for r in buys if _payload(r).get("deep_v_lag1")]
    sa, sb = _group_stats(a), _group_stats(b)
    delta = None
    if sa["d3"] is not None and sb["d3"] is not None:
        delta = round(float(sa["d3"]) - float(sb["d3"]), 2)
    note = (
        f"样本外自 {since}；A=深V日 14:00 后亮灯，B=深V次日亮灯；"
        "只记账，过门槛才软提示「未确认止跌」。"
    )
    return {
        "ok": True,
        "since": since,
        "a": sa,
        "b": sb,
        "delta": delta,
        "note": note,
        "verdict": (
            f"A−B {delta:+g}%"
            if delta is not None
            else "样本未齐"
        ),
    }
