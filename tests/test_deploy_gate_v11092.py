"""Deploy restart quiet windows (v1.10.92 / lunch apply v1.10.93)."""

from __future__ import annotations

from datetime import datetime

from market_desk.deploy_gate import (
    DEPLOY_QUIET_AM,
    DEPLOY_QUIET_END_MIN,
    DEPLOY_QUIET_PM,
    DEPLOY_QUIET_START_MIN,
    next_deferred_apply_label,
    should_defer_deploy_restart,
)


def test_defer_morning_and_afternoon_sessions() -> None:
    """Continuous-session hours must defer live restart."""
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 9, 0, 0)) is True
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 11, 0, 0)) is True
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 12, 29, 0)) is True
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 13, 0, 0)) is True
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 14, 27, 0)) is True
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 15, 29, 59)) is True


def test_allow_lunch_and_after_close() -> None:
    """Lunch 12:30–13:00 and after 15:30 may restart (12:30 timer applies live)."""
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 12, 30, 0)) is False
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 12, 45, 0)) is False
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 12, 59, 0)) is False
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 15, 30, 0)) is False
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 15, 35, 0)) is False
    assert should_defer_deploy_restart(datetime(2026, 11, 10, 8, 59, 0)) is False


def test_allow_weekend_and_holiday() -> None:
    """Non-sessions never defer (weekend / configured holiday)."""
    assert should_defer_deploy_restart(datetime(2026, 11, 8, 10, 0, 0)) is False  # Sunday
    assert should_defer_deploy_restart(datetime(2026, 10, 1, 10, 0, 0)) is False  # holiday


def test_quiet_bounds_and_next_apply_slot() -> None:
    """Documented split windows + next apply labels stay stable."""
    assert DEPLOY_QUIET_START_MIN == 9 * 60
    assert DEPLOY_QUIET_END_MIN == 15 * 60 + 30
    assert DEPLOY_QUIET_AM == (9 * 60, 12 * 60 + 30)
    assert DEPLOY_QUIET_PM == (13 * 60, 15 * 60 + 30)
    assert next_deferred_apply_label(datetime(2026, 11, 10, 10, 0, 0)) == "12:30 Asia/Shanghai"
    assert next_deferred_apply_label(datetime(2026, 11, 10, 14, 0, 0)) == "15:35 Asia/Shanghai"
