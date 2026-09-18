"""Exec diary merge into plan-execution scoring."""

from __future__ import annotations

from market_desk.review import (
    build_exec_score,
    diary_rows_as_exec_fills,
    merge_exec_score_rows,
)


def test_diary_chase_classifies() -> None:
    diary = [
        {
            "id": 1,
            "side": "buy",
            "code": "600000",
            "price": 12.0,
            "trade_date": "2026-09-18",
            "created_at": "2026-09-18 10:00:00",
            "advice": {"buy": {"wait_price": 10.0, "chase_price": 11.0, "kind": "stock"}},
        }
    ]
    fills = diary_rows_as_exec_fills(diary)
    assert len(fills) == 1
    score = build_exec_score(fills)
    assert score["traded_buy_n"] == 1
    assert score["chase_n"] == 1
    assert score["diary_n"] == 1
    assert score["score"] == 0.0


def test_merge_prefers_signal_over_diary() -> None:
    signals = [
        {
            "id": 9,
            "signal_type": "buy",
            "traded": 1,
            "code": "600000",
            "kind": "stock",
            "fill_price": 10.5,
            "price": 10.0,
            "wait_price": 10.0,
            "chase_price": 11.0,
            "trade_date": "2026-09-18",
            "signaled_at": "2026-09-18 09:40:00",
        }
    ]
    diary = [
        {
            "id": 2,
            "side": "buy",
            "code": "600000",
            "price": 12.0,
            "trade_date": "2026-09-18",
            "created_at": "2026-09-18 10:00:00",
            "advice": {"buy": {"wait_price": 10.0, "chase_price": 11.0}},
        }
    ]
    merged = merge_exec_score_rows(signals, diary)
    traded = [r for r in merged if int(r.get("traded") or 0)]
    assert len(traded) == 1
    assert traded[0]["id"] == 9
    score = build_exec_score(merged)
    assert score["diary_n"] == 0
    assert score["in_band_n"] == 1
