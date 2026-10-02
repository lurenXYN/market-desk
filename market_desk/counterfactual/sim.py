"""Counterfactual fill: what a buy card would have earned had its gate let it through."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.config import (
    CF_HOLD_DAYS,
    CF_ROUNDTRIP_COST_PCT,
    CF_STOP_FALLBACK_ETF_PCT,
    CF_STOP_FALLBACK_STOCK_PCT,
)


def _f(value: Any) -> float | None:
    """Coerce to a positive float, else None."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def is_one_word_limit_down(open_: float | None, high: float | None, low: float | None,
                           close: float | None, prev_close: float | None) -> bool:
    """Return True for a sealed limit-down bar (no sell liquidity all session).

    Uses daily OHLC only: high == low (one-字 bar) and the close is at least
    4.5% under the previous close (covers 5% ST, 10% main and 20% ChiNext/STAR).
    """
    if None in (high, low, close, prev_close):
        return False
    if abs(float(high) - float(low)) > 1e-6:
        return False
    return (float(close) / float(prev_close) - 1.0) * 100.0 <= -4.5


def is_one_word_limit_up(open_: float | None, high: float | None, low: float | None,
                         close: float | None, prev_close: float | None) -> bool:
    """Return True for a sealed limit-up bar (no buy liquidity all session)."""
    if None in (high, low, close, prev_close):
        return False
    if abs(float(high) - float(low)) > 1e-6:
        return False
    return (float(close) / float(prev_close) - 1.0) * 100.0 >= 4.5


TOUCH_TIERS = ("实测", "盘中", "收盘", "早盘")


def _in_range(px: float | None, low0: float, high0: float | None) -> bool:
    """Return True when ``px`` lies inside day0's traded range (0.1% slack)."""
    if px is None:
        return False
    return px >= low0 * 0.999 and (high0 is None or px <= high0 * 1.001)


def _touch_entry(
    signal: dict[str, Any],
    plan: float,
    low0: float,
    high0: float | None,
    open0: float | None,
    close0: float | None,
) -> tuple[str, float | None]:
    """Decide whether the card really met its plan after it existed, and at what price.

    A daily low under plan is not enough: the low may predate the card. Evidence
    tiers, strongest first:

    * 实测 — live trace saw the price in band (``cf.touch_n``); entry = touch price.
      A traced card that never touched is untouched, whatever the daily low says.
    * 盘中 — a stored refresh had ``near_entry`` / ``touched_plan`` / last ≤ plan.
    * 收盘 — day0 close ≤ plan (so the price sat at plan after the card fired).
    * 早盘 — card fired by 10:00 and the daily low reached plan.

    Returns:
        (tier, entry); entry None = not filled (tier 实测未到 / 未到 / 触达未知).
    """
    payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else {}
    cf = payload.get("cf") if isinstance(payload.get("cf"), dict) else {}
    if cf.get("seen"):
        if not cf.get("touch_n"):
            return "实测未到", None
        px = _f(cf.get("touch_px"))
        return "实测", (px if _in_range(px, low0, high0) else plan)
    last_px = _f(payload.get("last")) or _f(signal.get("last"))
    first_px = _f(payload.get("first_last"))
    if low0 > plan:
        if payload.get("near_entry") and _in_range(last_px, low0, high0):
            return "盘中", last_px
        return "未到", None
    born = str(signal.get("signaled_at") or "")[11:19]
    entry = open0 if (open0 is not None and open0 < plan and born and born <= "09:30:59") else plan
    if (
        payload.get("near_entry")
        or payload.get("touched_plan")
        or (first_px is not None and first_px <= plan)
        or (last_px is not None and last_px <= plan)
    ):
        return "盘中", entry
    if close0 is not None and close0 <= plan:
        return "收盘", entry
    if born and born <= "10:00:00":
        return "早盘", entry
    return "触达未知", None


def _is_etf(code: str) -> bool:
    """Return True for mainland ETF code prefixes."""
    from market_desk.verdict.common import _is_etf_code

    return _is_etf_code(code)


