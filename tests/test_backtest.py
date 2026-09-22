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


def test_plan_price_distinct_from_wait() -> None:
    from market_desk.backtest import _plan_buy_prices

    row = {
        "price": 10.5,
        "payload": {
            "plan_price": 10.5,
            "wait_price": 10.0,
            "chase_price": 11.0,
        },
    }
    band = _plan_buy_prices(row)
    assert band["plan"] == 10.5
    assert band["wait"] == 10.0


def test_sim_exec_in_summary() -> None:
    items = [
        {
            "side": "buy",
            "kind": "stock",
            "sim_filled": True,
            "sim_fill_price": 10.2,
            "plan_price": 10.0,
            "wait_price": 9.8,
            "chase_price": 10.5,
            "sim_exec": "in_band",
            "outcome_label": "次日红",
            "outcome_day1_pct": 2.0,
        },
        {
            "side": "buy",
            "kind": "stock",
            "sim_filled": True,
            "sim_fill_price": 10.6,
            "plan_price": 10.0,
            "wait_price": 9.8,
            "chase_price": 10.5,
            "sim_exec": "chase",
            "outcome_label": "次日绿",
            "outcome_day1_pct": -1.0,
        },
    ]
    s = _summarize_backtest(items)
    assert s["sim_exec"]["scored_n"] == 2
    assert s["sim_exec"]["chase_n"] == 1
    assert s["sim_exec"]["in_band_n"] == 1
    assert s["sim_exec"]["score"] is not None


def test_demote_preserves_plan_price() -> None:
    from market_desk.verdict import _demote_buy_to_wait

    item = {
        "buy_price": 10.5,
        "plan_price": 10.5,
        "wait_price": 10.0,
        "chase_price": 11.0,
    }
    out = _demote_buy_to_wait(item)
    assert out["plan_price"] == 10.5
    assert out["buy_price"] == 10.0


def test_filled_sell_no_fill_is_empty() -> None:
    from market_desk.review import score_signal_with_closes

    sig = {
        "signal_type": "sell",
        "trade_date": "2026-09-10",
        "price": 10.0,
        "fill_price": None,
    }
    out = score_signal_with_closes(
        sig,
        closes=[10.0, 10.2],
        dates=["2026-09-10", "2026-09-11"],
        opens=[10.0, 10.1],
        highs=[10.1, 10.3],
        lows=[9.9, 10.0],
        standard="filled",
    )
    assert out is not None
    assert out.get("outcome_label") == "无成交"


def test_minute_fidelity_chase_before_plan_skips() -> None:
    """Intraday: touch chase first → skip day0 fill even if plan also touched."""
    bars = {
        "2026-09-17": {"open": 10.2, "high": 10.8, "low": 9.9, "close": 10.1},
        "2026-09-18": {"open": 10.0, "high": 10.2, "low": 9.7, "close": 9.9},
    }
    minutes = [
        {"price": 10.3},
        {"price": 10.7},  # chase first
        {"price": 10.0},  # then plan
    ]
    sim = simulate_buy_fill(
        trade_date="2026-09-17",
        bars_by_day=bars,
        wait=10.0,
        plan=10.0,
        chase=10.6,
        mode="plan",
        look_ahead=2,
        vol_min_ratio=0,
        slip_pct=0,
        gap_pct=0,
        minutes=minutes,
        fidelity="minute",
    )
    assert sim is not None
    # Day0 skipped via minute path; day1 can still fill on daily.
    assert sim["filled"] is True
    assert sim["fill_date"] == "2026-09-18"
    assert sim.get("fidelity") == "daily"


def test_minute_fidelity_plan_before_chase_fills() -> None:
    bars = {
        "2026-09-17": {"open": 10.2, "high": 10.8, "low": 9.9, "close": 10.1},
    }
    minutes = [
        {"price": 10.1},
        {"price": 9.95},  # plan first
        {"price": 10.7},
    ]
    sim = simulate_buy_fill(
        trade_date="2026-09-17",
        bars_by_day=bars,
        wait=10.0,
        plan=10.0,
        chase=10.6,
        mode="plan",
        vol_min_ratio=0,
        slip_pct=0,
        gap_pct=0,
        minutes=minutes,
        fidelity="minute",
    )
    assert sim is not None
    assert sim["filled"] is True
    assert sim["fill_date"] == "2026-09-17"
    assert sim.get("fidelity") == "minute"
    assert "分时" in str(sim.get("note") or "")


