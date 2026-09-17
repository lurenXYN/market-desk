"""Session / day P&L helpers for sold lots."""

from __future__ import annotations

from market_desk.config import TRADE_FEE_CNY
from market_desk.verdict import (
    decorate_positions,
    position_day_anchor,
    quote_prev_close,
    session_sell_realized,
    session_trade_fees,
)


def test_quote_prev_close_recovers_from_pct() -> None:
    prev = quote_prev_close({"pct": 2.0}, last=102.0)
    assert prev is not None
    assert abs(prev - 100.0) < 1e-6


def test_day_anchor_bought_today_uses_buy() -> None:
    assert position_day_anchor(10.0, buy_day="2026-09-17", trade_day="2026-09-17", prev_close=9.5) == 10.0


def test_day_anchor_overnight_uses_prev() -> None:
    assert position_day_anchor(10.0, buy_day="2026-09-16", trade_day="2026-09-17", prev_close=12.0) == 12.0


def test_trade_fees_buy_and_sell_each_once() -> None:
    assert session_trade_fees(bought_today=True, sold_today=False) == TRADE_FEE_CNY
    assert session_trade_fees(bought_today=False, sold_today=True) == TRADE_FEE_CNY
    assert session_trade_fees(bought_today=True, sold_today=True) == TRADE_FEE_CNY * 2


def test_session_sell_realized_overnight_not_total() -> None:
    # Bought 10, 昨收 12, sell 12.5 × 100 → day = 50 − sell fee, not (12.5−10)×100.
    got = session_sell_realized(
        12.5,
        100,
        buy_price=10.0,
        buy_day="2026-09-01",
        trade_day="2026-09-17",
        prev_close=12.0,
        fee=TRADE_FEE_CNY,
    )
    assert got == round(50.0 - TRADE_FEE_CNY, 2)


def test_session_sell_realized_bought_today_vs_buy() -> None:
    got = session_sell_realized(
        10.8,
        100,
        buy_price=10.0,
        buy_day="2026-09-17",
        trade_day="2026-09-17",
        prev_close=9.5,
        fee=5.0,
    )
    assert got == 75.0  # (10.8-10)*100 - sell fee 5


def test_decorate_closed_day_pnl_uses_prev_not_cost() -> None:
    rows = [
        {
            "id": 1,
            "code": "600000",
            "name": "测试",
            "buy_price": 10.0,
            "qty": 0,
            "last_buy_date": "2026-09-01",
            "closed_date": "2026-09-17",
            "last_sell_date": "2026-09-17",
            "last_sell_price": 12.5,
            "day_sold_qty": 100,
            # Legacy stored total vs cost — must be ignored by decorate.
            "day_realized_pnl": 250.0,
        }
    ]
    quotes = {"600000": {"price": 12.5, "prev": 12.0, "pct": 4.17}}
    out = decorate_positions(rows, quotes, trade_date="2026-09-17")
    assert len(out) == 1
    row = out[0]
    assert row["closed"] is True
    assert row["trade_fee"] == TRADE_FEE_CNY
    assert row["day_pnl"] == round(50.0 - TRADE_FEE_CNY, 2)
    assert row["day_realized_pnl"] == row["day_pnl"]
    # Floating / total vs cost still available, minus sell fee only (buy was prior day).
    assert row["pnl"] == round(250.0 - TRADE_FEE_CNY, 2)


def test_decorate_same_day_round_trip_charges_buy_and_sell_fee() -> None:
    rows = [
        {
            "id": 2,
            "code": "600001",
            "name": "今买今卖",
            "buy_price": 10.0,
            "qty": 0,
            "last_buy_date": "2026-09-17",
            "closed_date": "2026-09-17",
            "last_sell_date": "2026-09-17",
            "last_sell_price": 10.8,
            "day_sold_qty": 100,
            "day_realized_pnl": 80.0,
        }
    ]
    quotes = {"600001": {"price": 10.8, "prev": 9.5, "pct": 13.68}}
    out = decorate_positions(rows, quotes, trade_date="2026-09-17")
    row = out[0]
    # (10.8-10)*100 - sell 5 - buy 5 = 70
    assert row["trade_fee"] == TRADE_FEE_CNY * 2
    assert row["day_pnl"] == 70.0
    assert row["day_realized_pnl"] == 70.0
    assert row["pnl"] == 70.0


def test_decorate_open_bought_today_subtracts_buy_fee() -> None:
    rows = [
        {
            "id": 3,
            "code": "600002",
            "name": "今买持仓",
            "buy_price": 10.0,
            "qty": 100,
            "last_buy_date": "2026-09-17",
            "closed_date": None,
            "day_sold_qty": 0,
            "day_realized_pnl": 0,
        }
    ]
    quotes = {"600002": {"price": 10.5, "prev": 9.8, "pct": 7.14}}
    out = decorate_positions(rows, quotes, trade_date="2026-09-17")
    row = out[0]
    assert row["trade_fee"] == TRADE_FEE_CNY
    assert row["day_pnl"] == round(50.0 - TRADE_FEE_CNY, 2)  # mtm 50 − buy fee
    assert row["pnl"] == round(50.0 - TRADE_FEE_CNY, 2)
