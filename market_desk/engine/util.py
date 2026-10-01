"""Small async / clock helpers shared by the desk engine."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
import httpx
from market_desk.calendar import is_trading_day
from market_desk.settings import setting

log = logging.getLogger("market_desk")


def _short_exc(exc: BaseException) -> str:
    """Compact HTTP/source errors for health tips (drop query strings / docs links)."""
    if isinstance(exc, httpx.HTTPStatusError):
        req = getattr(exc, "request", None)
        host = getattr(getattr(req, "url", None), "host", None) or "?"
        code = getattr(getattr(exc, "response", None), "status_code", "?")
        return f"{code} {host}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    text = str(exc)
    # Strip long URLs / MDN footnotes from httpx messages.
    if "For more information check:" in text:
        text = text.split("For more information check:", 1)[0].strip()
    if "url '" in text:
        # Keep status phrase, drop full URL body.
        head = text.split("url '", 1)[0].rstrip()
        if head:
            text = head.rstrip(" for").rstrip()
    return text[:160] if len(text) > 160 else text


async def _map_capped(fn, items: list[Any], *, limit: int = 3) -> list[Any]:
    """Run ``await fn(item)`` over items with a concurrency cap (default 3)."""
    if not items:
        return []
    sem = asyncio.Semaphore(max(1, int(limit)))

    async def _one(item: Any) -> Any:
        async with sem:
            return await fn(item)

    return list(await asyncio.gather(*[_one(item) for item in items]))


async def _safe(fn, *args, errors: list[str], label: str):
    from market_desk.engine.core import engine

    try:
        result = await fn(*args)
        try:
            engine._note_source(label, ok=True)
        except Exception:
            pass
        return result
    except Exception as exc:
        short = _short_exc(exc)
        log.warning("%s: %s", label, exc)
        errors.append(f"{label}: {short}")
        msg = str(exc).lower()
        is_to = "timeout" in msg or "timed out" in msg or "time out" in msg
        try:
            engine._note_source(label, ok=False, timeout=is_to)
        except Exception:
            pass
        return []


def _minutes(now: datetime) -> int:
    return now.hour * 60 + now.minute


def _prev_trading_day(now: datetime, *, max_back: int = 15) -> str:
    """Return the last trading date strictly before ``now``'s calendar day."""
    day = now.date()
    for _ in range(max_back):
        day = day - timedelta(days=1)
        if is_trading_day(day):
            return day.isoformat()
    return ""


def _effective_refresh_seconds(now: datetime) -> int:
    """Pick poll cadence: faster in 09:15–09:30 call-auction, else normal."""
    base = int(setting("refresh_seconds", 20))
    auction = int(setting("auction_refresh_seconds", 5) or 0)
    if not auction:
        return max(5, base)
    minutes = _minutes(now)
    if is_trading_day(now) and 9 * 60 + 15 <= minutes < 9 * 60 + 30:
        return max(3, min(auction, base))
    return max(5, base)


def _is_weekday(now: datetime) -> bool:
    """Return True for Monday–Friday in the given local time."""
    return now.weekday() < 5


def _is_session(now: datetime) -> bool:
    """Return True during A-share continuous auction on a trading day."""
    if not is_trading_day(now):
        return False
    minutes = _minutes(now)
    morning = 9 * 60 + 15 <= minutes <= 11 * 60 + 30
    afternoon = 13 * 60 <= minutes <= 15 * 60 + 5
    return morning or afternoon
