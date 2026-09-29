"""Review-page hints: above-plan chase mark, afternoon weak window, session banner."""

from __future__ import annotations

from datetime import datetime

from market_desk.review import (
    build_pm_weak_stats,
    build_review_session_hint,
    enrich_signals_with_live_marks,
    mark_pm_weak_signals,
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


def test_pm_weak_window_tags_buys_only():
    rows = mark_pm_weak_signals(
        [
            _buy("600001", "12:59"),
            _buy("600002", "13:00"),
            _buy("600003", "13:59"),
            _buy("600004", "14:00"),
            _buy("600005", "13:10", signal_type="sell"),
        ]
    )
    assert [r["pm_weak"] for r in rows] == [False, True, True, False, False]


def test_pm_weak_stats_split():
    rows = [
        _buy("600001", "13:10", outcome_day3_pct=-3.0),
        _buy("600002", "13:40", outcome_day3_pct=1.0),
        _buy("600003", "10:00", outcome_day3_pct=2.0),
        _buy("600004", "10:30", outcome_day3_pct=None),
    ]
    st = build_pm_weak_stats(rows)
    assert st["window"] == {"n": 2, "win3": 50.0, "d3": -1.0}
    assert st["rest"] == {"n": 1, "win3": 100.0, "d3": 2.0}


def test_session_hint_warns_inside_window():
    day_rows = mark_pm_weak_signals([_buy("600001", "13:05"), _buy("600002", "10:00")])
    day_rows[1]["price_flags"] = ["above_plan"]
    stats = {"window": {"n": 38, "win3": 26.3}, "rest": {"n": 390, "win3": 46.0}}
    hint = build_review_session_hint(
        day_rows, is_today=True, now=datetime(2026, 9, 28, 13, 20), stats=stats
    )
    assert hint["ok"] and hint["in_pm_weak"] and hint["level"] == "warn"
    assert hint["buy_n"] == 2 and hint["pm_weak_n"] == 1 and hint["above_plan_n"] == 1
    assert any("午后弱窗" in t for t in hint["tips"])
    assert "26.3%" in hint["history"] and "46.0%" in hint["history"]


def test_session_hint_quiet_outside_window_and_non_trading_day():
    morning = build_review_session_hint([], is_today=True, now=datetime(2026, 9, 28, 10, 0))
    assert morning["level"] == "info" and morning["session"] == "上午盘"
    assert morning["tips"] == []
    holiday = build_review_session_hint([], is_today=True, now=datetime(2026, 10, 1, 13, 20))
    assert holiday["session"] == "非交易日" and not holiday["in_pm_weak"]
    assert build_review_session_hint([], is_today=False) == {"ok": False}
