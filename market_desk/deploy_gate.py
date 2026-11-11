"""Deploy restart quiet window (avoid mid-session service restarts).

Live ``systemctl restart`` during the A-share day can interrupt refresh just as
cards light ``ready``, so ``first_ready_at`` lands after the continuous session
and paper fills refuse with ``outside_session``. Stage code instead and apply
after 15:30 Asia/Shanghai (see ``deploy/remote-update.sh``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.calendar import is_trading_day, now_cn

# Inclusive start, exclusive end (minutes from midnight, Asia/Shanghai).
# 09:00 covers open rush; 15:30 clears the post-close dead zone where a restart
# can stamp first_ready_at after fills are already closed (15:00).
DEPLOY_QUIET_START_MIN = 9 * 60
DEPLOY_QUIET_END_MIN = 15 * 60 + 30


def _minutes_of(clock: datetime) -> int:
    """Return minute-of-day for a naive/aware local wall clock."""
    return int(clock.hour) * 60 + int(clock.minute)


def should_defer_deploy_restart(now: datetime | None = None) -> bool:
    """Return True when a live deploy restart should wait for the quiet window.

    Args:
        now: Optional Asia/Shanghai wall clock; defaults to ``now_cn()``.

    Returns:
        True on A-share trading days while ``[09:00, 15:30)`` Shanghai time.
    """
    clock = now if isinstance(now, datetime) else now_cn()
    if not is_trading_day(clock):
        return False
    mins = _minutes_of(clock)
    return DEPLOY_QUIET_START_MIN <= mins < DEPLOY_QUIET_END_MIN


def deploy_quiet_window_label() -> dict[str, Any]:
    """Return a small diagnostic dict for logs / health tips."""
    clock = now_cn()
    return {
        "tz": "Asia/Shanghai",
        "now": clock.strftime("%Y-%m-%d %H:%M:%S"),
        "trading_day": bool(is_trading_day(clock)),
        "defer": should_defer_deploy_restart(clock),
        "quiet_start": "09:00",
        "quiet_end": "15:30",
    }
