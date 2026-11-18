"""Prior-session intraday turnover curve (沪 + 深) for time-normalised volume reads.

Tencent's five-day minute feed returns *cumulative* amount per minute for an index,
which is exactly what is needed to answer "what share of a normal day had already
traded by this clock time". Summing 上证指数 and 深证成指 reproduces the two-market
turnover (their last prints match the daily `market_turnover` figures).

The curve is fetched at most once per session and cached; any failure returns
``None`` so the caller falls back to linear clock progress instead of guessing.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

DAYS_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query?code={sym}"
# 上证指数 + 深证成指: the deep index's turnover equals 深市全量 (verified against
# 深证综指), so the sum is the usual "两市成交额".
SYMBOLS = ("sh000001", "sz399001")
_TTL_SEC = 600.0
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _parse_days(payload: Any, symbol: str) -> dict[str, dict[str, float]]:
    """Map ``YYYYMMDD`` → ``{"HH:MM": cumulative amount}`` for one symbol."""
    node = ((payload or {}).get("data") or {}).get(symbol) or {}
    out: dict[str, dict[str, float]] = {}
    for day in node.get("data") or []:
        if not isinstance(day, dict):
            continue
        raw_date = str(day.get("date") or "")
        if len(raw_date) != 8:
            continue
        points: dict[str, float] = {}
        for row in day.get("data") or []:
            parts = str(row).split()
            if len(parts) < 4 or len(parts[0]) != 4:
                continue
            clock = f"{parts[0][:2]}:{parts[0][2:]}"
            try:
                points[clock] = float(parts[3])
            except (TypeError, ValueError):
                continue
        if points:
            out[raw_date] = points
    return out


def merge_curve(
    sh: dict[str, float] | None,
    sz: dict[str, float] | None,
) -> list[dict[str, Any]]:
    """Combine two cumulative curves on their shared clock stamps (两市口径)."""
    a, b = sh or {}, sz or {}
    times = sorted(set(a) & set(b))
    return [{"time": t, "cum": a[t] + b[t]} for t in times]


async def prior_session_curve(
    client: httpx.AsyncClient,
    today: str,
    *,
    timeout: float = 6.0,
) -> list[dict[str, Any]] | None:
    """Return the most recent complete session before ``today`` as cumulative turnover."""
    day = str(today or "")[:10].replace("-", "")
    if len(day) != 8:
        return None
    hit = _cache.get(day)
    if hit and time.time() - hit[0] < _TTL_SEC:
        return hit[1]

    parsed: dict[str, dict[str, dict[str, float]]] = {}
    for symbol in SYMBOLS:
        try:
            resp = await client.get(
                DAYS_URL.format(sym=symbol), headers=_HEADERS, timeout=timeout
            )
            resp.raise_for_status()
            parsed[symbol] = _parse_days(resp.json(), symbol)
        except Exception as exc:  # noqa: BLE001 - a missing leg must not break the refresh
            # Log so a closed/broken client is visible (used to fail silently → linear).
            log.warning(
                "prior_session_curve leg %s failed: %s: %s",
                symbol,
                type(exc).__name__,
                exc,
            )
            return None

    sh_days, sz_days = parsed.get(SYMBOLS[0]) or {}, parsed.get(SYMBOLS[1]) or {}
    shared = sorted(d for d in set(sh_days) & set(sz_days) if d < day)
    if not shared:
        return None
    curve = merge_curve(sh_days[shared[-1]], sz_days[shared[-1]])
    if len(curve) < 30:
        return None
    _cache[day] = (time.time(), curve)
    return curve


def curve_total(curve: list[dict[str, Any]] | None) -> float | None:
    """Full-session turnover carried by a curve (元), or ``None``."""
    if not curve:
        return None
    try:
        total = float(curve[-1].get("cum") or 0.0)
    except (TypeError, ValueError):
        return None
    return total if total > 0 else None