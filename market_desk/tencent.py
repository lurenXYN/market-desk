"""Tencent Finance quote client."""

from __future__ import annotations

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
    sym = tencent_symbol(c)
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
        # Some responses nest under an unexpected key; take the first dict.
        first = next(iter(data.values()), None)
        node = first if isinstance(first, dict) else {}
    rows = node.get("qfqday") or node.get("day") or []
    out: list[dict[str, Any]] = []
    prev_close: float | None = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        # Tencent: date, open, close, high, low, volume
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
