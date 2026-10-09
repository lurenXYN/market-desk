"""Intraday minute trends / bars with Tencent fallback and concurrency cap."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import asyncio
import logging
import time
import httpx
from market_desk import tencent as tencent_client
from market_desk.config import EASTMONEY_UT, HTTP_HEADERS
from market_desk.filters import normalize_code
from market_desk.numbers import num

from market_desk.eastmoney.avail import em_get, recently_blocked
from market_desk.eastmoney.bars import _secid

log = logging.getLogger("market_desk.eastmoney")


def _parse_minute_trends(rows: list[Any]) -> list[dict[str, Any]]:
    """Parse East Money trends2 CSV rows into minute points."""
    out: list[dict[str, Any]] = []
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 3:
            continue
        # Prefer close; fall back to open when a short row appears.
        px = num(parts[2]) if len(parts) > 2 else None
        if px is None:
            px = num(parts[1])
        if px is None:
            continue
        avg = num(parts[7]) if len(parts) > 7 else None
        vol = num(parts[5]) if len(parts) > 5 else None
        amt = num(parts[6]) if len(parts) > 6 else None
        point: dict[str, Any] = {"time": str(parts[0]), "price": float(px)}
        if avg is not None and avg > 0:
            point["avg"] = float(avg)
        if vol is not None and vol >= 0:
            point["volume"] = float(vol)
        if amt is not None and amt >= 0:
            point["amount"] = float(amt)
        out.append(point)
    return out


async def fetch_minute_trends(
    client: httpx.AsyncClient,
    code: str,
) -> list[dict[str, Any]]:
    """Fetch today's minute trend points for an intraday sparkline.

    East Money ``trends2`` CSV (fields2=f51..f58) is currently:
    ``time,open,close,high,low,volume,amount,avg`` — use close as price.

    ``push2.eastmoney.com`` often disconnects on ETFs; try delay/his hosts with a
    short single-shot timeout so fallbacks are not delayed by ``_get_json`` retries.
    Concurrent calls are capped so recommend/position/favorite minutes do not stampede.
    """
    async with _minute_sem():
        return await _fetch_minute_trends_unlocked(client, code)


async def fetch_minute_trends_many(
    client: httpx.AsyncClient,
    codes: list[str],
    *,
    concurrency: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch minute trends for several codes with a shared concurrency cap."""
    uniq: list[str] = []
    seen: set[str] = set()
    for raw in codes:
        c = normalize_code(raw)
        if not c or c in seen:
            continue
        seen.add(c)
        uniq.append(c)
    if not uniq:
        return {}
    # Cap batch fan-out; also respect the process-wide minute semaphore.
    limit = max(1, min(int(concurrency or _MINUTE_CONCURRENCY), _MINUTE_CONCURRENCY))
    local = asyncio.Semaphore(limit)

    async def _one(code: str) -> tuple[str, list[dict[str, Any]]]:
        async with local:
            async with _minute_sem():
                try:
                    return code, await _fetch_minute_trends_unlocked(client, code)
                except Exception:
                    return code, []

    pairs = await asyncio.gather(*[_one(c) for c in uniq])
    return {code: rows for code, rows in pairs}


def _parse_minute_klines(rows: list[Any]) -> list[dict[str, Any]]:
    """Parse East Money 1-minute kline CSV rows into minute points."""
    out: list[dict[str, Any]] = []
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 5:
            continue
        # kline: time,open,close,high,low,volume,amount,...
        px = num(parts[2])
        if px is None or px <= 0:
            continue
        hi = num(parts[3])
        lo = num(parts[4])
        vol = num(parts[5]) if len(parts) > 5 else None
        point: dict[str, Any] = {
            "time": str(parts[0]),
            "price": float(px),
        }
        if hi is not None and hi > 0:
            point["high"] = float(hi)
        if lo is not None and lo > 0:
            point["low"] = float(lo)
        if vol is not None and vol >= 0:
            point["volume"] = float(vol)
        out.append(point)
    return out


async def fetch_minute_bars_for_day(
    client: httpx.AsyncClient,
    code: str,
    trade_date: str,
) -> list[dict[str, Any]]:
    """Fetch one session's 1-minute bars for ``trade_date`` (YYYY-MM-DD).

    Uses East Money history kline ``klt=1``. Today's session falls back to
    ``trends2`` (then Tencent); earlier sessions within the last five fall back
    to Tencent's five-day minute feed.
    """
    c = normalize_code(code)
    day = str(trade_date or "")[:10]
    if not c or len(day) < 10:
        return []
    day_compact = day.replace("-", "")
    today = datetime.now().strftime("%Y-%m-%d")
    async with _minute_sem():
        path = (
            "/api/qt/stock/kline/get"
            f"?secid={_secid(c)}&ut={EASTMONEY_UT}"
            "&fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt=1&fqt=1&beg={day_compact}&end={day_compact}&lmt=1000"
        )
        hosts = () if _minute_em_paused() else (
            "push2his.eastmoney.com",
            "push2delay.eastmoney.com",
        )
        for host in hosts:
            url = f"https://{host}{path}"
            try:
                resp = await em_get(client, url, headers=HTTP_HEADERS, timeout=8.0)
                resp.raise_for_status()
                payload = resp.json()
            except Exception:
                continue
            rows = ((payload.get("data") or {}).get("klines")) or []
            out = _parse_minute_klines(rows)
            if out:
                # Keep only bars whose timestamp falls on the requested day.
                filtered = [
                    p
                    for p in out
                    if str(p.get("time") or "").replace("/", "-")[:10] == day
                    or day_compact in str(p.get("time") or "").replace("-", "")
                ]
                return filtered or out
        if day == today:
            return await _fetch_minute_trends_unlocked(client, c)
        recent = (await tencent_client.fetch_minute_days(client, c)).get(day) or []
        if recent:
            _note_minute_fallback(bool(hosts))
        return recent


