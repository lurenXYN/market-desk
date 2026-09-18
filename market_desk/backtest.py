"""Lightweight signal-replay backtest with daily OHLC simulated fills.

Does not write traded/fill onto live signals — results are ephemeral for the
review-style 「回测」 tab. Outcomes reuse ``score_signal_with_closes``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from market_desk.filters import normalize_code
from market_desk.numbers import num
from market_desk.review import (
    BUY_HIT_LABELS,
    _flatten_signal_prices,
    is_buy_signal,
    is_sell_signal,
    score_signal_with_closes,
)

# Hard cap for one sync run (calendar days inclusive span).
MAX_BACKTEST_SPAN_DAYS = 90

BACKTEST_DISCLAIMER = (
    "日线 OHLC 回测会高估可成交性（事后高低点/瞬时穿刺无法在实盘保证成交）；"
    "结果仅供策略粗筛，不可等价实盘收益。不改真实 traded/fill；"
    "命中口径与复盘一致（次日红/三日红）。"
)


def validate_backtest_range(date_from: str, date_to: str) -> tuple[str, str] | dict[str, Any]:
    """Return ``(d0, d1)`` or an error payload dict with ``ok=False``."""
    d0 = str(date_from or "")[:10]
    d1 = str(date_to or "")[:10]
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
    if span > int(MAX_BACKTEST_SPAN_DAYS):
        return {
            "ok": False,
            "detail": f"单次回测跨度最多 {MAX_BACKTEST_SPAN_DAYS} 天（当前 {span} 天）",
            "max_span_days": MAX_BACKTEST_SPAN_DAYS,
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
    """Index daily OHLC by YYYY-MM-DD."""
    opens = (ohlc or {}).get("open") or []
    highs = (ohlc or {}).get("high") or []
    lows = (ohlc or {}).get("low") or []
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
        }
    return out


def _plan_buy_prices(row: dict[str, Any]) -> dict[str, float | None]:
    """Resolve wait / plan / chase / stop for a buy signal row."""
    flat = _flatten_signal_prices(row)
    payload = flat.get("payload") if isinstance(flat.get("payload"), dict) else {}
    wait = num(flat.get("wait_price") if flat.get("wait_price") is not None else payload.get("wait_price"))
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
) -> dict[str, Any] | None:
    """Simulate a buy fill on signal day or the next ``look_ahead`` sessions.

    Modes:
      - ``wait``: fill at wait when low ≤ wait (skip day if open ≥ chase).
      - ``plan``: fill at plan price when low ≤ plan.
      - ``mid``: fill at mid(wait, chase) when low reaches that level.
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

    days = sorted(d for d in bars_by_day if d >= day0)[: max(1, int(look_ahead) + 1)]
    for day in days:
        bar = bars_by_day.get(day) or {}
        o = num(bar.get("open"))
        lo = num(bar.get("low"))
        hi = num(bar.get("high"))
        if lo is None or lo <= 0:
            continue
        # Gap through the chase ceiling → treat as unfilled that session.
        if chase is not None and o is not None and o >= chase and day == day0:
            continue
        if lo <= target:
            # Conservative fill: cannot buy below the day's low; prefer target.
            fill = float(target)
            if o is not None and o < fill and lo <= o:
                # Opened through the band — fill at open.
                fill = float(o)
            if hi is not None and fill > hi:
                fill = float(hi)
            if fill < lo:
                fill = float(lo)
            return {
                "filled": True,
                "fill_price": round(fill, 4),
                "fill_date": day,
                "target": target,
                "mode": mode_s,
                "note": "触达价带",
            }
    return {
        "filled": False,
        "fill_price": None,
        "fill_date": None,
        "target": target,
        "mode": mode_s,
        "note": "未触达",
    }


def simulate_sell_fill(
    *,
    trade_date: str,
    bars_by_day: dict[str, dict[str, float | None]],
    sell: float | None,
    stop: float | None,
    look_ahead: int = 3,
) -> dict[str, Any] | None:
    """Simulate a sell: take-profit if high ≥ sell, else stop if low ≤ stop."""
    day0 = str(trade_date or "")[:10]
    if not day0 or not bars_by_day:
        return None
    days = sorted(d for d in bars_by_day if d >= day0)[: max(1, int(look_ahead) + 1)]
    for day in days:
        bar = bars_by_day.get(day) or {}
        o = num(bar.get("open"))
        lo = num(bar.get("low"))
        hi = num(bar.get("high"))
        # Stop first when both fire the same day (worse case for the plan).
        if stop is not None and lo is not None and lo <= stop:
            fill = float(stop)
            if o is not None and o < stop:
                fill = float(o)
            return {
                "filled": True,
                "fill_price": round(fill, 4),
                "fill_date": day,
                "exit_mode": "stop",
                "note": "触止损",
            }
        if sell is not None and hi is not None and hi >= sell:
            fill = float(sell)
            if o is not None and o > sell:
                fill = float(o)
            return {
                "filled": True,
                "fill_price": round(fill, 4),
                "fill_date": day,
                "exit_mode": "take",
                "note": "触卖价",
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


def _summarize_backtest(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate hit rates and average day1 for filled buys/sells."""
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
    client: Any | None = None,
) -> dict[str, Any]:
    """Replay paper signals in ``[date_from, date_to]`` with OHLC simulated fills.

    Read-only: never mutates ``signals`` / user meta.
    When ``dry_run`` is True, only count matching signals (no kline fetch).
    """
    import httpx

    from market_desk.eastmoney import fetch_daily_klines_many

    checked = validate_backtest_range(date_from, date_to)
    if isinstance(checked, dict):
        return checked
    d0, d1 = checked

    picked = collect_backtest_signals(
        date_from=d0,
        date_to=d1,
        include_sells=include_sells,
        ready_only=ready_only,
        limit=limit,
    )
    buy_n = sum(1 for r in picked if is_buy_signal(r.get("signal_type")))
    sell_n = sum(1 for r in picked if is_sell_signal(r.get("signal_type")))

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
            "max_span_days": MAX_BACKTEST_SPAN_DAYS,
            "matched_n": len(picked),
            "buy_n": buy_n,
            "sell_n": sell_n,
            "n": len(picked),
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
    finally:
        if own_client:
            await client.aclose()

    items: list[dict[str, Any]] = []
    for row in picked:
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
            sim = simulate_buy_fill(
                trade_date=day,
                bars_by_day=bars,
                wait=band["wait"],
                plan=band["plan"],
                chase=band["chase"],
                mode=mode,
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
        else:
            band = _plan_sell_prices(row)
            sim = simulate_sell_fill(
                trade_date=day,
                bars_by_day=bars,
                sell=band["sell"],
                stop=band["stop"],
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

    summary = _summarize_backtest(items)
    return {
        "ok": True,
        "dry_run": False,
        "date_from": d0,
        "date_to": d1,
        "mode": str(mode or "wait"),
        "ready_only": bool(ready_only),
        "include_sells": bool(include_sells),
        "max_span_days": MAX_BACKTEST_SPAN_DAYS,
        "n": len(items),
        "summary": summary,
        "items": items,
        "disclaimer": BACKTEST_DISCLAIMER,
        "note": BACKTEST_DISCLAIMER,
    }
