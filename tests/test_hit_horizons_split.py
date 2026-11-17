"""Next-day and 3-day hits are stored separately (they are not the same event)."""

from __future__ import annotations

from market_desk.review.outcome import score_signal_with_closes

DATES = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]


def _row():
    return {"price": 10.0, "trade_date": "2026-11-10", "signal_type": "buy", "code": "600001"}


def test_next_day_win_but_day3_loss_is_visible():
    res = score_signal_with_closes(_row(), [10.0, 10.15, 10.0, 9.9], DATES)
    assert res is not None
    assert res["hit_next"] is True
    assert res["hit_3d"] is False


def test_both_horizons_can_hit():
    res = score_signal_with_closes(_row(), [10.0, 10.2, 10.3, 10.45], DATES)
    assert res is not None
    assert res["hit_next"] is True
    assert res["hit_3d"] is True


def test_both_horizons_can_miss():
    res = score_signal_with_closes(_row(), [10.0, 9.9, 9.8, 9.7], DATES)
    assert res is not None
    assert res["hit_next"] is False
    assert res["hit_3d"] is False
