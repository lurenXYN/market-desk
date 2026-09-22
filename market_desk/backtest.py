"""Lightweight signal-replay backtest with daily OHLC simulated fills.

Does not write traded/fill onto live signals. Ephemeral results can be
persisted into ``signal_backtest_run`` / ``signal_backtest_fill`` (never via
``signals.payload.sim_*``). Outcomes reuse ``score_signal_with_closes``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from market_desk.filters import normalize_code
from market_desk.numbers import num
from market_desk.review import (
    BUY_HIT_LABELS,
    _flatten_signal_prices,
    build_exec_score,
    classify_fill_execution,
    is_buy_signal,
    is_sell_signal,
    score_signal_with_closes,
)

# Hard cap for one sync run (calendar days inclusive span).
MAX_BACKTEST_SPAN_DAYS = 90
# Async jobs may run longer (still capped).
MAX_BACKTEST_ASYNC_SPAN_DAYS = 180

BACKTEST_DISCLAIMER = (
    "日线 OHLC 回测仍会高估可成交性；已加量能门槛与滑点粗校正，"
    "仍无法等价实盘。不改真实 traded/fill；命中口径与复盘一致（次日红/三日红）。"
)

# Defaults for realism knobs (0 = off for vol filter / slip).
DEFAULT_VOL_MIN_RATIO = 0.4
DEFAULT_SLIP_PCT = 0.15
DEFAULT_GAP_PCT = 1.0
VOL_LOOKBACK = 10


def validate_backtest_range(
    date_from: str, date_to: str, *, max_span: int | None = None
) -> tuple[str, str] | dict[str, Any]:
    """Return ``(d0, d1)`` or an error payload dict with ``ok=False``."""
    d0 = str(date_from or "")[:10]
    d1 = str(date_to or "")[:10]
    cap = int(max_span if max_span is not None else MAX_BACKTEST_SPAN_DAYS)
    if not d0 or not d1 or d0 > d1:
        return {
            "ok": False,
            "detail": "date_from/date_to invalid",
            "items": [],
            "summary": {},
            "disclaimer": BACKTEST_DISCLAIMER,
        }
    try:
        a = datetime.strptime(d0, "%Y-%m-%d")
        b = datetime.strptime(d1, "%Y-%m-%d")
    except ValueError:
        return {
            "ok": False,
            "detail": "dates must be YYYY-MM-DD",
            "items": [],
            "summary": {},
            "disclaimer": BACKTEST_DISCLAIMER,
        }
    span = (b - a).days + 1
    if span > cap:
        return {
            "ok": False,
            "detail": f"单次回测跨度最多 {cap} 天（当前 {span} 天）",
            "max_span_days": cap,
            "items": [],
            "summary": {},
            "disclaimer": BACKTEST_DISCLAIMER,
        }
    return d0, d1


def collect_backtest_signals(
    *,
    date_from: str,
    date_to: str,
    include_sells: bool = True,
    ready_only: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Load and filter paper signals in range (newest first, capped)."""
    from market_desk.db import list_signal_trade_dates, load_signals_for_date

    d0, d1 = date_from, date_to
    dates = [d for d in list_signal_trade_dates(limit=120) if d0 <= d <= d1]
    if not dates:
        cur = datetime.strptime(d0, "%Y-%m-%d")
        end = datetime.strptime(d1, "%Y-%m-%d")
        while cur <= end:
            dates.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)

    rows: list[dict[str, Any]] = []
    for day in dates:
        for row in load_signals_for_date(day):
            rows.append(_flatten_signal_prices(row))
    rows = sorted(
        rows,
        key=lambda r: (
            str(r.get("trade_date") or ""),
            str(r.get("signaled_at") or ""),
            int(r.get("id") or 0),
        ),
        reverse=True,
    )
    picked: list[dict[str, Any]] = []
    for row in rows:
        if is_buy_signal(row.get("signal_type")):
            if ready_only and not int(row.get("ready") or 0):
                continue
            picked.append(row)
        elif include_sells and is_sell_signal(row.get("signal_type")):
            picked.append(row)
        if len(picked) >= max(20, min(400, int(limit or 200))):
            break
    return picked


def _bar_maps(
    dates: list[str],
    closes: list[float],
    ohlc: dict[str, list[float | None]] | None,
) -> dict[str, dict[str, float | None]]:
    """Index daily OHLC(+volume) by YYYY-MM-DD."""
    opens = (ohlc or {}).get("open") or []
    highs = (ohlc or {}).get("high") or []
    lows = (ohlc or {}).get("low") or []
    vols = (ohlc or {}).get("volume") or []
    out: dict[str, dict[str, float | None]] = {}
    for i, d in enumerate(dates):
        day = str(d or "")[:10]
        if not day:
            continue
        out[day] = {
            "open": opens[i] if i < len(opens) else None,
            "high": highs[i] if i < len(highs) else None,
            "low": lows[i] if i < len(lows) else None,
            "close": float(closes[i]) if i < len(closes) else None,
            "volume": vols[i] if i < len(vols) else None,
        }
    return out


