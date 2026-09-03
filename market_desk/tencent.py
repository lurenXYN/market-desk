"""Tencent Finance quote client."""

from __future__ import annotations

from typing import Any

import httpx

from market_desk.config import ETF_WATCH, HTTP_HEADERS, CHINEXT_STAR_ETFS
from market_desk.numbers import num


def tencent_symbol(code: str) -> str:
    """Map a six-digit ticker to a Tencent quote symbol."""
    c = str(code).strip().zfill(6)
    if c.startswith(("5", "6", "9")):
        return "sh" + c
    return "sz" + c


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
        out[code] = {
            "code": code,
            "name": str(fields[1] or ""),
            "price": num(fields[3]),
            "pct": num(fields[32]),
            "open": num(fields[5]),
            "high": num(fields[33]) if len(fields) > 33 else None,
            "low": num(fields[34]) if len(fields) > 34 else None,
            "prev": num(fields[4]),
        }
    return out
