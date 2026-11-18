"""Session progress and intraday turnover extrapolation."""

from __future__ import annotations

from datetime import datetime

import pytest

from market_desk import volume_pace as vp

CURVE = [
    {"time": "09:30", "cum": 1e8},
    {"time": "09:45", "cum": 1.0e9},
    {"time": "10:00", "cum": 2.2e9},
    {"time": "10:30", "cum": 3.5e9},
    {"time": "11:30", "cum": 5.2e9},
    {"time": "13:30", "cum": 6.3e9},
    {"time": "14:30", "cum": 8.4e9},
    {"time": "15:00", "cum": 10.0e9},
]


def test_parse_clock_variants():
    assert vp.parse_clock("09:35") == 575
    assert vp.parse_clock("0935") == 575
    assert vp.parse_clock(datetime(2026, 11, 18, 10, 5)) == 605
    assert vp.parse_clock(600) == 600
    assert vp.parse_clock("") is None


@pytest.mark.parametrize(
    "clock,expected",
    [
        ((9, 20), 0.0),
        ((9, 30), 0.0),
        ((10, 30), 60.0),
        ((11, 30), 120.0),
        ((12, 15), 120.0),
        ((13, 0), 120.0),
        ((14, 0), 180.0),
        ((15, 0), 240.0),
        ((15, 30), 240.0),
    ],
)
def test_session_minutes(clock, expected):
    assert vp.session_minutes(datetime(2026, 11, 18, *clock)) == expected


def test_linear_progress_is_clock_based():
    assert vp.linear_progress(datetime(2026, 11, 18, 10, 45)) == pytest.approx(0.3125)
    assert vp.linear_progress(datetime(2026, 11, 18, 16, 0)) == 1.0


def test_prior_progress_uses_last_point_at_or_before_now():
    # 10:25 falls between the 10:00 (22%) and 10:30 (35%) prints.
    got = vp.prior_progress(CURVE, datetime(2026, 11, 18, 10, 25))
    assert got == pytest.approx(0.22)
    assert vp.prior_progress(CURVE, datetime(2026, 11, 18, 10, 30)) == pytest.approx(0.35)
    assert vp.prior_progress(CURVE, datetime(2026, 11, 18, 15, 0)) == pytest.approx(1.0)


def test_prior_progress_rejects_unusable_curves():
    assert vp.prior_progress([], datetime(2026, 11, 18, 10, 0)) is None
    assert vp.prior_progress(CURVE[:3], datetime(2026, 11, 18, 10, 0)) is None
    assert vp.prior_progress(CURVE, datetime(2026, 11, 18, 9, 20)) is None
    zeroed = [{"time": t, "cum": 0.0} for t in ("09:30", "10:00", "11:30", "14:00", "15:00")]
    assert vp.prior_progress(zeroed, datetime(2026, 11, 18, 10, 0)) is None


def test_full_day_estimate():
    assert vp.full_day_estimate(3000.0, 0.35) == pytest.approx(3000.0 / 0.35)
    assert vp.full_day_estimate(3000.0, 1.0) == 3000.0
    # Too early to extrapolate: the first prints can imply any full-day number.
    assert vp.full_day_estimate(3000.0, 0.01) is None
    assert vp.full_day_estimate(0.0, 0.5) is None
    assert vp.full_day_estimate(3000.0, None) is None


def test_linear_proration_overstates_a_morning_sample():
    """The reason prev-shape is preferred: linear assumes a flat intraday profile."""
    cum, actual_share = 5132.0, 0.35
    linear = vp.linear_progress(datetime(2026, 11, 18, 10, 25))
    assert vp.full_day_estimate(cum, linear) > vp.full_day_estimate(cum, actual_share)
    assert vp.full_day_estimate(cum, linear) / vp.full_day_estimate(cum, actual_share) > 1.4


def test_pick_progress_prefers_prior_curve_then_linear():
    now = datetime(2026, 11, 18, 10, 25)
    assert vp.pick_progress(prev_curve=CURVE, now=now) == (pytest.approx(0.22), "prev")
    assert vp.pick_progress(prev_curve=None, now=now) == (pytest.approx(0.2292), "linear")
    assert vp.pick_progress(prev_curve=None, now=datetime(2026, 11, 18, 9, 10)) == (None, "none")