def _median(vals: list[float]) -> float | None:
    """Return median of a non-empty float list."""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (float(s[mid - 1]) + float(s[mid])) / 2.0


def _prior_volume_median(
    bars_by_day: dict[str, dict[str, float | None]],
    day: str,
    *,
    lookback: int = VOL_LOOKBACK,
) -> float | None:
    """Median volume of up to ``lookback`` sessions strictly before ``day``."""
    prior = sorted(d for d in bars_by_day if d < day)[-max(1, int(lookback)) :]
    vols: list[float] = []
    for d in prior:
        v = num((bars_by_day.get(d) or {}).get("volume"))
        if v is not None and v > 0:
            vols.append(float(v))
    return _median(vols)


def _liquidity_ok(
    bars_by_day: dict[str, dict[str, float | None]],
    day: str,
    *,
    vol_min_ratio: float,
) -> tuple[bool, str]:
    """Return whether the session has enough volume vs recent median.

    Missing volume data soft-passes so older bars without vol still fill.
    """
    ratio = float(vol_min_ratio or 0)
    if ratio <= 0:
        return True, ""
    bar = bars_by_day.get(day) or {}
    vol = num(bar.get("volume"))
    if vol is None or vol <= 0:
        return True, ""
    med = _prior_volume_median(bars_by_day, day)
    if med is None or med <= 0:
        return True, ""
    if float(vol) < float(med) * ratio:
        return False, f"量能不足({float(vol):.0f}<{ratio:.0%}×中位{med:.0f})"
    return True, ""


def _apply_buy_slip(fill: float, slip_pct: float) -> float:
    """Worsen a buy fill by ``slip_pct`` percent (e.g. 0.15 → +0.15%)."""
    s = max(0.0, float(slip_pct or 0))
    if s <= 0 or fill <= 0:
        return float(fill)
    return float(fill) * (1.0 + s / 100.0)


def _apply_sell_slip(fill: float, slip_pct: float) -> float:
    """Worsen a sell fill by ``slip_pct`` percent (lower exit)."""
    s = max(0.0, float(slip_pct or 0))
    if s <= 0 or fill <= 0:
        return float(fill)
    return float(fill) * (1.0 - s / 100.0)


def _clamp_fill(fill: float, lo: float | None, hi: float | None) -> float:
    """Keep fill inside the day's traded range when bounds exist."""
    px = float(fill)
    if lo is not None and lo > 0 and px < lo:
        px = float(lo)
    if hi is not None and hi > 0 and px > hi:
        px = float(hi)
    return px


def _plan_buy_prices(row: dict[str, Any]) -> dict[str, float | None]:
    """Resolve wait / plan / chase / stop for a buy signal row.

    ``plan`` prefers locked ``plan_price`` (survives demote-to-wait); falls back
    to ``signals.price``. ``wait`` stays the ideal pullback rung.
    """
    flat = _flatten_signal_prices(row)
    payload = flat.get("payload") if isinstance(flat.get("payload"), dict) else {}
    wait = num(flat.get("wait_price") if flat.get("wait_price") is not None else payload.get("wait_price"))
    plan = num(
        flat.get("plan_price")
        if flat.get("plan_price") is not None
        else payload.get("plan_price")
    )
    if plan is None:
        plan = num(flat.get("price"))
    chase = num(
        flat.get("chase_price") if flat.get("chase_price") is not None else payload.get("chase_price")
    )
    stop = num(flat.get("stop_price") if flat.get("stop_price") is not None else payload.get("stop_price"))
    if wait is None:
        wait = plan
    return {"wait": wait, "plan": plan, "chase": chase, "stop": stop}


def _plan_sell_prices(row: dict[str, Any]) -> dict[str, float | None]:
    """Resolve sell / stop for a sell signal row."""
    flat = _flatten_signal_prices(row)
    payload = flat.get("payload") if isinstance(flat.get("payload"), dict) else {}
    sell = num(flat.get("price"))
    stop = num(flat.get("stop_price") if flat.get("stop_price") is not None else payload.get("stop_price"))
    return {"sell": sell, "stop": stop}


