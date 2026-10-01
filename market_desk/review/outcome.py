"""Forward outcome scoring, horizons, and outcome write-back."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from market_desk.db import load_unscored_signals, mark_signal_outcome
from market_desk.filters import normalize_code
from market_desk.numbers import num
from market_desk.settings import setting

from market_desk.review.signals import _infer_source_board, is_buy_signal, is_sell_signal

try:
    from zoneinfo import ZoneInfo

    _CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    _CN_TZ = timezone(timedelta(hours=8))


def _cn_now() -> datetime:
    """Return timezone-aware China local time."""
    return datetime.now(_CN_TZ)


def forward_session_ready(day1: str, *, now: datetime | None = None) -> bool:
    """Return True when ``day1`` daily close is settled enough to score outcomes.

    Intraday feeds expose today's bar with ``close`` = last trade. Scoring at
    09:34 used to lock 「次日红」 on the open print; wait until the session is
    finished (or a later calendar day).
    """
    d1 = str(day1 or "")[:10]
    if not d1:
        return False
    clock = now or _cn_now()
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=_CN_TZ)
    today = clock.strftime("%Y-%m-%d")
    if d1 < today:
        return True
    if d1 > today:
        return False
    # Same calendar day as day1: only after the cash auction close.
    return (clock.hour, clock.minute) >= (15, 5)


def _pending_outcome_clear() -> dict[str, Any]:
    """Payload that clears a premature outcome lock (UI shows 待隔日)."""
    return {
        "outcome_day1_pct": None,
        "outcome_day3_pct": None,
        "outcome_mfe_pct": None,
        "outcome_mae_pct": None,
        "outcome_label": None,
        "outcome_pending": True,
        "outcome_checked_at": _cn_now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def score_signal_with_closes(
    signal: dict[str, Any],
    closes: list[float],
    dates: list[str] | None = None,
    *,
    opens: list[float | None] | None = None,
    lows: list[float | None] | None = None,
    highs: list[float | None] | None = None,
    standard: str = "classic",
) -> dict[str, Any] | None:
    """Score a buy/sell signal against daily bars. Return outcome fields or None.

    Standards:
      - ``classic``: entry = fill else plan; buy labels use next-session close.
      - ``same_day_plan``: entry = plan; require signal-day low ≤ plan; then score
        the **next** session(s) like classic (T+1-friendly, not same-day close).
      - ``filled``: entry = fill_price only (no fill → 「无成交」); next-session score.

    Sell MAE ignores the signal+1 session's **high** (open-reaction grace): day1
    contributes only via close so a noisy open spike is less likely to mark 卖飞.
    Watch-track sells (open buffer) use day0 open as a 09:45 decision-price proxy
    when fill/plan looks like an early-open print, so 卖飞 is not judged on 09:31 noise.

    Never finalize against an in-progress day1 bar; returns ``outcome_pending`` so
    callers can clear premature locks until 15:05 CN on day1.
    """
    from market_desk.config import (
        OUTCOME_FAKE_RED_CLOSE_MAX,
        OUTCOME_FAKE_RED_LOW_PCT,
        OUTCOME_FAKE_RED_OPEN_PCT,
        OUTCOME_STANDARDS,
    )

    std = str(standard or "classic").strip().lower()
    if std not in OUTCOME_STANDARDS:
        std = "classic"

    trade_date = str(signal.get("trade_date") or "")
    sig_type = signal.get("signal_type") or "buy"
    payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else {}
    plan_locked = num(
        signal.get("plan_price")
        if signal.get("plan_price") is not None
        else payload.get("plan_price")
    )
    plan = plan_locked if plan_locked is not None else num(signal.get("price"))
    fill = num(signal.get("fill_price"))
    open_buffer_track = str(
        signal.get("open_buffer_track")
        or payload.get("open_buffer_track")
        or ""
    ).strip().lower()
    buffer_decision = num(
        signal.get("open_buffer_decision_price")
        or payload.get("open_buffer_decision_price")
    )

    if std == "same_day_plan" and is_buy_signal(sig_type):
        price = plan
    elif std == "filled":
        price = fill if fill is not None and fill > 0 else None
        if price is None:
            return {
                "outcome_day1_pct": None,
                "outcome_day3_pct": None,
                "outcome_mfe_pct": None,
                "outcome_mae_pct": None,
                "outcome_label": "无成交",
                "outcome_standard": std,
                "outcome_checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
    else:
        price = fill if fill is not None and fill > 0 else plan
    if price is None or price <= 0 or not closes:
        return None

    # Index bars by date (signal day + forward).
    bar_by_day: dict[str, dict[str, float | None]] = {}
    if dates and len(dates) == len(closes) and trade_date:
        for i, (d, px) in enumerate(zip(dates, closes)):
            ds = str(d)
            bar_by_day[ds] = {
                "close": float(px),
                "open": (opens[i] if opens and i < len(opens) else None),
                "low": (lows[i] if lows and i < len(lows) else None),
                "high": (highs[i] if highs and i < len(highs) else None),
            }
    else:
        return None

    after_dates = sorted(d for d in bar_by_day if d > trade_date)
    if not after_dates and std != "same_day_plan":
        return None
    # Refuse to use today's unfinished bar (close == last print) for any horizon.
    settled = [d for d in after_dates if forward_session_ready(d)]
    if after_dates and not settled:
        return _pending_outcome_clear()
    after_dates = settled

    day0 = bar_by_day.get(trade_date) or {}
    day0_low = num(day0.get("low"))

    if is_sell_signal(sig_type):
        if not after_dates:
            return None
        exit_basis = "fill_or_plan"
        # Watch-track open buffer: score 卖飞 vs 09:45 decision price (minute
        # decision_price when stored; else day0 open as daily proxy). Skip when
        # signal already stamped after the buffer window.
        if open_buffer_track == "watch":
            late_enough = False
            stamped = str(signal.get("signaled_at") or "")
            if len(stamped) >= 16:
                try:
                    hhmm = stamped[11:16]
                    late_enough = hhmm >= "09:45"
                except Exception:
                    late_enough = False
            if not late_enough:
                day0_open = num(day0.get("open"))
                proxy = buffer_decision
                if proxy is None or proxy <= 0:
                    proxy = day0_open
                if proxy is not None and proxy > 0:
                    price = float(proxy)
                    exit_basis = (
                        "watch_945_minute"
                        if buffer_decision is not None and buffer_decision > 0
                        else "watch_945_open_proxy"
                    )
        after_closes = [float(bar_by_day[d]["close"]) for d in after_dates]  # type: ignore[index]
        day1 = after_closes[0]
        day3 = after_closes[min(2, len(after_closes) - 1)]
        trough = min(after_closes)
        # Open-reaction grace: day1 high does not inflate "left upside" / 卖飞.
        peak = float(day1)
        for i, d in enumerate(after_dates):
            bar = bar_by_day[d]
            c = num(bar.get("close"))
            h = num(bar.get("high"))
            if c is not None:
                peak = max(peak, float(c))
            if i == 0:
                continue
            if h is not None:
                peak = max(peak, float(h))
        d1 = (price / day1 - 1.0) * 100.0
        d3 = (price / day3 - 1.0) * 100.0
        mfe = (price / trough - 1.0) * 100.0
        mae = (price / peak - 1.0) * 100.0
        # Round before thresholds so stored pct and label never disagree at edges.
        d1 = round(d1, 2)
        d3 = round(d3, 2)
        mfe = round(mfe, 2)
        mae = round(mae, 2)
        from market_desk.config import SELL_FLY_DAY1_PCT, SELL_FLY_MAE_PCT

        left = abs(mae) if mae <= 0 else 0.0
        if d1 <= float(SELL_FLY_DAY1_PCT) or left >= float(SELL_FLY_MAE_PCT):
            label = "卖飞"
        elif d1 >= 1.0:
            label = "卖后回落"
        elif d1 <= -1.5:
            label = "卖后继续涨"
        else:
            label = "平淡"
        return {
            "outcome_day1_pct": d1,
            "outcome_day3_pct": d3,
            "outcome_mfe_pct": mfe,
            "outcome_mae_pct": mae,
            "outcome_label": label,
            "outcome_standard": std,
            "outcome_exit_basis": exit_basis,
            "outcome_exit_price": round(float(price), 4),
            "outcome_checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # --- buys ---
    if std == "same_day_plan":
        # Human ops under T+1: must touch plan that day, then score next session(s).
        if day0_low is None or day0_low > price:
            return {
                "outcome_day1_pct": None,
                "outcome_day3_pct": None,
                "outcome_mfe_pct": None,
                "outcome_mae_pct": None,
                "outcome_label": "当日未触达",
                "outcome_standard": std,
                "outcome_checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        if not after_dates:
            return {
                "outcome_day1_pct": None,
                "outcome_day3_pct": None,
                "outcome_mfe_pct": None,
                "outcome_mae_pct": None,
                "outcome_label": "待隔日",
                "outcome_standard": std,
                "outcome_checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    if not after_dates:
        return None

    after_closes = [float(bar_by_day[d]["close"]) for d in after_dates]  # type: ignore[index]
    day1 = after_closes[0]
    day3 = after_closes[min(2, len(after_closes) - 1)]
    peak = max(after_closes)
    trough = min(after_closes)
    day1_bar = bar_by_day[after_dates[0]]
    day1_open = num(day1_bar.get("open"))
    day1_low = num(day1_bar.get("low"))

    d1 = (day1 / price - 1.0) * 100.0
    d3 = (day3 / price - 1.0) * 100.0
    mfe = (peak / price - 1.0) * 100.0
    mae = (trough / price - 1.0) * 100.0
    if day1_low is not None and float(day1_low) > 0:
        try:
            day_mae = (float(day1_low) / price - 1.0) * 100.0
            mae = min(mae, day_mae)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    # Round before label thresholds so UI pct and label stay consistent
    # (e.g. raw -1.498 used to miss 次日绿 then store as -1.5 + 三日绿).
    d1 = round(d1, 2)
    d3 = round(d3, 2)
    mfe = round(mfe, 2)
    mae = round(mae, 2)
    open_pct = None
    if day1_open is not None and float(day1_open) > 0:
        try:
            open_pct = round((float(day1_open) / price - 1.0) * 100.0, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            open_pct = None
    low_pct = None
    if day1_low is not None and float(day1_low) > 0:
        try:
            low_pct = round((float(day1_low) / price - 1.0) * 100.0, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            low_pct = None

    if d1 >= 1.0:
        label = "次日红"
        if low_pct is not None and low_pct <= float(OUTCOME_FAKE_RED_LOW_PCT):
            label = "次日虚红"
    elif d1 <= -1.5:
        # Deep green wins over open-fade taxonomy.
        label = "次日绿"
    elif (
        open_pct is not None
        and open_pct >= float(OUTCOME_FAKE_RED_OPEN_PCT)
        and d1 < float(OUTCOME_FAKE_RED_CLOSE_MAX)
    ):
        label = "次日冲高回落"
    elif d3 >= 2.0:
        label = "三日红"
    elif d3 <= -2.0:
        label = "三日绿"
    else:
        label = "平淡"

    return {
        "outcome_day1_pct": d1,
        "outcome_day3_pct": d3,
        "outcome_mfe_pct": mfe,
        "outcome_mae_pct": mae,
        "outcome_label": label,
        "outcome_standard": std,
        "outcome_checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# Labels that count as a buy "hit" for rate / soft feedback.
BUY_HIT_LABELS = frozenset({"次日红", "三日红"})


def overlay_outcomes_for_standard(
    rows: list[dict[str, Any]],
    closes_map: dict[str, Any],
    *,
    standard: str,
) -> list[dict[str, Any]]:
    """Return row copies with outcome fields rescored for a display standard."""
    std = str(standard or "classic").strip().lower()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if std == "classic":
            item["outcome_standard"] = "classic"
            out.append(item)
            continue
        code = normalize_code(row.get("code"))
        packed = closes_map.get(code)
        if not packed:
            item["outcome_standard"] = std
            out.append(item)
            continue
        if len(packed) >= 3:
            dates, closes, ohlc = packed[0], packed[1], packed[2] or {}
        else:
            dates, closes = packed[0], packed[1]
            ohlc = {}
        scored = score_signal_with_closes(
            row,
            closes,
            dates,
            opens=list(ohlc.get("open") or []) or None,
            lows=list(ohlc.get("low") or []) or None,
            highs=list(ohlc.get("high") or []) or None,
            standard=std,
        )
        if scored:
            item.update(scored)
        else:
            item["outcome_standard"] = std
        out.append(item)
    return out


def summarize_signals(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> dict[str, Any]:
    """Aggregate hit-rate style stats for the review panel.

    hit_mode:
      - traded: only rows marked traded (default, cleaner feedback loop)
      - all: any non-skipped scored row (paper signals)
    """
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    if mode not in ("traded", "all"):
        mode = "traded"
    active = [r for r in rows if not int(r.get("skipped") or 0)]
    buys = [r for r in active if is_buy_signal(r.get("signal_type"))]
    sells = [r for r in active if is_sell_signal(r.get("signal_type"))]
    scored_buys_all = [r for r in buys if r.get("outcome_label")]
    scored_buys = (
        [r for r in scored_buys_all if int(r.get("traded") or 0)]
        if mode == "traded"
        else scored_buys_all
    )
    scored_sells = [r for r in sells if r.get("outcome_label")]
    if mode == "traded":
        scored_sells = [r for r in scored_sells if int(r.get("traded") or 0)]

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

    paper = [r for r in scored_buys_all if num(r.get("outcome_day3_pct")) is not None]
    paper_pnl = _avg(paper, "outcome_day3_pct")

    return {
        "buy_total": len(buys),
        "buy_scored": len(scored_buys),
        "buy_scored_all": len(scored_buys_all),
        "sell_total": len(sells),
        "sell_scored": len(scored_sells),
        "buy_hit_rate": _rate(scored_buys, BUY_HIT_LABELS),
        "buy_hit_rate_all": _rate(scored_buys_all, BUY_HIT_LABELS),
        "sell_hit_rate": _rate(scored_sells, {"卖后回落"}),
        "buy_avg_day1": _avg(scored_buys, "outcome_day1_pct"),
        "buy_avg_day3": _avg(scored_buys, "outcome_day3_pct"),
        "paper_avg_day3": paper_pnl,
        "hit_rate_mode": mode,
        "skipped": sum(1 for r in rows if int(r.get("skipped") or 0)),
        "traded": sum(1 for r in rows if int(r.get("traded") or 0)),
        "pending": sum(1 for r in active if not r.get("outcome_label")),
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
    if item.get("plan_price") is None:
        item["plan_price"] = num(payload.get("plan_price"))
    if item.get("plan_qty") is None:
        pq = num(payload.get("qty"))
        if pq is not None and float(pq) > 0:
            item["plan_qty"] = int(pq)
    if not item.get("desk_source"):
        item["desk_source"] = payload.get("desk_source") or (
            "main"
            if is_buy_signal(item.get("signal_type"))
            else ("sell" if is_sell_signal(item.get("signal_type")) else None)
        )
    if not item.get("source_board"):
        sb = str(payload.get("source_board") or "").strip()
        if not sb:
            sb = _infer_source_board(item, payload)
        item["source_board"] = sb or None
    if item.get("near_entry") is None and "near_entry" in payload:
        item["near_entry"] = bool(payload.get("near_entry"))
    if not item.get("confirm_fail") and payload.get("confirm_fail"):
        item["confirm_fail"] = list(payload.get("confirm_fail") or [])
    if not item.get("confirm_soft") and payload.get("confirm_soft"):
        item["confirm_soft"] = list(payload.get("confirm_soft") or [])
    if not item.get("reason") and payload.get("reason"):
        item["reason"] = str(payload.get("reason") or "")[:240] or None
    if not item.get("role_label") and payload.get("role_label"):
        item["role_label"] = payload.get("role_label")
    if not isinstance(item.get("minute"), dict) and isinstance(payload.get("minute"), dict):
        item["minute"] = payload.get("minute")
    if not isinstance(item.get("cv"), dict) and isinstance(payload.get("cv"), dict):
        item["cv"] = payload.get("cv")
    if isinstance(item.get("cv"), dict) and "cv_tags" not in item:
        from market_desk.chip_volume import cv_tags

        item["cv_tags"] = cv_tags(item["cv"])
    return item


OUTCOME_HORIZON_SESSIONS = 3


def outcome_final_date(trade_date: str, sessions: int = OUTCOME_HORIZON_SESSIONS) -> str:
    """Return the trading date whose close completes the ``sessions``-day horizon."""
    from datetime import date as _date

    from market_desk.calendar import is_trading_day

    try:
        d = _date.fromisoformat(str(trade_date or "")[:10])
    except ValueError:
        return ""
    n = 0
    for _ in range(40):
        d = d + timedelta(days=1)
        if is_trading_day(d):
            n += 1
            if n >= sessions:
                return d.isoformat()
    return ""


def load_pending_outcomes(today: str, *, since: str, cap: int = 80) -> list[dict[str, Any]]:
    """Return up to ``cap`` signals whose outcome still needs (re)scoring.

    Final rows are dropped before capping; with a small SQL limit the settled
    rows inside the open-horizon window crowded out the newest trade day.

    Args:
        today: Only rows before this trade date are eligible.
        since: Labeled rows from this date on are re-checked until final.
        cap: Maximum rows handed to one scoring pass.
    """
    rows = load_unscored_signals(today, limit=5000, labeled_since=since)
    return [r for r in rows if not outcome_is_final(r)][: int(cap)]


def outcome_is_final(row: dict[str, Any]) -> bool:
    """True when the stored outcome was scored after the day-3 close settled.

    Rows scored earlier carry a partial 三日% (day1/day2 close) and a label that
    may still flip, so they are re-scored until the horizon completes.
    """
    if not row.get("outcome_label"):
        return False
    final_day = outcome_final_date(str(row.get("trade_date") or ""))
    checked = str(row.get("outcome_checked_at") or "")
    if not final_day or len(checked) < 10:
        return False
    return checked >= f"{final_day} 15:05"


def apply_outcomes(
    rows: list[dict[str, Any]],
    closes_map: dict[str, tuple[list[str], list[float]]],
    *,
    overwrite: bool = False,
) -> int:
    """Write scored outcomes for signals that have forward closes. Return update count.

    When ``overwrite`` is True, re-score rows that already have a label (used by
    formula-version migrations such as OHLC fake-red labels). Labeled rows whose
    3-session horizon was not complete at scoring time are always refreshed.

    If day1 is still the in-progress session, clear any premature label so the
    review UI shows 待隔日 until 15:05.
    """
    n = 0
    today = _cn_now().strftime("%Y-%m-%d")
    for row in rows:
        if str(row.get("trade_date") or "") >= today:
            continue
        code = normalize_code(row.get("code"))
        packed = closes_map.get(code)
        if not packed:
            continue
        if len(packed) >= 3:
            dates, closes, ohlc = packed[0], packed[1], packed[2] or {}
        else:
            dates, closes = packed[0], packed[1]
            ohlc = {}
        outcome = score_signal_with_closes(
            row,
            closes,
            dates,
            opens=list(ohlc.get("open") or []) or None,
            lows=list(ohlc.get("low") or []) or None,
            highs=list(ohlc.get("high") or []) or None,
        )
        if outcome and outcome.get("outcome_pending"):
            # Drop open-print locks written before the day1 session settled.
            if row.get("outcome_label"):
                if mark_signal_outcome(int(row["id"]), outcome):
                    n += 1
            continue
        if row.get("outcome_label") and not overwrite and outcome_is_final(row):
            continue
        if not outcome:
            continue
        if row.get("outcome_label") and _same_outcome(row, outcome):
            if outcome_is_final({**row, **outcome}):
                mark_signal_outcome(int(row["id"]), outcome)
            continue
        if mark_signal_outcome(int(row["id"]), outcome):
            n += 1
    return n


def _same_outcome(row: dict[str, Any], outcome: dict[str, Any]) -> bool:
    """True when label and stored pct fields already match a fresh score."""
    if str(row.get("outcome_label") or "") != str(outcome.get("outcome_label") or ""):
        return False
    for key in ("outcome_day1_pct", "outcome_day3_pct", "outcome_mfe_pct", "outcome_mae_pct"):
        a, b = row.get(key), outcome.get(key)
        if a is None and b is None:
            continue
        if a is None or b is None or abs(float(a) - float(b)) > 1e-6:
            return False
    return True
