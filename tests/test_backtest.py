"""Unit tests for daily OHLC simulated fills (no network)."""

from __future__ import annotations

from market_desk.backtest import (
    _summarize_backtest,
    simulate_buy_fill,
    simulate_sell_fill,
)
from market_desk.review import score_signal_with_closes


def test_buy_fill_on_wait_touch() -> None:
    bars = {
        "2026-09-17": {"open": 10.5, "high": 10.8, "low": 9.9, "close": 10.2},
    }
    sim = simulate_buy_fill(
        trade_date="2026-09-17",
        bars_by_day=bars,
        wait=10.0,
        plan=10.0,
        chase=10.6,
        mode="wait",
        vol_min_ratio=0,
        slip_pct=0,
        gap_pct=0,
    )
    assert sim is not None
    assert sim["filled"] is True
    assert sim["fill_price"] == 10.0
    assert sim["fill_date"] == "2026-09-17"


def test_buy_skip_when_open_above_chase() -> None:
    bars = {
        "2026-09-17": {"open": 11.0, "high": 11.2, "low": 10.8, "close": 11.0},
        "2026-09-18": {"open": 10.4, "high": 10.5, "low": 9.8, "close": 10.0},
    }
    sim = simulate_buy_fill(
        trade_date="2026-09-17",
        bars_by_day=bars,
        wait=10.0,
        plan=10.0,
        chase=10.6,
        mode="wait",
        look_ahead=2,
        vol_min_ratio=0,
        slip_pct=0,
        gap_pct=0,
    )
    assert sim is not None
    assert sim["filled"] is True
    assert sim["fill_date"] == "2026-09-18"


def test_sell_stop_before_take() -> None:
    bars = {
        "2026-09-17": {"open": 10.0, "high": 10.8, "low": 9.4, "close": 9.6},
    }
    sim = simulate_sell_fill(
        trade_date="2026-09-17",
        bars_by_day=bars,
        sell=10.5,
        stop=9.5,
        slip_pct=0,
        gap_pct=0,
    )
    assert sim is not None
    assert sim["filled"] is True
    assert sim["exit_mode"] == "stop"
    assert sim["fill_price"] == 9.5


def test_outcome_reuse_after_sim_fill() -> None:
    dates = ["2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20"]
    closes = [10.0, 10.5, 10.8, 11.0]
    opens = [10.0, 10.2, 10.6, 10.9]
    lows = [9.8, 10.1, 10.4, 10.7]
    highs = [10.2, 10.6, 10.9, 11.2]
    row = {
        "signal_type": "buy",
        "trade_date": "2026-09-17",
        "fill_price": 10.0,
        "price": 10.0,
    }
    out = score_signal_with_closes(
        row, closes, dates, opens=opens, lows=lows, highs=highs
    )
    assert out is not None
    assert out["outcome_label"] == "次日红"
    assert out["outcome_day1_pct"] == 5.0


def test_summarize_hit_rate() -> None:
    items = [
        {
            "side": "buy",
            "sim_filled": True,
            "outcome_label": "次日红",
            "outcome_day1_pct": 2.0,
        },
        {
            "side": "buy",
            "sim_filled": True,
            "outcome_label": "次日绿",
            "outcome_day1_pct": -2.0,
        },
        {"side": "buy", "sim_filled": False},
    ]
    s = _summarize_backtest(items)
    assert s["buy_n"] == 3
    assert s["buy_filled_n"] == 2
    assert s["buy_hit_rate"] == 50.0


def test_validate_span_cap() -> None:
    from market_desk.backtest import MAX_BACKTEST_SPAN_DAYS, validate_backtest_range

    bad = validate_backtest_range("2025-01-01", "2025-12-31")
    assert isinstance(bad, dict)
    assert bad["ok"] is False
    assert str(MAX_BACKTEST_SPAN_DAYS) in str(bad.get("detail") or "")
    ok = validate_backtest_range("2026-09-01", "2026-09-18")
    assert ok == ("2026-09-01", "2026-09-18")


def test_buy_liquidity_skips_thin_day() -> None:
    bars = {
        "2026-09-10": {"open": 10.0, "high": 10.2, "low": 9.9, "close": 10.0, "volume": 1000},
        "2026-09-11": {"open": 10.0, "high": 10.1, "low": 9.95, "close": 10.0, "volume": 1100},
        "2026-09-12": {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "volume": 1050},
        "2026-09-13": {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "volume": 1200},
        "2026-09-14": {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "volume": 1150},
        # Thin needle day — price touches wait but volume tiny.
        "2026-09-17": {"open": 10.2, "high": 10.3, "low": 9.9, "close": 10.1, "volume": 50},
        "2026-09-18": {"open": 10.1, "high": 10.2, "low": 9.85, "close": 10.0, "volume": 1100},
    }
    sim = simulate_buy_fill(
        trade_date="2026-09-17",
        bars_by_day=bars,
        wait=10.0,
        plan=10.0,
        chase=10.6,
        mode="wait",
        look_ahead=2,
        vol_min_ratio=0.4,
        slip_pct=0,
        gap_pct=0,
    )
    assert sim is not None
    assert sim["filled"] is True
    assert sim["fill_date"] == "2026-09-18"
    assert int(sim.get("liq_skips") or 0) >= 1


def test_buy_slippage_worsens_fill() -> None:
    bars = {
        "2026-09-17": {"open": 10.5, "high": 10.8, "low": 9.9, "close": 10.2, "volume": 1000},
    }
    sim = simulate_buy_fill(
        trade_date="2026-09-17",
        bars_by_day=bars,
        wait=10.0,
        plan=10.0,
        chase=10.6,
        mode="wait",
        vol_min_ratio=0,
        slip_pct=1.0,
        gap_pct=0,
    )
    assert sim is not None
    assert sim["filled"] is True
    assert abs(float(sim["fill_price"]) - 10.1) < 1e-6


def test_buy_gap_down_fills_at_open() -> None:
    bars = {
        "2026-09-16": {"open": 10.5, "high": 10.6, "low": 10.4, "close": 10.5, "volume": 1000},
        "2026-09-17": {"open": 9.8, "high": 10.0, "low": 9.7, "close": 9.9, "volume": 1200},
    }
    sim = simulate_buy_fill(
        trade_date="2026-09-17",
        bars_by_day=bars,
        wait=10.0,
        plan=10.0,
        chase=10.6,
        mode="wait",
        vol_min_ratio=0,
        slip_pct=0,
        gap_pct=1.0,
    )
    assert sim is not None
    assert sim["filled"] is True
    assert sim["fill_price"] == 9.8
    assert sim.get("gap_filled") is True
    assert "缺口" in str(sim.get("note") or "")


def test_sell_gap_through_stop() -> None:
    bars = {
        "2026-09-16": {"open": 10.0, "high": 10.2, "low": 9.9, "close": 10.0, "volume": 1000},
        "2026-09-17": {"open": 9.4, "high": 9.6, "low": 9.3, "close": 9.5, "volume": 1100},
    }
    sim = simulate_sell_fill(
        trade_date="2026-09-17",
        bars_by_day=bars,
        sell=10.5,
        stop=9.5,
        slip_pct=0,
        gap_pct=1.0,
    )
    assert sim is not None
    assert sim["filled"] is True
    assert sim["exit_mode"] == "stop"
    assert sim["fill_price"] == 9.4
    assert sim.get("gap_filled") is True
