"""Signal logging and post-trade review helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.db import (
    load_signals,
    mark_signal_outcome,
    upsert_signal,
)
from market_desk.filters import normalize_code
from market_desk.numbers import num


def record_session_signals(snapshot: dict[str, Any]) -> int:
    """Persist buy/sell recommendations for the current session. Return insert/update count."""
    trade_date = snapshot.get("trade_date") or ""
    if not trade_date:
        return 0
    signaled_at = snapshot.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    phase = snapshot.get("phase") or ""
    verdict = snapshot.get("verdict") or {}
    action = verdict.get("action") or ""
    mainline = ((verdict.get("mainline") or {}).get("name")) or ""
    n = 0

    rec = verdict.get("recommend") or {}
    # Record buyable names, plus pending/manual stock cards for user review.
    record_items = bool(action in ("可买入", "可小仓", "观察回踩") or rec.get("buy") or rec.get("items"))
    if record_items:
        for item in rec.get("items") or []:
            code = normalize_code(item.get("code"))
            if not code:
                continue
            kind = item.get("kind") or "stock"
            pending = bool(item.get("trend_pending"))
            manual_up = item.get("trend_manual") == "up"
            if kind == "stock" and not item.get("trend_ok") and not pending and not manual_up:
                # Confirmed non-uptrend: still log for review when action is buy-ish.
                if action not in ("可买入", "可小仓", "观察回踩") and not rec.get("buy"):
                    continue
            if kind == "etf" and not item.get("ready") and not rec.get("buy"):
                if action not in ("可买入", "可小仓", "观察回踩"):
                    continue
            price = num(item.get("buy_price"))
            if price is None:
                price = num(item.get("last"))
            if price is None:
                continue
            upsert_signal(
                {
                    "trade_date": trade_date,
                    "signaled_at": signaled_at,
                    "signal_type": "buy",
                    "action": action,
                    "phase": phase,
                    "mainline": mainline,
                    "code": code,
                    "name": item.get("name") or "",
                    "kind": kind,
                    "price": float(price),
                    "last": num(item.get("last")),
                    "ready": 1 if item.get("ready") else 0,
                    "payload": {
                        "wait_price": item.get("wait_price"),
                        "stop_price": item.get("stop_price"),
                        "chase_price": item.get("chase_price"),
                        "pct": item.get("pct"),
                        "trend": item.get("trend"),
                        "trend_quality": item.get("trend_quality"),
                        "trend_pending": pending,
                        "trend_manual": item.get("trend_manual"),
                        "trend_ok": bool(item.get("trend_ok")),
                    },
                }
            )
            n += 1

    sell = snapshot.get("sell_advice") or {}
    for item in sell.get("items") or []:
        if not item.get("ready"):
            continue
        code = normalize_code(item.get("code"))
        if not code:
            continue
        price = num(item.get("sell_price"))
        if price is None:
            price = num(item.get("last"))
        if price is None:
            continue
        upsert_signal(
            {
                "trade_date": trade_date,
                "signaled_at": signaled_at,
                "signal_type": "sell",
                "action": item.get("role_label") or "建议卖出",
                "phase": phase,
                "mainline": mainline,
                "code": code,
                "name": item.get("name") or "",
                "kind": item.get("kind") or "stock",
                "price": float(price),
                "last": num(item.get("last")),
                "ready": 1,
                "payload": {
                    "buy_price": item.get("buy_price"),
                    "stop_price": item.get("stop_price"),
                    "pnl_pct": item.get("pnl_pct"),
                    "qty": item.get("qty"),
                },
            }
        )
        n += 1
    return n


def score_signal_with_closes(
    signal: dict[str, Any],
    closes: list[float],
    dates: list[str] | None = None,
) -> dict[str, Any] | None:
    """Score a buy/sell signal against subsequent daily closes. Return outcome fields or None."""
    price = num(signal.get("price"))
    if price is None or price <= 0 or not closes:
        return None
    trade_date = str(signal.get("trade_date") or "")
    sig_type = signal.get("signal_type") or "buy"

    # Prefer closes strictly after the signal date when dates are available.
    after: list[float] = []
    if dates and len(dates) == len(closes) and trade_date:
        for d, px in zip(dates, closes):
            if str(d) > trade_date:
                after.append(float(px))
        if not after:
            return None
    else:
        return None
    if not after:
        return None

    day1 = after[0]
    day3 = after[min(2, len(after) - 1)]
    peak = max(after)
    trough = min(after)
    if sig_type == "buy":
        d1 = (day1 / price - 1.0) * 100.0
        d3 = (day3 / price - 1.0) * 100.0
        mfe = (peak / price - 1.0) * 100.0
        mae = (trough / price - 1.0) * 100.0
        if d1 >= 1.0:
            label = "次日红"
        elif d1 <= -1.5:
            label = "次日绿"
        elif d3 >= 2.0:
            label = "三日红"
        elif d3 <= -2.0:
            label = "三日绿"
        else:
            label = "平淡"
    else:
        # Sell: positive means avoiding further drop (price fell after sell).
        d1 = (price / day1 - 1.0) * 100.0
        d3 = (price / day3 - 1.0) * 100.0
        mfe = (price / trough - 1.0) * 100.0
        mae = (price / peak - 1.0) * 100.0
        if d1 >= 1.0:
            label = "卖后回落"
        elif d1 <= -1.5:
            label = "卖后继续涨"
        else:
            label = "平淡"

    return {
        "outcome_day1_pct": round(d1, 2),
        "outcome_day3_pct": round(d3, 2),
        "outcome_mfe_pct": round(mfe, 2),
        "outcome_mae_pct": round(mae, 2),
        "outcome_label": label,
        "outcome_checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def summarize_signals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate hit-rate style stats for the review panel."""
    active = [r for r in rows if not int(r.get("skipped") or 0)]
    buys = [r for r in active if r.get("signal_type") == "buy"]
    sells = [r for r in active if r.get("signal_type") == "sell"]
    scored_buys = [r for r in buys if r.get("outcome_label")]
    scored_sells = [r for r in sells if r.get("outcome_label")]

    def _rate(items: list[dict[str, Any]], good: set[str]) -> float | None:
        if not items:
            return None
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in good)
        return round(100.0 * hit / len(items), 1)

    def _avg(items: list[dict[str, Any]], key: str) -> float | None:
        vals = [num(r.get(key)) for r in items]
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    # Simple paper P&L: assume buy at signal price, mark day3 close move.
    paper = [r for r in scored_buys if num(r.get("outcome_day3_pct")) is not None]
    paper_pnl = _avg(paper, "outcome_day3_pct")

    return {
        "buy_total": len(buys),
        "buy_scored": len(scored_buys),
        "sell_total": len(sells),
        "sell_scored": len(scored_sells),
        "buy_hit_rate": _rate(scored_buys, {"次日红", "三日红"}),
        "sell_hit_rate": _rate(scored_sells, {"卖后回落"}),
        "buy_avg_day1": _avg(scored_buys, "outcome_day1_pct"),
        "buy_avg_day3": _avg(scored_buys, "outcome_day3_pct"),
        "paper_avg_day3": paper_pnl,
        "skipped": sum(1 for r in rows if int(r.get("skipped") or 0)),
        "traded": sum(1 for r in rows if int(r.get("traded") or 0)),
        "pending": sum(1 for r in active if not r.get("outcome_label")),
    }