def test_minute_fidelity_ambiguous_day0_without_minutes() -> None:
    """Without minutes, ambiguous day0 (high≥chase and low≤plan) is skipped."""
    bars = {
        "2026-09-17": {"open": 10.2, "high": 10.8, "low": 9.9, "close": 10.1},
        "2026-09-18": {"open": 10.0, "high": 10.1, "low": 9.8, "close": 9.9},
    }
    sim = simulate_buy_fill(
        trade_date="2026-09-17",
        bars_by_day=bars,
        wait=10.0,
        plan=10.0,
        chase=10.6,
        mode="plan",
        look_ahead=2,
        vol_min_ratio=0,
        slip_pct=0,
        gap_pct=0,
        minutes=None,
        fidelity="minute",
    )
    assert sim is not None
    assert sim["filled"] is True
    assert sim["fill_date"] == "2026-09-18"


def test_validate_backtest_async_span() -> None:
    from market_desk.backtest import (
        MAX_BACKTEST_ASYNC_SPAN_DAYS,
        MAX_BACKTEST_SPAN_DAYS,
        validate_backtest_range,
    )

    # ~121 calendar days > sync 90, still within async 180.
    ok = validate_backtest_range("2026-01-01", "2026-05-01", max_span=MAX_BACKTEST_SPAN_DAYS)
    assert isinstance(ok, dict) and ok.get("ok") is False
    wide = validate_backtest_range(
        "2026-01-01", "2026-05-01", max_span=MAX_BACKTEST_ASYNC_SPAN_DAYS
    )
    assert isinstance(wide, tuple)
    assert wide[0] == "2026-01-01"


def test_adapt_same_day_plan_remap_with_bars() -> None:
    from market_desk.adapt import (
        _remap_rows_for_adapt_standard,
        set_adapt_bars,
    )

    dates = ["2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20"]
    closes = [10.5, 10.8, 11.0, 11.2]
    ohlc = {
        "open": [10.4, 10.6, 10.9, 11.1],
        "low": [10.0, 10.5, 10.8, 11.0],  # day0 low touches plan 10.2
        "high": [10.6, 10.9, 11.1, 11.3],
    }
    set_adapt_bars(
        {
            "600000": (dates, closes, ohlc),
        }
    )
    rows = [
        {
            "code": "600000",
            "name": "浦发",
            "signal_type": "buy",
            "trade_date": "2026-09-17",
            "price": 10.2,
            "plan_price": 10.2,
            "wait_price": 10.0,
            "outcome_label": "次日绿",  # classic placeholder
            "outcome_day1_pct": -1.0,
        }
    ]
    remapped, n_hit = _remap_rows_for_adapt_standard(rows, "same_day_plan")
    assert n_hit >= 1
    assert remapped[0].get("outcome_standard") == "same_day_plan"
    assert remapped[0].get("outcome_label")
    set_adapt_bars({})


def test_compare_sim_vs_filled_pairs() -> None:
    from market_desk.backtest import compare_sim_vs_filled

    items = [
        {
            "side": "buy",
            "code": "600000",
            "name": "浦发",
            "trade_date": "2099-01-01",
            "sim_filled": True,
            "sim_fill_price": 10.0,
            "outcome_label": "次日红",
            "outcome_day1_pct": 2.0,
        }
    ]
    out = compare_sim_vs_filled(items, date_from="2099-01-01", date_to="2099-01-01")
    assert out["ok"] is True
    # No traded rows for far-future date → empty pairs, still ok.
    assert out["paired_n"] == 0


def test_serverchan_sell_only_filter() -> None:
    from market_desk.notify import filter_serverchan_alerts

    alerts = [
        ("buy:600000", "可买", "x"),
        ("fly:600000", "将飞", "x"),
        ("sell:stop:600000", "止损", "x"),
        ("sell:trim:600000", "减仓", "x"),
        ("eod:2099-01-01", "收盘", "x"),
    ]
    all_sc = filter_serverchan_alerts(alerts, sell_only=False)
    assert len(all_sc) == 5
    quiet = filter_serverchan_alerts(alerts, sell_only=True)
    keys = [k for k, _, _ in quiet]
    assert "sell:stop:600000" in keys
    assert "eod:2099-01-01" in keys
    assert "buy:600000" not in keys
    assert "sell:trim:600000" not in keys
