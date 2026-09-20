"""Tests for missed-buy attribution buckets."""

from __future__ import annotations

from market_desk.review import build_miss_attribution, build_missed_buys, classify_miss_kind


def test_classify_never_touched() -> None:
    row = {
        "traded": 0,
        "price_flags": ["miss_pullback"],
        "price_mark": "未回踩·已上行",
    }
    assert classify_miss_kind(row) == "never_touched"


def test_classify_touched_not_bought() -> None:
    row = {
        "traded": 0,
        "price_flags": ["in_band"],
        "price_mark": "建议价附近",
    }
    assert classify_miss_kind(row) == "touched_not_bought"


def test_classify_gate_blocked() -> None:
    row = {
        "traded": 0,
        "ready": 0,
        "price_flags": [],
        "live_last": 11.0,
        "price": 10.0,
        "payload": {"confirm_fail": ["离日高过近"], "block_ready": False},
    }
    assert classify_miss_kind(row) == "gate_blocked"


def test_build_miss_attribution_buckets() -> None:
    day = "2026-09-19"
    rows = [
        {
            "trade_date": day,
            "signal_type": "buy",
            "traded": 0,
            "skipped": 0,
            "code": "A",
            "name": "甲",
            "price": 10,
            "live_last": 11,
            "price_flags": ["miss_pullback"],
            "price_mark": "未回踩·已上行",
        },
        {
            "trade_date": day,
            "signal_type": "buy",
            "traded": 0,
            "skipped": 1,
            "code": "B",
            "name": "乙",
            "price": 10,
            "live_last": 10.2,
            "price_flags": ["in_band"],
            "price_mark": "建议价附近",
        },
        {
            "trade_date": day,
            "signal_type": "buy",
            "traded": 1,
            "code": "C",
            "price_flags": ["miss_pullback"],
        },
    ]
    attr = build_miss_attribution(rows, trade_date=day)
    assert attr["counts"]["never_touched"] == 1
    assert attr["counts"]["touched_not_bought"] == 1
    assert attr["total"] == 2
    # Adapt list stays miss_pullback-only.
    missed = build_missed_buys(rows, trade_date=day)
    assert len(missed) == 1
    assert missed[0]["code"] == "A"
