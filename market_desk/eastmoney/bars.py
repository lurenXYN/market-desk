"""Daily bars / klines / closes from East Money with Sina fallback."""

from __future__ import annotations

from typing import Any
import asyncio
import httpx
from market_desk.config import EASTMONEY_UT, HTTP_HEADERS
from market_desk.filters import normalize_code
from market_desk.numbers import num

from market_desk.eastmoney.avail import em_get, recently_blocked


def _secid(code: str) -> str:
    """Map a six-digit code to an East Money secid."""
    c = normalize_code(code)
    if c.startswith(("5", "6", "9")):
        return f"1.{c}"
    return f"0.{c}"


async def _fetch_daily_bars_eastmoney(
    client: httpx.AsyncClient,
    code: str,
    limit: int = 60,
    *,
    adjust: int = 1,
    beg: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """Try East Money history hosts; return [] on disconnect / empty klines.

    Uses a short single-shot timeout so Tencent/Sina fallbacks are not delayed.
    """
    fqt = 0 if int(adjust) <= 0 else (2 if int(adjust) >= 2 else 1)
    end_s = str(end or "20500101").replace("-", "")[:8] or "20500101"
    beg_s = str(beg or "").replace("-", "")[:8]
    beg_q = f"&beg={beg_s}" if len(beg_s) == 8 else ""
    path = (
        "/api/qt/stock/kline/get"
        f"?secid={_secid(code)}&ut={EASTMONEY_UT}"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt={fqt}{beg_q}&end={end_s}&lmt={limit}"
    )
    hosts = (
        "push2his.eastmoney.com",
        "push2delay.eastmoney.com",
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
        rows = ((payload.get("data") or {}).get("klines")) or []
        if not rows:
            continue
        out = _parse_eastmoney_klines(rows)
        if out:
            for row in out:
                row["source"] = "eastmoney"
            return out
    return []


async def fetch_daily_bars(
    client: httpx.AsyncClient,
    code: str,
    limit: int = 60,
    *,
    adjust: int = 1,
    beg: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch daily OHLCV bars (oldest → newest).

    ``adjust`` maps to East Money ``fqt``: 0=none, 1=forward, 2=backward.
    When present, the exchange daily percent change is parsed into ``pct``.
    Optional ``beg`` / ``end`` (YYYY-MM-DD or YYYYMMDD) shrink the window.

    Order: East Money (fast fail) → Tencent qfq → Sina. EM history hosts often
    disconnect on some networks; Tencent/Sina keep daily trend usable.
    """
    c = normalize_code(code)
    if not c:
        return []
    # Prefer Tencent first when only a narrow window is needed for trend MA —
    # EM his is flaky; still try EM when beg/end are set (calendar range).
    prefer_tx = not beg and not end
    bars: list[dict[str, Any]] = []
    if prefer_tx:
        try:
            from market_desk.tencent import fetch_daily_bars as tx_daily

            bars = await tx_daily(client, c, limit=limit)
        except Exception:
            bars = []
        if len(bars) >= 20:
            return bars
    em = await _fetch_daily_bars_eastmoney(
        client, c, limit=limit, adjust=adjust, beg=beg, end=end
    )
    if len(em) > len(bars):
        bars = em
    if len(bars) >= 20:
        return bars
    if not prefer_tx:
        try:
            from market_desk.tencent import fetch_daily_bars as tx_daily

            tx = await tx_daily(client, c, limit=limit)
            if len(tx) > len(bars):
                bars = tx
        except Exception:
            pass
    if len(bars) >= 20:
        return bars
    try:
        sina = await _fetch_daily_bars_sina(client, c, limit=limit)
        if len(sina) > len(bars):
            bars = sina
    except Exception:
        pass
    return bars


def _parse_eastmoney_klines(rows: list[Any]) -> list[dict[str, Any]]:
    """Parse East Money kline CSV rows into bar dicts."""
    out: list[dict[str, Any]] = []
    prev_close: float | None = None
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 6:
            continue
        o, h, lo, cl = num(parts[1]), num(parts[3]), num(parts[4]), num(parts[2])
        if cl is None:
            continue
        pct = num(parts[8]) if len(parts) >= 9 else None
        if pct is None and prev_close not in (None, 0):
            pct = (float(cl) / float(prev_close) - 1.0) * 100.0
        out.append(
            {
                "date": str(parts[0]),
                "open": o,
                "close": float(cl),
                "high": h,
                "low": lo,
                "volume": num(parts[5]),
                "pct": None if pct is None else round(float(pct), 2),
            }
        )
        prev_close = float(cl)
    return out


async def _fetch_daily_bars_sina(
    client: httpx.AsyncClient,
    code: str,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Fetch daily bars from Sina CN_MarketData (unadjusted scale=240)."""
    c = normalize_code(code)
    if not c:
        return []
    prefix = "sh" if c.startswith(("5", "6", "9")) else "sz"
    n = max(5, min(int(limit or 60), 320))
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={prefix}{c}&scale=240&ma=no&datalen={n}"
    )
    try:
        resp = await client.get(
            url,
            headers={
                **HTTP_HEADERS,
                "Referer": "https://finance.sina.com.cn",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    prev_close: float | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        cl = num(row.get("close"))
        if cl is None:
            continue
        pct = None
        if prev_close not in (None, 0):
            pct = (float(cl) / float(prev_close) - 1.0) * 100.0
        out.append(
            {
                "date": str(row.get("day") or ""),
                "open": num(row.get("open")),
                "close": float(cl),
                "high": num(row.get("high")),
                "low": num(row.get("low")),
                "volume": num(row.get("volume")),
                "pct": None if pct is None else round(float(pct), 2),
                "source": "sina",
            }
        )
        prev_close = float(cl)
    return out


async def fetch_daily_klines(
    client: httpx.AsyncClient,
    code: str,
    limit: int = 60,
) -> tuple[list[str], list[float], dict[str, list[float | None]]]:
    """Fetch adjusted daily dates, closes, and parallel OHLC(+volume) lists."""
    bars = await fetch_daily_bars(client, code, limit=limit)
    dates: list[str] = []
    closes: list[float] = []
    opens: list[float | None] = []
    highs: list[float | None] = []
    lows: list[float | None] = []
    volumes: list[float | None] = []
    for b in bars:
        dates.append(str(b.get("date") or ""))
        closes.append(float(b["close"]))
        try:
            opens.append(float(b["open"]) if b.get("open") is not None else None)
        except (TypeError, ValueError):
            opens.append(None)
        try:
            highs.append(float(b["high"]) if b.get("high") is not None else None)
        except (TypeError, ValueError):
            highs.append(None)
        try:
            lows.append(float(b["low"]) if b.get("low") is not None else None)
        except (TypeError, ValueError):
            lows.append(None)
        try:
            volumes.append(float(b["volume"]) if b.get("volume") is not None else None)
        except (TypeError, ValueError):
            volumes.append(None)
    return dates, closes, {"open": opens, "high": highs, "low": lows, "volume": volumes}


async def fetch_daily_closes(
    client: httpx.AsyncClient,
    code: str,
    limit: int = 60,
) -> list[float]:
    """Fetch adjusted daily closes for trend checks (oldest → newest)."""
    _dates, closes, _ohlc = await fetch_daily_klines(client, code, limit=limit)
    return closes


async def fetch_daily_closes_many(
    client: httpx.AsyncClient,
    codes: list[str],
    limit: int = 60,
) -> dict[str, list[float]]:
    """Fetch daily closes for several codes concurrently."""
    packed = await fetch_daily_klines_many(client, codes, limit=limit)
    out: dict[str, list[float]] = {}
    for code, triple in packed.items():
        if not triple:
            continue
        out[code] = triple[1]
    return out


async def fetch_daily_klines_many(
    client: httpx.AsyncClient,
    codes: list[str],
    limit: int = 60,
    *,
    concurrency: int = 6,
) -> dict[str, tuple[list[str], list[float], dict[str, list[float | None]]]]:
    """Fetch daily dates+closes+OHLC for several codes with a concurrency cap."""
    uniq: list[str] = []
    seen: set[str] = set()
    for raw in codes:
        code = normalize_code(raw)
        if not code or code in seen:
            continue
        seen.add(code)
        uniq.append(code)
    if not uniq:
        return {}
    sem = asyncio.Semaphore(max(1, int(concurrency or 6)))

    async def _one(
        code: str,
    ) -> tuple[list[str], list[float], dict[str, list[float | None]]]:
        async with sem:
            return await fetch_daily_klines(client, code, limit=limit)

    results = await asyncio.gather(*[_one(code) for code in uniq])
    return {code: triple for code, triple in zip(uniq, results)}