def simulate_buy_fill(
    *,
    trade_date: str,
    bars_by_day: dict[str, dict[str, float | None]],
    wait: float | None,
    plan: float | None,
    chase: float | None,
    mode: str = "wait",
    look_ahead: int = 2,
    vol_min_ratio: float = DEFAULT_VOL_MIN_RATIO,
    slip_pct: float = DEFAULT_SLIP_PCT,
    gap_pct: float = DEFAULT_GAP_PCT,
    minutes: list[dict[str, Any]] | None = None,
    fidelity: str = "daily",
) -> dict[str, Any] | None:
    """Simulate a buy fill on signal day or the next ``look_ahead`` sessions.

    Modes:
      - ``wait``: fill at wait when low ≤ wait (skip day if open ≥ chase).
      - ``plan``: fill at plan price when low ≤ plan.
      - ``mid``: fill at mid(wait, chase) when low reaches that level.

    Realism knobs:
      - ``vol_min_ratio``: require day volume ≥ ratio × prior median (0=off).
      - ``slip_pct``: worsen fill price by this percent.
      - ``gap_pct``: if open gaps down ≥ this % vs prior close and open ≤ target,
        fill at open (gap-open path).
      - ``fidelity=minute``: when ``minutes`` provided for day0, resolve
        chase-before-plan order on the signal day (high-fidelity path).
    """
    day0 = str(trade_date or "")[:10]
    if not day0 or not bars_by_day:
        return None
    mode_s = str(mode or "wait").strip().lower()
    if mode_s not in ("wait", "plan", "mid"):
        mode_s = "wait"
    target: float | None
    if mode_s == "plan":
        target = plan if plan is not None else wait
    elif mode_s == "mid":
        if wait is not None and chase is not None and chase > wait:
            target = round((wait + chase) / 2.0, 4)
        else:
            target = wait if wait is not None else plan
    else:
        target = wait if wait is not None else plan
    if target is None or target <= 0:
        return None

    fid = str(fidelity or "daily").strip().lower()
    if fid == "minute" and minutes:
        minute_hit = _minute_buy_fill(
            minutes,
            target=float(target),
            chase=float(chase) if chase is not None else None,
            slip_pct=slip_pct,
        )
        if minute_hit is not None and minute_hit.get("filled"):
            minute_hit["mode"] = mode_s
            minute_hit["fill_date"] = day0
            minute_hit["fidelity"] = "minute"
            return minute_hit
        # No minute fill (no touch or chase-first): skip day0, try later sessions.
        days = sorted(d for d in bars_by_day if d > day0)[: max(1, int(look_ahead))]
    else:
        days = sorted(d for d in bars_by_day if d >= day0)[: max(1, int(look_ahead) + 1)]

    liq_skips = 0
    last_liq_note = ""
    for day in days:
        bar = bars_by_day.get(day) or {}
        o = num(bar.get("open"))
        lo = num(bar.get("low"))
        hi = num(bar.get("high"))
        if lo is None or lo <= 0:
            continue
        # Ambiguous day0 (both chase and target in range): conservative skip when
        # fidelity asks for minute but minutes missing.
        if (
            fid == "minute"
            and day == day0
            and not minutes
            and chase is not None
            and hi is not None
            and float(hi) >= float(chase)
            and float(lo) <= float(target)
        ):
            continue
        # Gap through the chase ceiling → treat as unfilled that session.
        if chase is not None and o is not None and o >= chase and day == day0:
            continue
        if lo > target:
            continue

        ok_liq, liq_note = _liquidity_ok(
            bars_by_day, day, vol_min_ratio=vol_min_ratio
        )
        if not ok_liq:
            liq_skips += 1
            last_liq_note = liq_note
            continue

        # Base fill: target, or open if opened through the band.
        fill = float(target)
        note = "触达价带"
        gap_filled = False
        prev_days = sorted(d for d in bars_by_day if d < day)
        prev_close = None
        if prev_days:
            prev_close = num((bars_by_day.get(prev_days[-1]) or {}).get("close"))
        gap_th = max(0.0, float(gap_pct or 0))
        if (
            gap_th > 0
            and o is not None
            and o > 0
            and prev_close is not None
            and prev_close > 0
            and o <= target
        ):
            gap_down = (float(prev_close) - float(o)) / float(prev_close) * 100.0
            if gap_down >= gap_th:
                fill = float(o)
                note = f"缺口低开成交({gap_down:.1f}%)"
                gap_filled = True
        if not gap_filled and o is not None and o < fill and lo <= o:
            fill = float(o)
            note = "开盘砸穿成交"

        fill = _apply_buy_slip(fill, slip_pct)
        fill = _clamp_fill(fill, lo, hi)
        if slip_pct and float(slip_pct) > 0:
            note = f"{note}·滑点+{float(slip_pct):.2f}%"
        return {
            "filled": True,
            "fill_price": round(fill, 4),
            "fill_date": day,
            "target": target,
            "mode": mode_s,
            "note": note,
            "liq_skips": liq_skips,
            "gap_filled": gap_filled,
            "slip_pct": float(slip_pct or 0),
            "fidelity": "daily",
        }
    if liq_skips and last_liq_note:
        return {
            "filled": False,
            "fill_price": None,
            "fill_date": None,
            "target": target,
            "mode": mode_s,
            "note": f"未触达·{last_liq_note}",
            "liq_skips": liq_skips,
        }
    return {
        "filled": False,
        "fill_price": None,
        "fill_date": None,
        "target": target,
        "mode": mode_s,
        "note": "未触达",
        "liq_skips": liq_skips,
    }


