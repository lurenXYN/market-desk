"""P0: NP miss truth source, miss loosen gate, historical live-mark freeze."""

from __future__ import annotations

from market_desk.adapt.tune import build_auto_tune_deltas
from market_desk.np_review import build_np_missed
from market_desk.review.payload import enrich_signals_with_live_marks


def test_build_np_missed_ignores_price_flags_and_fills():
    rows = [
        {
            "id": 1,
            "code": "600001",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "price": 10.0,
            "owner_user_id": 0,
            "payload": {
                "np": {"np": True, "day_low": 10.2, "day_close": 10.5, "plan": 10.0},
                "context": {"key": "open30|high", "tagged": True, "label": "开盘·高波动"},
            },
            "price_flags": [],  # raw DB row never has these
        },
        {
            "id": 2,
            "code": "600002",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "price": 10.0,
            "fill_price": 10.1,
            "owner_user_id": 0,
            "payload": {"np": {"np": True, "day_low": 10.2, "day_close": 10.5, "plan": 10.0}},
        },
        {
            "id": 3,
            "code": "600003",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "price": 10.0,
            "owner_user_id": 0,
            "payload": {"np": {"np": False, "day_low": 9.8, "day_close": 10.5, "plan": 10.0}},
        },
        {
            "id": 4,
            "code": "600004",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "price": 10.0,
            "owner_user_id": 0,
            # live flag only — must NOT count for adapt
            "price_flags": ["miss_pullback"],
            "price_mark": "未回踩·已上行",
            "payload": {},
        },
    ]
    missed = build_np_missed(rows)
    assert [m["id"] for m in missed] == [1]


def test_miss_loosen_gated_by_allow_flag():
    ctx = {"tagged": True, "key": "open30|high", "label": "开盘·高波动", "segment": "open30", "vol": "high"}
    blocked = build_auto_tune_deltas(
        missed_by_context={"open30|high": 5},
        current_context=ctx,
        allow_miss_loosen=False,
    )
    assert blocked["miss_mult"] == 1.0
    assert "NP未过线" in blocked["note"]

    opened = build_auto_tune_deltas(
        missed_by_context={"open30|high": 5},
        current_context=ctx,
        allow_miss_loosen=True,
    )
    assert opened["miss_mult"] < 1.0
    assert "略放宽" in opened["note"]


def test_historical_enrich_uses_frozen_np_not_today_quote():
    rows = [
        {
            "code": "600001",
            "trade_date": "2026-10-10",
            "signal_type": "buy",
            "price": 10.0,
            "payload": {
                "np": {"np": True, "day_low": 10.2, "day_close": 10.8, "plan": 10.0},
                "stop_price": 9.0,
                "chase_price": 11.0,
                "wait_price": 10.0,
            },
        },
        {
            "code": "600002",
            "trade_date": "2026-10-10",
            "signal_type": "buy",
            "price": 10.0,
            "payload": {},  # no freeze → no historical miss mark
        },
    ]
    # Today's quote is far above plan — must NOT create miss for row without freeze.
    quotes = {
        "600001": {"price": 20.0, "low": 19.0, "pct": 5.0},
        "600002": {"price": 20.0, "low": 19.0, "pct": 5.0},
    }
    out = enrich_signals_with_live_marks(rows, quotes, today="2026-11-03")
    assert "miss_pullback" in out[0]["price_flags"]
    assert out[0]["above_plan_pct"] is not None
    assert out[0].get("live_last") is None  # not today's 20
    assert out[0]["day_close"] == 10.8  # frozen close labeled separately
    assert "miss_pullback" not in out[1]["price_flags"]
    assert out[1].get("live_last") is None
    assert out[1].get("day_close") is None
    # Historical must not use today's stop/chase live hits.
    assert "stop_hit" not in out[0]["price_flags"]
    assert "chase_hit" not in out[0]["price_flags"]


def test_same_day_enrich_still_uses_live_quote():
    rows = [
        {
            "code": "600001",
            "trade_date": "2026-11-03",
            "signal_type": "buy",
            "price": 10.0,
            "payload": {"stop_price": 9.0, "chase_price": 12.0, "wait_price": 9.8},
        }
    ]
    quotes = {"600001": {"price": 10.5, "low": 10.2, "pct": 1.0}}
    out = enrich_signals_with_live_marks(rows, quotes, today="2026-11-03")
    assert "miss_pullback" in out[0]["price_flags"]