async def fetch_minute_bars_many_days(
    client: httpx.AsyncClient,
    pairs: list[tuple[str, str]],
    *,
    concurrency: int | None = None,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Fetch 1-minute bars for many ``(code, trade_date)`` pairs."""
    uniq: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_code, raw_day in pairs:
        c = normalize_code(raw_code)
        d = str(raw_day or "")[:10]
        if not c or len(d) < 10:
            continue
        key = (c, d)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(key)
    if not uniq:
        return {}
    limit = max(1, min(int(concurrency or _MINUTE_CONCURRENCY), _MINUTE_CONCURRENCY))
    local = asyncio.Semaphore(limit)

    async def _one(code: str, day: str) -> tuple[tuple[str, str], list[dict[str, Any]]]:
        async with local:
            try:
                rows = await fetch_minute_bars_for_day(client, code, day)
            except Exception:
                rows = []
            return (code, day), rows

    results = await asyncio.gather(*[_one(c, d) for c, d in uniq])
    return {key: rows for key, rows in results}


_MINUTE_SOURCE = "eastmoney"


# After East Money minute feeds come back empty while Tencent has data, skip
# East Money minute calls for this long and read Tencent directly.
_MINUTE_EM_PAUSE_UNTIL = 0.0


_MINUTE_EM_PAUSE_SEC = 600.0


def _minute_em_paused() -> bool:
    return time.time() < _MINUTE_EM_PAUSE_UNTIL


def _note_minute_fallback(em_tried: bool) -> None:
    """Record that minute data came from Tencent; arm the pause if EM just failed."""
    global _MINUTE_SOURCE, _MINUTE_EM_PAUSE_UNTIL
    if em_tried:
        _MINUTE_EM_PAUSE_UNTIL = time.time() + _MINUTE_EM_PAUSE_SEC
    if _MINUTE_SOURCE != "tencent":
        log.warning("minute trends via Tencent fallback (East Money minute feed empty)")
    _MINUTE_SOURCE = "tencent"


async def _fetch_minute_trends_unlocked(
    client: httpx.AsyncClient,
    code: str,
) -> list[dict[str, Any]]:
    """Single-code minute fetch without taking the concurrency semaphore.

    East Money ``trends2`` first; Tencent when it is paused or returns nothing.
    """
    global _MINUTE_SOURCE
    c = normalize_code(code)
    if not c:
        return []
    em_tried = not _minute_em_paused()
    if em_tried:
        out = await _minute_trends_eastmoney(client, c)
        if out:
            _MINUTE_SOURCE = "eastmoney"
            return out
    out = await tencent_client.fetch_minute_trends(client, c)
    if out:
        _note_minute_fallback(em_tried)
    return out


async def _minute_trends_eastmoney(
    client: httpx.AsyncClient, c: str
) -> list[dict[str, Any]]:
    """Fetch today's minute points from East Money ``trends2`` hosts."""
    path = (
        "/api/qt/stock/trends2/get"
        f"?secid={_secid(c)}&ut={EASTMONEY_UT}"
        "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        "&ndays=1&iscr=0&iscca=0"
    )
    # Prefer delay first: push2 frequently RSTs on ETF secids.
    hosts = (
        "push2delay.eastmoney.com",
        "push2.eastmoney.com",
        "push2his.eastmoney.com",
    )
    for host in hosts:
        url = f"https://{host}{path}"
        if recently_blocked(url):
            continue
        try:
            resp = await em_get(client, url, headers=HTTP_HEADERS, timeout=6.0)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            continue
        rows = ((payload.get("data") or {}).get("trends")) or []
        out = _parse_minute_trends(rows)
        if out:
            return out
    return []


_MINUTE_CONCURRENCY = 3


_MINUTE_SEM: asyncio.Semaphore | None = None


def _minute_sem() -> asyncio.Semaphore:
    """Limit concurrent East Money minute-trend pulls across the process."""
    global _MINUTE_SEM
    if _MINUTE_SEM is None:
        _MINUTE_SEM = asyncio.Semaphore(_MINUTE_CONCURRENCY)
    return _MINUTE_SEM