def enrich_signals_with_live_marks(
    rows: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flag signals whose live price hit stop or chase levels."""
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        code = normalize_code(item.get("code"))
        q = quotes.get(code) or {}
        last = num(q.get("price"))
        day_low = num(q.get("low"))
        stop = num(payload.get("stop_price"))
        chase = num(payload.get("chase_price"))
        wait = num(payload.get("wait_price"))
        sig_px = num(item.get("price"))
        item["chase_price"] = chase
        item["wait_price"] = wait
        item["stop_price"] = stop
        flags: list[str] = []
        labels: list[str] = []
        if last is not None and stop is not None and last <= stop:
            flags.append("stop_hit")
            labels.append("触及止损")
        if last is not None and chase is not None and last >= chase:
            flags.append("chase_hit")
            labels.append("触及不追")
        if (
            last is not None
            and wait is not None
            and stop is not None
            and stop < last < wait
            and "stop_hit" not in flags
            and "chase_hit" not in flags
        ):
            flags.append("near_wait")
            labels.append("回踩区间")
        # Ideal entry never touched today, yet price already ran higher.
        miss_pullback = (
            str(item.get("signal_type") or "") == "buy"
            and last is not None
            and sig_px is not None
            and day_low is not None
            and day_low > sig_px
            and last > sig_px
            and "stop_hit" not in flags
        )
        if miss_pullback:
            flags.append("miss_pullback")
            labels.append("未回踩·已上行")
        elif (
            last is not None
            and wait is not None
            and chase is not None
            and wait <= last < chase
            and "chase_hit" not in flags
            and "stop_hit" not in flags
        ):
            flags.append("in_band")
            labels.append("建议价附近")
        item["live_last"] = last
        item["live_pct"] = num(q.get("pct"))
        if last is not None and sig_px is not None and sig_px > 0:
            item["dev_pct"] = round((float(last) / float(sig_px) - 1.0) * 100.0, 2)
        else:
            item["dev_pct"] = None
        if last is not None and chase is not None and chase > 0:
            item["chase_dev_pct"] = round((float(last) / float(chase) - 1.0) * 100.0, 2)
        else:
            item["chase_dev_pct"] = None
        item["price_flags"] = flags
        item["price_mark"] = " / ".join(labels) if labels else ""
        # Buying caution for same-day signals.
        if str(item.get("signal_type") or "") == "buy":
            if "stop_hit" in flags:
                item["buy_caution"] = "现价已到止损带，当日不宜再按原计划买"
            elif "chase_hit" in flags:
                item["buy_caution"] = "现价已过不追价，当日不宜追高"
            elif "miss_pullback" in flags:
                item["buy_caution"] = "未回踩建议价已上行，勿死等；可对照不追价决定是否放弃"
            elif "near_wait" in flags:
                item["buy_caution"] = "现价在回踩带，可观察是否站稳"
            else:
                item["buy_caution"] = ""
        else:
            item["buy_caution"] = ""
        out.append(item)
    return out


def build_review_payload(
    limit: int = 60,
    quotes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Load recent signals and summary; optionally attach live price marks."""
    rows = [_flatten_signal_prices(r) for r in load_signals(limit=limit)]
    if quotes:
        rows = enrich_signals_with_live_marks(rows, quotes)
    return {
        "ok": True,
        "signals": rows,
        "summary": summarize_signals(rows),
    }


def _flatten_signal_prices(row: dict[str, Any]) -> dict[str, Any]:
    """Copy plan prices from payload onto the top-level signal row."""
    item = dict(row)
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    if item.get("chase_price") is None:
        item["chase_price"] = num(payload.get("chase_price"))
    if item.get("wait_price") is None:
        item["wait_price"] = num(payload.get("wait_price"))
    if item.get("stop_price") is None:
        item["stop_price"] = num(payload.get("stop_price"))
    return item


def apply_outcomes(
    rows: list[dict[str, Any]],
    closes_map: dict[str, tuple[list[str], list[float]]],
) -> int:
    """Write scored outcomes for signals that have forward closes. Return update count."""
    n = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for row in rows:
        if row.get("outcome_label"):
            continue
        if str(row.get("trade_date") or "") >= today:
            continue
        code = normalize_code(row.get("code"))
        packed = closes_map.get(code)
        if not packed:
            continue
        dates, closes = packed
        outcome = score_signal_with_closes(row, closes, dates)
        if not outcome:
            continue
        if mark_signal_outcome(int(row["id"]), outcome):
            n += 1
    return n