def _minute_buy_fill(
    minutes: list[dict[str, Any]],
    *,
    target: float,
    chase: float | None,
    slip_pct: float,
) -> dict[str, Any] | None:
    """Walk intraday minutes: fill at first touch of target unless chase hit first."""
    seen_chase = False
    for pt in minutes or []:
        px = num(pt.get("price") or pt.get("close"))
        if px is None or px <= 0:
            continue
        hi = num(pt.get("high"))
        lo = num(pt.get("low"))
        # trends2 points are usually close-only; treat price as both.
        top = float(hi) if hi is not None and hi > 0 else float(px)
        bot = float(lo) if lo is not None and lo > 0 else float(px)
        if chase is not None and top >= float(chase):
            seen_chase = True
        if bot <= float(target):
            if seen_chase and chase is not None and float(target) < float(chase):
                return {
                    "filled": False,
                    "fill_price": None,
                    "fill_date": None,
                    "target": target,
                    "note": "分时先触不追上限·当日跳过",
                    "liq_skips": 0,
                    "fidelity": "minute",
                }
            fill = _apply_buy_slip(float(target), slip_pct)
            fill = _clamp_fill(fill, bot, top)
            note = "分时触达价带"
            if slip_pct and float(slip_pct) > 0:
                note = f"{note}·滑点+{float(slip_pct):.2f}%"
            return {
                "filled": True,
                "fill_price": round(fill, 4),
                "target": target,
                "note": note,
                "liq_skips": 0,
                "gap_filled": False,
                "slip_pct": float(slip_pct or 0),
                "fidelity": "minute",
            }
    return None


def simulate_sell_fill(
    *,
    trade_date: str,
    bars_by_day: dict[str, dict[str, float | None]],
    sell: float | None,
    stop: float | None,
    look_ahead: int = 3,
    slip_pct: float = DEFAULT_SLIP_PCT,
    gap_pct: float = DEFAULT_GAP_PCT,
) -> dict[str, Any] | None:
    """Simulate a sell: take-profit if high ≥ sell, else stop if low ≤ stop.

    Gap-down open through stop fills at open. Slippage worsens exits.
    """
    day0 = str(trade_date or "")[:10]
    if not day0 or not bars_by_day:
        return None
    days = sorted(d for d in bars_by_day if d >= day0)[: max(1, int(look_ahead) + 1)]
    for day in days:
        bar = bars_by_day.get(day) or {}
        o = num(bar.get("open"))
        lo = num(bar.get("low"))
        hi = num(bar.get("high"))
        prev_days = sorted(d for d in bars_by_day if d < day)
        prev_close = None
        if prev_days:
            prev_close = num((bars_by_day.get(prev_days[-1]) or {}).get("close"))
        gap_th = max(0.0, float(gap_pct or 0))

        # Gap-down through stop at open (worse path).
        if (
            stop is not None
            and gap_th > 0
            and o is not None
            and o > 0
            and prev_close is not None
            and prev_close > 0
            and o <= stop
        ):
            gap_down = (float(prev_close) - float(o)) / float(prev_close) * 100.0
            if gap_down >= gap_th:
                fill = _apply_sell_slip(float(o), slip_pct)
                fill = _clamp_fill(fill, lo, hi)
                note = f"缺口低开触止损({gap_down:.1f}%)"
                if slip_pct and float(slip_pct) > 0:
                    note = f"{note}·滑点-{float(slip_pct):.2f}%"
                return {
                    "filled": True,
                    "fill_price": round(fill, 4),
                    "fill_date": day,
                    "exit_mode": "stop",
                    "note": note,
                    "gap_filled": True,
                    "slip_pct": float(slip_pct or 0),
                }

        # Stop first when both fire the same day (worse case for the plan).
        if stop is not None and lo is not None and lo <= stop:
            fill = float(stop)
            if o is not None and o < stop:
                fill = float(o)
            fill = _apply_sell_slip(fill, slip_pct)
            fill = _clamp_fill(fill, lo, hi)
            note = "触止损"
            if slip_pct and float(slip_pct) > 0:
                note = f"{note}·滑点-{float(slip_pct):.2f}%"
            return {
                "filled": True,
                "fill_price": round(fill, 4),
                "fill_date": day,
                "exit_mode": "stop",
                "note": note,
                "gap_filled": False,
                "slip_pct": float(slip_pct or 0),
            }
        if sell is not None and hi is not None and hi >= sell:
            fill = float(sell)
            if o is not None and o > sell:
                fill = float(o)
            fill = _apply_sell_slip(fill, slip_pct)
            fill = _clamp_fill(fill, lo, hi)
            note = "触卖价"
            if slip_pct and float(slip_pct) > 0:
                note = f"{note}·滑点-{float(slip_pct):.2f}%"
            return {
                "filled": True,
                "fill_price": round(fill, 4),
                "fill_date": day,
                "exit_mode": "take",
                "note": note,
                "gap_filled": False,
                "slip_pct": float(slip_pct or 0),
            }
    return {
        "filled": False,
        "fill_price": None,
        "fill_date": None,
        "exit_mode": None,
        "note": "未触达",
    }


