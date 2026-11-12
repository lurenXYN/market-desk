"""Deploy restart quiet windows (avoid mid-session service restarts).

Live ``systemctl restart`` during continuous auction can interrupt refresh just
as cards light ``ready``, so ``first_ready_at`` lands after fills close and
paper lots refuse with ``outside_session``.

Policy (Asia/Shanghai, trading days only):
- Quiet (defer): ``[09:00, 12:30)`` and ``[13:00, 15:30)``
- Open for restart: lunch ``[12:30, 13:00)``, before 09:00, and from 15:30 on
- Staged applies: timer at **12:30** (morning backlog) and **15:35** (afternoon)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.calendar import is_trading_day, now_cn

# Inclusive start, exclusive end (minutes from midnight, Asia/Shanghai).
DEPLOY_QUIET_AM = (9 * 60, 12 * 60 + 30)  # 09:00–12:30
DEPLOY_QUIET_PM = (13 * 60, 15 * 60 + 30)  # 13:00–15:30
# Kept for older tests / docs that mention the outer envelope.
DEPLOY_QUIET_START_MIN = DEPLOY_QUIET_AM[0]
DEPLOY_QUIET_END_MIN = DEPLOY_QUIET_PM[1]
DEPLOY_LUNCH_APPLY_MIN = 12 * 60 + 30  # 12:30
DEPLOY_EOD_APPLY_MIN = 15 * 60 + 35  # 15:35


def _minutes_of(clock: datetime) -> int:
    """Return minute-of-day for a naive/aware local wall clock."""
    return int(clock.hour) * 60 + int(clock.minute)


def should_defer_deploy_restart(now: datetime | None = None) -> bool:
    """Return True when a live deploy restart should wait for a quiet gap.

    Args:
        now: Optional Asia/Shanghai wall clock; defaults to ``now_cn()``.

    Returns:
        True on A-share trading days in ``[09:00, 12:30)`` or ``[13:00, 15:30)``.
        Lunch ``[12:30, 13:00)`` is open so the 12:30 timer can apply live.
    """
    clock = now if isinstance(now, datetime) else now_cn()
    if not is_trading_day(clock):
        return False
    mins = _minutes_of(clock)
    am0, am1 = DEPLOY_QUIET_AM
    pm0, pm1 = DEPLOY_QUIET_PM
    return (am0 <= mins < am1) or (pm0 <= mins < pm1)


def next_deferred_apply_label(now: datetime | None = None) -> str:
    """Return the next scheduled apply slot label for logs / pending stamps."""
    clock = now if isinstance(now, datetime) else now_cn()
    mins = _minutes_of(clock)
    if mins < DEPLOY_LUNCH_APPLY_MIN:
        return "12:30 Asia/Shanghai"
    return "15:35 Asia/Shanghai"


def deploy_quiet_window_label() -> dict[str, Any]:
    """Return a small diagnostic dict for logs / health tips."""
    clock = now_cn()
    return {
        "tz": "Asia/Shanghai",
        "now": clock.strftime("%Y-%m-%d %H:%M:%S"),
        "trading_day": bool(is_trading_day(clock)),
        "defer": should_defer_deploy_restart(clock),
        "quiet_am": "09:00-12:30",
        "quiet_pm": "13:00-15:30",
        "lunch_open": "12:30-13:00",
        "next_apply": next_deferred_apply_label(clock),
    }
