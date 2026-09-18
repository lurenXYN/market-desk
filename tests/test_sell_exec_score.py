"""Sell-side exec diary scoring (mirror of buy plan execution)."""

from __future__ import annotations

from market_desk.review import (
    build_sell_exec_score,
    classify_sell_fill_execution,
    diary_rows_as_sell_fills,
    merge_sell_exec_score_rows,
)


def test_sell_diary_in_band() -> None:
    diary = [
        {
            "id": 1,
            "side": "half",
            "code": "600000",
            "price": 10.0,
            "trade_date": "2026-09-18",
            "created_at": "2026-09-18 14:00:00",
            "advice": {"sell": {"sell_price": 10.2, "stop_price": 9.5}},
        }
    ]
    fills = diary_rows_as_sell_fills(diary)
    assert len(fills) == 1
    assert classify_sell_fill_execution(fills[0]) == "in_band"
    score = build_sell_exec_score(fills)
    assert score["traded_sell_n"] == 1
    assert score["in_band_n"] == 1
    assert score["score"] == 100.0


def test_sell_diary_late() -> None:
    diary = [
        {
            "id": 2,
            "side": "clear",
            "code": "600001",
            "price": 12.0,
            "trade_date": "2026-09-18",
            "created_at": "2026-09-18 14:30:00",
            "advice": {"sell": {"sell_price": 10.0}},
        }
    ]
    fills = diary_rows_as_sell_fills(diary)
    assert classify_sell_fill_execution(fills[0]) == "late"
    score = build_sell_exec_score(fills)
    assert score["late_n"] == 1
    assert score["score"] == 20.0


def test_sell_unplanned_skipped() -> None:
    diary = [
        {
            "id": 3,
            "side": "clear",
            "code": "600002",
            "price": 8.0,
            "trade_date": "2026-09-18",
            "advice": {"source": "manual"},
        }
    ]
    assert diary_rows_as_sell_fills(diary) == []


def test_merge_sell_prefers_signal() -> None:
    signals = [
        {
            "id": 9,
            "signal_type": "sell",
            "traded": 1,
            "code": "600000",
            "fill_price": 10.0,
            "price": 10.0,
            "sell_price": 10.0,
            "trade_date": "2026-09-18",
        }
    ]
    diary = [
        {
            "id": 4,
            "side": "half",
            "code": "600000",
            "price": 11.0,
            "trade_date": "2026-09-18",
            "advice": {"sell": {"sell_price": 10.0}},
        }
    ]
    merged = merge_sell_exec_score_rows(signals, diary)
    sells = [r for r in merged if int(r.get("traded") or 0)]
    assert len(sells) == 1
    assert sells[0]["id"] == 9
