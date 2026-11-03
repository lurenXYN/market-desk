"""Size-heat boost vs consec-loss sample floors; exec haircut n>=8."""

from __future__ import annotations

from market_desk.adapt.sizing import build_exec_size_bias, build_size_heat


def _buy(*, i: int, win: bool, d3: float) -> dict:
    return {
        "id": i,
        "signal_type": "buy",
        "traded": 1,
        "skipped": 0,
        "outcome_label": "三日红" if win else "三日绿",
        "outcome_day3_pct": d3,
        "signal_at": f"2026-10-01 10:{i:02d}:00",
        "signaled_at": f"2026-10-01 10:{i:02d}:00",
    }


def _fill(*, i: int, fill: float) -> dict:
    return {
        "id": i,
        "signal_type": "buy",
        "traded": 1,
        "fill_price": fill,
        "wait_price": 10.0,
        "chase_price": 10.5,
        "plan_price": 10.2,
        "signaled_at": f"2026-10-01 10:{i:02d}:00",
    }


def test_heat_consec_loss_fires_at_five() -> None:
    rows = [_buy(i=i, win=False, d3=-1.0) for i in range(5)]
    out = build_size_heat(rows, use_cache=False)
    assert out["ok"] is True
    assert out["n"] == 5
    assert out["consec_loss"] == 5
    assert out["size_mult"] == 0.25


def test_heat_boost_needs_eight() -> None:
    thin = [_buy(i=i, win=True, d3=3.0) for i in range(7)]
    thin_out = build_size_heat(thin, use_cache=False)
    assert thin_out["ok"] is True
    assert thin_out["n"] == 7
    assert thin_out["size_mult"] == 1.0

    thick = [_buy(i=i, win=True, d3=3.0) for i in range(8)]
    thick_out = build_size_heat(thick, use_cache=False)
    assert thick_out["n"] == 8
    assert thick_out["size_mult"] > 1.0


def test_exec_haircut_needs_eight() -> None:
    seven = [_fill(i=i, fill=10.6) for i in range(7)]
    seven_out = build_exec_size_bias(seven, use_cache=False)
    assert seven_out["ok"] is False
    assert seven_out["size_mult"] == 1.0

    eight = [_fill(i=i, fill=10.6) for i in range(8)]
    eight_out = build_exec_size_bias(eight, use_cache=False)
    assert eight_out["ok"] is True
    assert eight_out["n"] == 8
    assert eight_out["size_mult"] <= 0.75
