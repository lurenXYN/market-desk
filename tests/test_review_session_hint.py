"""Review-page hints: above-plan chase mark and the session banner."""

from __future__ import annotations

from datetime import datetime

from market_desk.review import (
    build_review_session_hint,
    enrich_signals_with_live_marks,
)

TRADE_DAY = "2026-09-28"


def _buy(code: str, hhmm: str, price: float = 10.0, **extra):
    row = {
        "id": 1,
        "code": code,
        "signal_type": "buy",
        "signaled_at": f"{TRADE_DAY} {hhmm}:00",
        "price": price,
        "payload": {"wait_price": price, "chase_price": price * 1.05, "stop_price": price * 0.95},
    }
    row.update(extra)
    return row


def test_above_plan_marks_chase_and_caution():
    rows = enrich_signals_with_live_marks(
        [_buy("600001", "09:40")],
        {"600001": {"price": 10.2, "low": 9.9}},
    )
    r = rows[0]
    assert "above_plan" in r["price_flags"]
    assert r["above_plan_pct"] == 2.0
    assert r["price_mark"] == "高于计划价 +2.0%"
    assert "in_band" in r["price_flags"]
    assert r["buy_caution"] == "挂计划价，不追"
    assert "只按计划价挂单" in r["buy_caution_tip"]


def test_above_plan_skipped_below_threshold_and_on_chase_hit():
    small = enrich_signals_with_live_marks(
        [_buy("600001", "09:40")], {"600001": {"price": 10.05, "low": 9.9}}
    )[0]
    assert "above_plan" not in small["price_flags"]
    capped = enrich_signals_with_live_marks(
        [_buy("600001", "09:40")], {"600001": {"price": 10.6, "low": 9.9}}
    )[0]
    assert "chase_hit" in capped["price_flags"]
    assert "above_plan" not in capped["price_flags"]
    assert capped["buy_caution"] == "过不追价，放弃"
    assert capped["buy_caution_tip"] == "现价已过不追价，当日不宜追高"


def test_sell_rows_never_marked_above_plan():
    sell = _buy("600001", "09:40", signal_type="sell")
    r = enrich_signals_with_live_marks([sell], {"600001": {"price": 10.3, "low": 9.9}})[0]
    assert "above_plan" not in r["price_flags"]


def test_session_hint_counts_and_above_plan_tip():
    day_rows = [_buy("600001", "13:05"), _buy("600002", "10:00", price_flags=["above_plan"])]
    hint = build_review_session_hint(day_rows, is_today=True, now=datetime(2026, 9, 28, 13, 20))
    assert hint["ok"] and hint["level"] == "info" and hint["session"] == "午后盘"
    assert hint["buy_n"] == 2 and hint["above_plan_n"] == 1
    assert len(hint["tips"]) == 1 and "只按计划价挂单" in hint["tips"][0]
    assert "pm_weak_n" not in hint and "in_pm_weak" not in hint


def test_session_hint_quiet_morning_and_non_trading_day():
    morning = build_review_session_hint([], is_today=True, now=datetime(2026, 9, 28, 10, 0))
    assert morning["level"] == "info" and morning["session"] == "上午盘"
    assert morning["tips"] == []
    holiday = build_review_session_hint([], is_today=True, now=datetime(2026, 10, 1, 13, 20))
    assert holiday["session"] == "非交易日"
    assert build_review_session_hint([], is_today=False) == {"ok": False}