def simulate_cf_trade(
    signal: dict[str, Any],
    packed: tuple[list[str], list[float], dict[str, list[Any]]] | None,
    *,
    hold_days: int = CF_HOLD_DAYS,
    cost_pct: float = CF_ROUNDTRIP_COST_PCT,
    entry_offset_pct: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Simulate one plan-price fill with a stop and a fixed holding window.

    Rules (daily bars only, deliberately conservative):

    * Fill only when the price met the plan after the card existed (see
      ``_touch_entry`` evidence tiers); a sealed limit-up day0 never fills.
    * Entry = traced touch price, else plan (day0 open when the card predates
      a gap-down open).
    * T+1: the stop is checked from day1. Stop = recorded ``stop_price`` when
      below entry, else plan × (1 − 3%) for stocks / (1 − 1.5%) for ETFs.
    * Stop exit = min(open, stop) to charge gap-downs; a sealed limit-down bar
      cannot be sold, so the exit rolls to the next tradable session.
    * Otherwise exit at the close of day ``hold_days``.
    * Round-trip cost (fees + stamp + slippage) is subtracted from every return.

    Args:
        signal: Stored buy signal row (``trade_date``, ``code``, ``price``, ``payload``).
        packed: ``(dates, closes, {"open","high","low"})`` daily klines for the code.
        hold_days: Holding window in settled sessions after day0.
        cost_pct: Round-trip cost in percent.
        entry_offset_pct: Entry-slippage sensitivity mode. When set, the fill is
            re-anchored at plan × (1 + offset%) clamped into day0's range (a
            gap-open under plan still fills at the open) and the stop is anchored
            on plan, so only the price paid changes between offsets.
        now: Clock override for tests.

    Returns:
        None when klines miss day0; otherwise a dict with ``filled``, ``touch``
        (evidence tier) and, when filled and settled, ``r`` (net %), ``r1``
        (day1 close net %), ``stop_hit``, ``held`` (sessions held),
        ``complete`` (full window settled) and ``entry``.
    """
    from market_desk.review.outcome import forward_session_ready

    if not packed:
        return None
    dates, closes, ohlc = packed
    day0 = str(signal.get("trade_date") or "")[:10]
    try:
        i0 = list(dates).index(day0)
    except ValueError:
        return None
    payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else {}
    plan = _f(payload.get("plan_price")) or _f(payload.get("buy_price")) or _f(signal.get("price"))
    opens = list(ohlc.get("open") or [])
    highs = list(ohlc.get("high") or [])
    lows = list(ohlc.get("low") or [])

    def _at(seq: list[Any], i: int) -> float | None:
        return _f(seq[i]) if 0 <= i < len(seq) else None

    low0 = _at(lows, i0)
    if plan is None or low0 is None:
        return None
    prev0 = _f(closes[i0 - 1]) if i0 > 0 else None
    if is_one_word_limit_up(_at(opens, i0), _at(highs, i0), low0, _f(closes[i0]), prev0):
        return {"filled": False, "entry": plan, "touch": "一字涨停"}
    touch, entry = _touch_entry(signal, plan, low0, _at(highs, i0), _at(opens, i0), _f(closes[i0]))
    if entry is None:
        return {"filled": False, "entry": plan, "touch": touch}
    anchor = entry
    if entry_offset_pct is not None:
        anchor = plan
        open0 = _at(opens, i0)
        gap_fill = open0 is not None and open0 < plan and abs(entry - open0) < 1e-9
        if not gap_fill:
            high0 = _at(highs, i0)
            entry = plan * (1.0 + float(entry_offset_pct) / 100.0)
            if high0 is not None:
                entry = min(entry, high0)
            entry = max(entry, low0)
    stop = _f(payload.get("stop_price"))
    if stop is None or stop >= anchor:
        fb = CF_STOP_FALLBACK_ETF_PCT if _is_etf(str(signal.get("code") or "")) else CF_STOP_FALLBACK_STOCK_PCT
        stop = anchor * (1.0 - float(fb) / 100.0)
    fwd = [j for j in range(i0 + 1, len(dates)) if forward_session_ready(dates[j], now=now)]
    if not fwd:
        return {"filled": True, "pending": True, "touch": touch, "entry": round(entry, 4)}
    window = fwd[: max(1, int(hold_days))]
    hold_end = window[-1] if len(window) >= int(hold_days) else None
    exit_px: float | None = None
    stop_hit = False
    held = 0
    pending_stop = False
    for j in fwd:
        held += 1
        o, h, lo, c = _at(opens, j), _at(highs, j), _at(lows, j), _f(closes[j])
        prev = _f(closes[j - 1])
        sealed = is_one_word_limit_down(o, h, lo, c, prev)
        if pending_stop or (lo is not None and lo <= stop):
            if sealed:
                pending_stop = True
                continue
            exit_px = min(o, stop) if (o is not None and not pending_stop) else (o or c)
            stop_hit = True
            break
        if j == hold_end:
            if sealed:
                pending_stop = True
                continue
            exit_px = c
            break
        if hold_end is None and j == fwd[-1]:
            break
    if exit_px is None:
        return {"filled": True, "pending": True, "touch": touch, "entry": round(entry, 4)}
    c1 = _f(closes[fwd[0]])
    r = (exit_px / entry - 1.0) * 100.0 - float(cost_pct)
    r1 = ((c1 / entry - 1.0) * 100.0 - float(cost_pct)) if c1 else None
    return {
        "filled": True,
        "touch": touch,
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "exit": round(exit_px, 4),
        "r": round(r, 3),
        "r1": round(r1, 3) if r1 is not None else None,
        "stop_hit": stop_hit,
        "held": held,
        "complete": stop_hit or len(window) >= int(hold_days),
    }
