"""Tencent Finance quote client."""

from __future__ import annotations

import time
from typing import Any

import httpx

from market_desk.config import ETF_WATCH, HTTP_HEADERS, CHINEXT_STAR_ETFS, INDEX_WATCH
from market_desk.numbers import num


def tencent_symbol(code: str) -> str:
    """Map a six-digit ticker to a Tencent quote symbol."""
    c = str(code).strip().zfill(6)
    if c.startswith(("5", "6", "9")):
        return "sh" + c
    return "sz" + c


async def fetch_indices(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch major index quotes (上证/创业板/科创50/…)."""
    if not INDEX_WATCH:
        return []
    url = "https://qt.gtimg.cn/q=" + ",".join(sym for sym, _, _ in INDEX_WATCH)
    resp = await client.get(url, headers=HTTP_HEADERS, timeout=15.0)
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="ignore")
    by_code: dict[str, dict[str, Any]] = {}
    for chunk in text.split(";"):
        if '="' not in chunk:
            continue
        body = chunk.split('="', 1)[1].rstrip('";')
        fields = body.split("~")
        if len(fields) < 33:
            continue
        code = str(fields[2]).zfill(6)
        by_code[code] = {
            "code": code,
            "name": str(fields[1] or ""),
            "price": num(fields[3]),
            "pct": num(fields[32]),
            "open": num(fields[5]),
            "high": num(fields[33]) if len(fields) > 33 else None,
            "low": num(fields[34]) if len(fields) > 34 else None,
            "prev": num(fields[4]),
        }
    out: list[dict[str, Any]] = []
    for _sym, code, name in INDEX_WATCH:
        row = dict(by_code.get(code) or {})
        row["code"] = code
        row["name"] = name
        row["kind"] = "index"
        out.append(row)
    return out


async def fetch_etfs(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch the configured ETF watchlist quotes."""
    codes = [item[1] for item in ETF_WATCH]
    by_code = await fetch_quotes(client, codes)
    out: list[dict[str, Any]] = []
    for _, code, name in ETF_WATCH:
        row = dict(by_code.get(code) or {})
        row["code"] = code
        row["name"] = name
        row["stock_no_perm"] = code in CHINEXT_STAR_ETFS
        out.append(row)
    return out


async def fetch_quotes(
    client: httpx.AsyncClient, codes: list[str]
) -> dict[str, dict[str, Any]]:
    """Fetch last/open/high/low quotes keyed by six-digit code."""
    uniq: list[str] = []
    seen: set[str] = set()
    for raw in codes:
        code = str(raw or "").strip().zfill(6)
        if not code or code in seen:
            continue
        seen.add(code)
        uniq.append(code)
    if not uniq:
        return {}
    url = "https://qt.gtimg.cn/q=" + ",".join(tencent_symbol(c) for c in uniq)
    resp = await client.get(url, headers=HTTP_HEADERS, timeout=15.0)
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="ignore")
    out: dict[str, dict[str, Any]] = {}
    for chunk in text.split(";"):
        if '="' not in chunk:
            continue
        body = chunk.split('="', 1)[1].rstrip('";')
        fields = body.split("~")
        if len(fields) < 33:
            continue
        code = str(fields[2]).zfill(6)
        # Tencent: ~6 volume(手), ~36 volume, ~37 amount(万元), ~38 turnover%.
        volume = num(fields[6])
        if volume is None and len(fields) > 36:
            volume = num(fields[36])
        amount_wan = num(fields[37]) if len(fields) > 37 else None
        turnover = num(fields[38]) if len(fields) > 38 else None
        out[code] = {
            "code": code,
            "name": str(fields[1] or ""),
            "price": num(fields[3]),
            "pct": num(fields[32]),
            "open": num(fields[5]),
            "high": num(fields[33]) if len(fields) > 33 else None,
            "low": num(fields[34]) if len(fields) > 34 else None,
            "prev": num(fields[4]),
            "volume": volume,
            # Convert 万元 → 元 so callers can share stock amount units.
            "amount": None if amount_wan is None else float(amount_wan) * 1e4,
            "turnover": turnover,
        }
    return out


