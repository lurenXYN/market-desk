"""Deploy restart quiet window (v1.10.92)."""

from __future__ import annotations

from datetime import datetime

from market_desk.deploy_gate import (
    DEPLOY_QUIET_END_MIN,
    DEPLOY_QUIET_START_MIN,
    should_defer_deploy_restart,
)


def test_defer_on_trading_day_inside_quiet_window() -> None:
    """Mid-session Tuesday must defer live restart."""
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 14, 27, 0)) is True
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 9, 0, 0)) is True
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 15, 29, 59)) is True


def test_allow_after_quiet_end_and_before_open() -> None:
    """After 15:30 or before 09:00 on a session day may restart."""
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 15, 30, 0)) is False
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 15, 35, 0)) is False
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 8, 59, 0)) is False


def test_allow_weekend_and_holiday() -> None:
    """Non-sessions never defer (weekend / configured holiday)."""
    assert should_defer_deploy_restart(datetime(2026, 11, 8, 10, 0, 0)) is False  # Sunday
    assert should_defer_deploy_restart(datetime(2026, 10, 1, 10, 0, 0)) is False  # holiday


def test_quiet_bounds_match_product_window() -> None:
    """Keep the documented 09:00–15:30 Shanghai window stable."""
    assert DEPLOY_QUIET_START_MIN == 9 * 60
    assert DEPLOY_QUIET_END_MIN == 15 * 60 + 30
