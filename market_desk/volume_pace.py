"""Session progress and full-day turnover extrapolation for intraday volume reads.

Comparing the running turnover with *full-day* history is structurally biased: at
10:25 only ~23% of the clock has passed but ~35–40% of the day's turnover is often
done, so a raw percentile reads "record low volume" every morning and the thin-volume
gate fires on a sample that has not formed yet.

Two bases, in order of preference:

``prev``
    Yesterday's cumulative curve gives the *share of the day already traded at this
    clock time*. Turnover is U-shaped (heavy open, heavy close), so this is the only
    honest way to compare 10:25 against a full session.
``linear``
    Elapsed trading minutes / 240, used only when no prior curve is available. It
    overstates the morning (the open is heavier than average) and is reported as such.

Callers pass ``progress`` to :func:`market_desk.sentiment.enrich_market_context` so the
percentile is computed on a comparable full-day basis.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Sequence

TRADING_MINUTES = 240.0
# Below this share of the day the extrapolation is noise: a few early prints can
# imply any full-day number. Report "not ready" instead of a made-up pace.
MIN_PROGRESS = 0.05
# Morning/afternoon bounds in minutes from midnight.
_OPEN_AM, _CLOSE_AM = 9 * 60 + 30, 11 * 60 + 30
_OPEN_PM, _CLOSE_PM = 13 * 60, 15 * 60


def parse_clock(value: Any) -> int | None:
    """Parse ``"09:35"`` / ``"0935"`` / ``datetime`` / minute-count into minutes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if isinstance(value, (int, float)):
        return int(value)
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 3:
        return None
    if len(digits) >= 4:
        hh, mm = int(digits[:2]), int(digits[2:4])
    else:
        hh, mm = int(digits[0]), int(digits[1:3])
    if hh > 23 or mm > 59:
        return None
    return hh * 60 + mm


def session_minutes(now: datetime) -> float:
    """Trading minutes elapsed in ``now``'s session (0 at 09:30, 240 after 15:00)."""
    m = now.hour * 60 + now.minute
    if m <= _OPEN_AM:
        return 0.0
    if m < _CLOSE_AM:
        return float(m - _OPEN_AM)
    if m < _OPEN_PM:
        return _CLOSE_AM - _OPEN_AM
    if m < _CLOSE_PM:
        return float(_CLOSE_AM - _OPEN_AM + (m - _OPEN_PM))
    return TRADING_MINUTES


def linear_progress(now: datetime) -> float:
    """Share of the session elapsed by the clock, ignoring the intraday volume shape."""
    return round(session_minutes(now) / TRADING_MINUTES, 4)


def _curve_points(curve: Sequence[Any] | None) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for item in curve or []:
        if isinstance(item, dict):
            minute = parse_clock(item.get("time") or item.get("t") or item.get("minute"))
            value = item.get("cum", item.get("amount"))
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            minute = parse_clock(item[0])
            value = item[1]
        else:
            continue
        if minute is None or value is None:
            continue
        try:
            out.append((minute, float(value)))
        except (TypeError, ValueError):
            continue
    out.sort()
    return out


def prior_progress(
    curve: Sequence[Any] | None,
    now: datetime,
    *,
    min_points: int = 5,
) -> float | None:
    """Share of the prior session's turnover already done at ``now``'s clock time.

    ``curve`` must be a *complete* prior session as cumulative turnover, e.g.
    ``[{"time": "09:30", "cum": 12.5e8}, ...]`` through 15:00. Returns ``None`` when the
    curve is too short, does not reach ``now``, or carries no turnover.
    """
    points = _curve_points(curve)
    if len(points) < min_points:
        return None
    total = points[-1][1]
    if total <= 0:
        return None
    now_minute = parse_clock(now)
    if now_minute is None:
        return None
    cum = 0.0
    for minute, value in points:
        if minute > now_minute:
            break
        cum = value
    if cum <= 0:
        return None
    return round(max(0.0, min(1.0, cum / total)), 4)


def full_day_estimate(cum: float, progress: float | None, *, min_progress: float = MIN_PROGRESS) -> float | None:
    """Extrapolate ``cum`` to a full session using ``progress`` (share already traded)."""
    try:
        value = float(cum)
        share = float(progress) if progress is not None else 0.0
    except (TypeError, ValueError):
        return None
    if value <= 0 or share < min_progress:
        return None
    if share >= 1.0:
        return value
    return value / share


def pick_progress(
    *,
    prev_curve: Sequence[Any] | None,
    now: datetime,
) -> tuple[float | None, str]:
    """Return ``(progress, basis)`` — prior-session shape first, linear as fallback."""
    prior = prior_progress(prev_curve, now)
    if prior is not None and prior > 0:
        return prior, "prev"
    linear = linear_progress(now)
    return (linear, "linear") if linear > 0 else (None, "none")