def _score_after_fill(
    row: dict[str, Any],
    *,
    fill_price: float,
    fill_date: str,
    dates: list[str],
    closes: list[float],
    ohlc: dict[str, list[float | None]],
) -> dict[str, Any] | None:
    """Score outcome as if the signal traded at ``fill_price`` on ``fill_date``."""
    pseudo = dict(row)
    pseudo["fill_price"] = float(fill_price)
    pseudo["price"] = float(fill_price)
    pseudo["trade_date"] = str(fill_date)[:10]
    return score_signal_with_closes(
        pseudo,
        closes,
        dates,
        opens=ohlc.get("open"),
        lows=ohlc.get("low"),
        highs=ohlc.get("high"),
    )


def _sim_exec_kind(
    *,
    fill_price: float,
    wait: float | None,
    plan: float | None,
    chase: float | None,
) -> str | None:
    """Classify a simulated buy fill vs wait/plan/chase (never writes signals)."""
    pseudo = {
        "signal_type": "buy",
        "traded": 1,
        "fill_price": fill_price,
        "price": plan if plan is not None else wait,
        "plan_price": plan,
        "wait_price": wait,
        "chase_price": chase,
        "payload": {
            "plan_price": plan,
            "wait_price": wait,
            "chase_price": chase,
        },
    }
    return classify_fill_execution(pseudo)


