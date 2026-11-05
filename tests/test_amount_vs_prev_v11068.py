"""Market amount vs previous session (放量/缩量 glance line)."""

from __future__ import annotations

from market_desk.sentiment import enrich_market_context


def test_amount_vs_prev_fangliang():
    m = enrich_market_context(
        {"amount_yi": 8000.0, "zt": 40, "avg_explode": 0, "late_seal_rate": 0, "big_drop": 0},
        indices=[
            {"code": "000001", "name": "上证指数", "amount": 4500e8, "pct": 0.5},
            {"code": "399001", "name": "深证成指", "amount": 3500e8, "pct": 0.8},
        ],
        history=[
            {"trade_date": "2026-11-04", "amount_yi": 5000.0},
            {"trade_date": "2026-11-03", "amount_yi": 4800.0},
        ],
    )
    assert m["turnover_yi"] == 8000.0
    assert m["amount_yi"] == 8000.0
    assert m["amount_prev_yi"] == 5000.0
    assert m["amount_delta_yi"] == 3000.0
    assert m["amount_chg_pct"] == 60.0
    assert m["amount_tone"] == "放量"


def test_amount_vs_prev_suoliang_without_indices():
    m = enrich_market_context(
        {"amount_yi": 4000.0, "zt": 20, "avg_explode": 0, "late_seal_rate": 0, "big_drop": 0},
        indices=[],
        history=[{"trade_date": "2026-11-04", "amount_yi": 8000.0}],
    )
    assert m.get("turnover_yi") is None
    assert m["amount_tone"] == "缩量"
    assert m["amount_chg_pct"] == -50.0
    assert m["amount_delta_yi"] == -4000.0