async def fetch_daily_bars(
    client: httpx.AsyncClient,
    code: str,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Fetch forward-adjusted daily OHLCV from Tencent (oldest → newest).

    Used when East Money history hosts fail or return empty klines.
    Row shape matches ``eastmoney.fetch_daily_bars``.
    """
    c = str(code or "").strip().zfill(6)
    if len(c) != 6 or not c.isdigit():
        return []
    return await fetch_daily_bars_symbol(client, tencent_symbol(c), limit=limit)


async def fetch_daily_bars_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Fetch daily bars for an explicit Tencent symbol (e.g. ``sh000001``).

    Prefer this for indices — six-digit ``000001`` alone maps to 平安银行 on SZ.
    """
    sym = str(symbol or "").strip().lower()
    if not sym or not sym[:2] in ("sh", "sz") or len(sym) < 8:
        return []
    n = max(5, min(int(limit or 60), 320))
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={sym},day,,,{n},qfq"
    )
    try:
        resp = await client.get(
            url,
            headers={
                **HTTP_HEADERS,
                "Referer": "https://gu.qq.com/",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []
    data = payload.get("data") or {}
    node = data.get(sym) or {}
    if not node and data:
        first = next(iter(data.values()), None)
        node = first if isinstance(first, dict) else {}
    rows = node.get("qfqday") or node.get("day") or []
    out: list[dict[str, Any]] = []
    prev_close: float | None = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        o, cl, h, lo = num(row[1]), num(row[2]), num(row[3]), num(row[4])
        if cl is None:
            continue
        pct = None
        if prev_close not in (None, 0):
            pct = (float(cl) / float(prev_close) - 1.0) * 100.0
        out.append(
            {
                "date": str(row[0]),
                "open": o,
                "close": float(cl),
                "high": h,
                "low": lo,
                "volume": num(row[5]) if len(row) > 5 else None,
                "pct": None if pct is None else round(float(pct), 2),
                "source": "tencent",
            }
        )
        prev_close = float(cl)
    return out[-n:] if len(out) > n else out


_MINUTE_HEADERS = {**HTTP_HEADERS, "Referer": "https://gu.qq.com/"}
_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={sym}"
_MINUTE_DAYS_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query?code={sym}"
_MINUTE_DAYS_TTL_S = 600.0
_MINUTE_DAYS_CACHE: dict[str, tuple[float, dict[str, list[dict[str, Any]]]]] = {}


def _minute_points(rows: list[Any], day: str) -> list[dict[str, Any]]:
    """Map Tencent minute rows into East Money ``trends2`` point shape.

    Tencent rows are ``"HHMM price cum_volume(手) cum_amount(元)"``. Volume and
    amount become per-minute deltas, ``avg`` the running VWAP; rows after 15:00
    (after-hours prints) are dropped.
    """
    d = str(day or "").replace("-", "")
    if len(d) != 8:
        return []
    date_s = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    out: list[dict[str, Any]] = []
    prev_vol = prev_amt = 0.0
    for row in rows:
        parts = str(row).split()
        if len(parts) < 2 or len(parts[0]) != 4 or parts[0] > "1500":
            continue
        px = num(parts[1])
        if px is None or px <= 0:
            continue
        point: dict[str, Any] = {
            "time": f"{date_s} {parts[0][:2]}:{parts[0][2:]}",
            "price": float(px),
        }
        cum_vol = num(parts[2]) if len(parts) > 2 else None
        cum_amt = num(parts[3]) if len(parts) > 3 else None
        if cum_vol is not None and cum_amt is not None:
            point["volume"] = max(0.0, float(cum_vol) - prev_vol)
            point["amount"] = max(0.0, float(cum_amt) - prev_amt)
            if cum_vol > 0 and cum_amt > 0:
                point["avg"] = float(cum_amt) / (float(cum_vol) * 100.0)
            prev_vol, prev_amt = float(cum_vol), float(cum_amt)
        out.append(point)
    return out


async def _get_minute_node(client: httpx.AsyncClient, url: str, sym: str) -> Any:
    resp = await client.get(url, headers=_MINUTE_HEADERS, timeout=10.0)
    resp.raise_for_status()
    return ((resp.json() or {}).get("data") or {}).get(sym) or {}


async def fetch_minute_trends(client: httpx.AsyncClient, code: str) -> list[dict[str, Any]]:
    """Fetch today's minute points from Tencent (``[]`` on any failure)."""
    c = str(code or "").strip().zfill(6)
    if len(c) != 6 or not c.isdigit():
        return []
    sym = tencent_symbol(c)
    try:
        node = await _get_minute_node(client, _MINUTE_URL.format(sym=sym), sym)
    except Exception:
        return []
    inner = node.get("data") if isinstance(node, dict) else None
    if not isinstance(inner, dict):
        return []
    return _minute_points(inner.get("data") or [], str(inner.get("date") or ""))


async def fetch_minute_days(
    client: httpx.AsyncClient, code: str
) -> dict[str, list[dict[str, Any]]]:
    """Fetch the last five sessions of minute points keyed by ``YYYY-MM-DD``."""
    c = str(code or "").strip().zfill(6)
    if len(c) != 6 or not c.isdigit():
        return {}
    now = time.time()
    hit = _MINUTE_DAYS_CACHE.get(c)
    if hit and now - hit[0] < _MINUTE_DAYS_TTL_S:
        return hit[1]
    sym = tencent_symbol(c)
    try:
        node = await _get_minute_node(client, _MINUTE_DAYS_URL.format(sym=sym), sym)
    except Exception:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for day in (node.get("data") if isinstance(node, dict) else None) or []:
        if not isinstance(day, dict):
            continue
        points = _minute_points(day.get("data") or [], str(day.get("date") or ""))
        if points:
            out[points[0]["time"][:10]] = points
    if out:
        _MINUTE_DAYS_CACHE[c] = (now, out)
    return out


async def _fetch_daily_bars_sina_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Fetch daily bars from Sina for an explicit symbol (e.g. ``sh000001``)."""
    sym = str(symbol or "").strip().lower()
    if not sym or sym[:2] not in ("sh", "sz") or len(sym) < 8:
        return []
    n = max(5, min(int(limit or 60), 320))
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={n}"
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
                "date": str(row.get("day") or row.get("date") or "")[:10],
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
    return out[-n:] if len(out) > n else out


async def fetch_index_daily_bars(
    client: httpx.AsyncClient,
    symbol: str = "sh000001",
    limit: int = 320,
) -> list[dict[str, Any]]:
    """Load index daily OHLCV with Tencent → Sina fallback.

    Six-digit ``000001`` alone is 平安银行 on SZ; always pass ``sh000001`` for 上证.
    Empty Tencent responses do not raise, so Sina is tried whenever the sample is thin.
    """
    want = max(30, min(int(limit or 320), 320))
    bars = await fetch_daily_bars_symbol(client, symbol, limit=want)
    if len(bars) >= 30:
        return bars
    sina = await _fetch_daily_bars_sina_symbol(client, symbol, limit=want)
    if len(sina) > len(bars):
        return sina
    return bars