def _summarize_backtest(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate hit rates, average day1, and sim exec score for filled buys."""
    buys = [x for x in items if x.get("side") == "buy"]
    sells = [x for x in items if x.get("side") == "sell"]
    buy_filled = [x for x in buys if x.get("sim_filled")]
    sell_filled = [x for x in sells if x.get("sim_filled")]
    buy_scored = [x for x in buy_filled if x.get("outcome_label")]
    sell_scored = [x for x in sell_filled if x.get("outcome_label")]
    buy_hits = [x for x in buy_scored if x.get("outcome_label") in BUY_HIT_LABELS]
    sell_hits = [x for x in sell_scored if x.get("outcome_label") == "卖后回落"]

    def _avg(rows: list[dict[str, Any]], key: str) -> float | None:
        vals = [float(r[key]) for r in rows if r.get(key) is not None]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    def _rate(hits: list, n: list) -> float | None:
        if not n:
            return None
        return round(100.0 * len(hits) / len(n), 1)

    # Build pseudo traded rows for build_exec_score (sim-only; not real traded).
    sim_exec_rows = []
    for x in buy_filled:
        if x.get("sim_fill_price") is None:
            continue
        sim_exec_rows.append(
            {
                "signal_type": "buy",
                "traded": 1,
                "kind": x.get("kind") or "stock",
                "fill_price": x.get("sim_fill_price"),
                "price": x.get("plan_price") or x.get("wait_price"),
                "plan_price": x.get("plan_price"),
                "wait_price": x.get("wait_price"),
                "chase_price": x.get("chase_price"),
                "payload": {
                    "plan_price": x.get("plan_price"),
                    "wait_price": x.get("wait_price"),
                    "chase_price": x.get("chase_price"),
                },
                "exec_source": "sim",
                "sim_exec": x.get("sim_exec"),
            }
        )
    sim_exec = build_exec_score(sim_exec_rows) if sim_exec_rows else {
        "score": None,
        "scored_n": 0,
        "in_band_n": 0,
        "chase_n": 0,
        "below_n": 0,
        "other_n": 0,
    }

    return {
        "buy_n": len(buys),
        "buy_filled_n": len(buy_filled),
        "buy_fill_rate": _rate(buy_filled, buys),
        "buy_scored_n": len(buy_scored),
        "buy_hit_n": len(buy_hits),
        "buy_hit_rate": _rate(buy_hits, buy_scored),
        "buy_avg_day1": _avg(buy_scored, "outcome_day1_pct"),
        "sell_n": len(sells),
        "sell_filled_n": len(sell_filled),
        "sell_fill_rate": _rate(sell_filled, sells),
        "sell_scored_n": len(sell_scored),
        "sell_hit_n": len(sell_hits),
        "sell_hit_rate": _rate(sell_hits, sell_scored),
        "sell_avg_day1": _avg(sell_scored, "outcome_day1_pct"),
        "unfilled_n": sum(1 for x in items if not x.get("sim_filled")),
        "liq_skip_n": sum(1 for x in items if int(x.get("liq_skips") or 0) > 0),
        "gap_fill_n": sum(1 for x in items if x.get("gap_filled")),
        "sim_exec": sim_exec,
    }


async def run_signal_backtest(
    *,
    date_from: str,
    date_to: str,
    mode: str = "wait",
    include_sells: bool = True,
    ready_only: bool = False,
    limit: int = 200,
    dry_run: bool = False,
    vol_min_ratio: float | None = None,
    slip_pct: float | None = None,
    gap_pct: float | None = None,
    fidelity: str = "daily",
    max_span: int | None = None,
    client: Any | None = None,
    progress_cb: Any | None = None,
) -> dict[str, Any]:
    """Replay paper signals in ``[date_from, date_to]`` with OHLC simulated fills.

    Read-only: never mutates ``signals`` / user meta.
    When ``dry_run`` is True, only count matching signals (no kline fetch).
    ``fidelity=minute`` uses today's minute series for day0 chase-order when available.
    ``progress_cb(done, total)`` optional for async jobs.
    """
    import httpx

    from market_desk.eastmoney import (
        fetch_daily_klines_many,
        fetch_minute_bars_many_days,
        fetch_minute_trends_many,
    )

    span_cap = int(max_span if max_span is not None else MAX_BACKTEST_SPAN_DAYS)
    checked = validate_backtest_range(date_from, date_to, max_span=span_cap)
    if isinstance(checked, dict):
        return checked
    d0, d1 = checked
    fid = str(fidelity or "daily").strip().lower()
    if fid not in ("daily", "minute"):
        fid = "daily"

    vol_r = (
        DEFAULT_VOL_MIN_RATIO
        if vol_min_ratio is None
        else max(0.0, min(2.0, float(vol_min_ratio)))
    )
    slip = (
        DEFAULT_SLIP_PCT if slip_pct is None else max(0.0, min(3.0, float(slip_pct)))
    )
    gap = DEFAULT_GAP_PCT if gap_pct is None else max(0.0, min(10.0, float(gap_pct)))

    picked = collect_backtest_signals(
        date_from=d0,
        date_to=d1,
        include_sells=include_sells,
        ready_only=ready_only,
        limit=limit,
    )
    buy_n = sum(1 for r in picked if is_buy_signal(r.get("signal_type")))
    sell_n = sum(1 for r in picked if is_sell_signal(r.get("signal_type")))

    realism = {
        "vol_min_ratio": vol_r,
        "slip_pct": slip,
        "gap_pct": gap,
        "fidelity": fid,
    }

    if dry_run:
        preview_items = [
            {
                "id": r.get("id"),
                "side": "buy" if is_buy_signal(r.get("signal_type")) else "sell",
                "code": normalize_code(r.get("code")),
                "name": r.get("name"),
                "trade_date": str(r.get("trade_date") or "")[:10],
                "signal_type": r.get("signal_type"),
                "ready": int(r.get("ready") or 0),
            }
            for r in picked[:80]
        ]
        return {
            "ok": True,
            "dry_run": True,
            "date_from": d0,
            "date_to": d1,
            "mode": str(mode or "wait"),
            "ready_only": bool(ready_only),
            "include_sells": bool(include_sells),
            "max_span_days": span_cap,
            "matched_n": len(picked),
            "buy_n": buy_n,
            "sell_n": sell_n,
            "n": len(picked),
            "realism": realism,
            "summary": {
                "matched_n": len(picked),
                "buy_n": buy_n,
                "sell_n": sell_n,
            },
            "items": preview_items,
            "disclaimer": BACKTEST_DISCLAIMER,
            "note": BACKTEST_DISCLAIMER + " 本结果为预览（未拉日线、未撮合）。",
        }

    codes = [normalize_code(r.get("code")) for r in picked]
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        klines = await fetch_daily_klines_many(client, codes, limit=80, concurrency=6)
        minutes_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
        if fid == "minute":
            pairs = [
                (normalize_code(r.get("code")), str(r.get("trade_date") or "")[:10])
                for r in picked
                if is_buy_signal(r.get("signal_type"))
            ]
            pairs = [(c, d) for c, d in pairs if c and len(d) >= 10]
            # Cap minute pulls to avoid stampeding East Money (unique pairs).
            pairs = list(dict.fromkeys(pairs))[:60]
            if pairs:
                try:
                    minutes_by_key = await fetch_minute_bars_many_days(
                        client, pairs, concurrency=3
                    )
                except Exception:
                    minutes_by_key = {}
                # Soft fallback: today's trends2 when 1-min kline empty.
                today = datetime.now().strftime("%Y-%m-%d")
                need_today = [
                    c for c, d in pairs if d == today and not minutes_by_key.get((c, d))
                ]
                if need_today:
                    try:
                        today_mins = await fetch_minute_trends_many(
                            client, need_today, concurrency=3
                        )
                        for c, rows in (today_mins or {}).items():
                            if rows:
                                minutes_by_key[(c, today)] = rows
                    except Exception:
                        pass
    finally:
        if own_client:
            await client.aclose()

    items: list[dict[str, Any]] = []
    total = max(1, len(picked))
    for idx, row in enumerate(picked):
        if progress_cb:
            try:
                progress_cb(idx, total)
            except Exception:
                pass
        code = normalize_code(row.get("code"))
        triple = klines.get(code)
        if not triple:
            items.append(
                {
                    "id": row.get("id"),
                    "side": "buy" if is_buy_signal(row.get("signal_type")) else "sell",
                    "code": code,
                    "name": row.get("name"),
                    "trade_date": str(row.get("trade_date") or "")[:10],
                    "signal_type": row.get("signal_type"),
                    "sim_filled": False,
                    "note": "无日线",
                }
            )
            continue
        dates_k, closes, ohlc = triple
        bars = _bar_maps(dates_k, closes, ohlc)
        day = str(row.get("trade_date") or "")[:10]
        if is_buy_signal(row.get("signal_type")):
            band = _plan_buy_prices(row)
            mins = minutes_by_key.get((code, day)) if fid == "minute" else None
            sim = simulate_buy_fill(
                trade_date=day,
                bars_by_day=bars,
                wait=band["wait"],
                plan=band["plan"],
                chase=band["chase"],
                mode=mode,
                vol_min_ratio=vol_r,
                slip_pct=slip,
                gap_pct=gap,
                minutes=mins,
                fidelity=fid,
            ) or {"filled": False, "note": "无结果"}
            item: dict[str, Any] = {
                "id": row.get("id"),
                "side": "buy",
                "code": code,
                "name": row.get("name"),
                "kind": row.get("kind") or "stock",
                "trade_date": day,
                "signal_type": row.get("signal_type"),
                "desk_source": row.get("desk_source"),
                "plan_price": band["plan"],
                "wait_price": band["wait"],
                "chase_price": band["chase"],
                "sim_filled": bool(sim.get("filled")),
                "sim_fill_price": sim.get("fill_price"),
                "sim_fill_date": sim.get("fill_date"),
                "sim_mode": sim.get("mode") or mode,
                "note": sim.get("note"),
                "liq_skips": int(sim.get("liq_skips") or 0),
                "gap_filled": bool(sim.get("gap_filled")),
                "fidelity": sim.get("fidelity") or fid,
            }
            if sim.get("filled") and sim.get("fill_price") is not None:
                item["sim_exec"] = _sim_exec_kind(
                    fill_price=float(sim["fill_price"]),
                    wait=band["wait"],
                    plan=band["plan"],
                    chase=band["chase"],
                )
            if sim.get("filled") and sim.get("fill_price") and sim.get("fill_date"):
                outcome = _score_after_fill(
                    row,
                    fill_price=float(sim["fill_price"]),
                    fill_date=str(sim["fill_date"]),
                    dates=dates_k,
                    closes=closes,
                    ohlc=ohlc,
                )
                if outcome:
                    item.update(outcome)
            items.append(item)
        else:
            band = _plan_sell_prices(row)
            sim = simulate_sell_fill(
                trade_date=day,
                bars_by_day=bars,
                sell=band["sell"],
                stop=band["stop"],
                slip_pct=slip,
                gap_pct=gap,
            ) or {"filled": False, "note": "无结果"}
            item = {
                "id": row.get("id"),
                "side": "sell",
                "code": code,
                "name": row.get("name"),
                "kind": row.get("kind") or "stock",
                "trade_date": day,
                "signal_type": "sell",
                "plan_price": band["sell"],
                "stop_price": band["stop"],
                "sim_filled": bool(sim.get("filled")),
                "sim_fill_price": sim.get("fill_price"),
                "sim_fill_date": sim.get("fill_date"),
                "sim_exit_mode": sim.get("exit_mode"),
                "note": sim.get("note"),
                "gap_filled": bool(sim.get("gap_filled")),
            }
            if sim.get("filled") and sim.get("fill_price") and sim.get("fill_date"):
                outcome = _score_after_fill(
                    row,
                    fill_price=float(sim["fill_price"]),
                    fill_date=str(sim["fill_date"]),
                    dates=dates_k,
                    closes=closes,
                    ohlc=ohlc,
                )
                if outcome:
                    item.update(outcome)
            items.append(item)

    if progress_cb:
        try:
            progress_cb(total, total)
        except Exception:
            pass
    summary = _summarize_backtest(items)
    min_n = sum(1 for x in items if x.get("fidelity") == "minute")
    fid_note = ""
    if fid == "minute":
        fid_note = f" · 分时保真（分钟命中 {min_n} 笔）"
    return {
        "ok": True,
        "dry_run": False,
        "date_from": d0,
        "date_to": d1,
        "mode": str(mode or "wait"),
        "ready_only": bool(ready_only),
        "include_sells": bool(include_sells),
        "max_span_days": span_cap,
        "realism": realism,
        "n": len(items),
        "summary": summary,
        "items": items,
        "disclaimer": BACKTEST_DISCLAIMER,
        "note": (
            BACKTEST_DISCLAIMER
            + f" 量能≥{vol_r:.0%}中位 · 滑点 {slip:.2f}% · 跳空阈值 {gap:.1f}%。"
            + fid_note
        ),
    }


def compare_sim_vs_filled(
    items: list[dict[str, Any]] | None,
    *,
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    """Side-by-side paper sim fills vs user traded+fill_price outcomes.

    Matches buy rows by ``code`` + ``trade_date``. Does not mutate signals.
    """
    from market_desk.db import load_signals_for_date
    from market_desk.review import BUY_HIT_LABELS, is_buy_signal

    src = [dict(x) for x in (items or []) if str(x.get("side") or "") == "buy"]
    if not src:
        return {
            "ok": True,
            "paired_n": 0,
            "pairs": [],
            "summary": {"paired_n": 0},
            "note": "无买侧模拟行可对照",
        }
    d0 = str(date_from or "")[:10]
    d1 = str(date_to or "")[:10]
    days = sorted(
        {
            str(x.get("trade_date") or "")[:10]
            for x in src
            if len(str(x.get("trade_date") or "")) >= 10
        }
    )
    if d0 and d1:
        days = [d for d in days if d0 <= d <= d1]
    traded_map: dict[tuple[str, str], dict[str, Any]] = {}
    for day in days:
        for row in load_signals_for_date(day):
            if not is_buy_signal(row.get("signal_type")):
                continue
            if not int(row.get("traded") or 0):
                continue
            code = normalize_code(row.get("code"))
            if not code:
                continue
            traded_map[(code, day)] = row

    pairs: list[dict[str, Any]] = []
    both_hit = both_miss = sim_only_hit = fill_only_hit = 0
    price_gaps: list[float] = []
    for it in src:
        code = normalize_code(it.get("code"))
        day = str(it.get("trade_date") or "")[:10]
        real = traded_map.get((code, day)) if code else None
        if not real:
            continue
        sim_label = str(it.get("outcome_label") or "")
        real_label = str(real.get("outcome_label") or "")
        sim_hit = sim_label in BUY_HIT_LABELS
        real_hit = real_label in BUY_HIT_LABELS
        if sim_hit and real_hit:
            both_hit += 1
        elif (not sim_hit) and (not real_hit) and sim_label and real_label:
            both_miss += 1
        elif sim_hit and not real_hit:
            sim_only_hit += 1
        elif real_hit and not sim_hit:
            fill_only_hit += 1
        sim_px = it.get("sim_fill_price")
        fill_px = real.get("fill_price")
        gap_pct = None
        try:
            if sim_px is not None and fill_px is not None and float(fill_px) > 0:
                gap_pct = round(
                    (float(sim_px) - float(fill_px)) / float(fill_px) * 100.0, 2
                )
                price_gaps.append(gap_pct)
        except (TypeError, ValueError):
            gap_pct = None
        pairs.append(
            {
                "code": code,
                "name": it.get("name") or real.get("name"),
                "trade_date": day,
                "sim_filled": bool(it.get("sim_filled")),
                "sim_fill_price": sim_px,
                "sim_label": sim_label or None,
                "sim_day1": it.get("outcome_day1_pct"),
                "fill_price": fill_px,
                "fill_label": real_label or None,
                "fill_day1": real.get("outcome_day1_pct"),
                "price_gap_pct": gap_pct,
                "agree_hit": bool(sim_hit == real_hit and (sim_label or real_label)),
            }
        )
    avg_gap = round(sum(price_gaps) / len(price_gaps), 2) if price_gaps else None
    return {
        "ok": True,
        "paired_n": len(pairs),
        "pairs": pairs[:120],
        "summary": {
            "paired_n": len(pairs),
            "both_hit": both_hit,
            "both_miss": both_miss,
            "sim_only_hit": sim_only_hit,
            "fill_only_hit": fill_only_hit,
            "avg_price_gap_pct": avg_gap,
        },
        "note": "同码同日：模拟成交 vs 已交易成交价；命中=次日红/三日红",
    }
