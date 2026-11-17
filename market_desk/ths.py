"""Tonghuashun (同花顺) fallback source: intraday trend and today's snapshot.

Why this exists: on the VPS, East Money's ``push2his`` trends endpoint is the one
that gets cut most often, so the chart drawer and the minute sparkline go empty
while quotes still work. Tencent has no minute line and Sina's minute API needs a
different shape, so a third independent host is worth having.

Payload shapes were verified against live responses (2026-11-17):

* ``/v6/time/hs_600000/last.js`` →
  ``{"hs_600000": {"pre": "10.78", "data": "0930,10.78,10134817,10.780,940150;...``
  → ``time, price, volume, avg, amount`` per minute.
* ``/v6/line/hs_600000/01/today.js`` →
  ``{"1": "20261117", "7": "10.78", "8": "10.88", "9": "10.73", "11": "10.86", ...}``
  → ``1=date, 7=prev_close, 8=high, 9=low, 11=last, 13=volume, 19=amount``.

Only these two are used. The daily ``line/.../last.js`` payload is **not** parsed
here: its adjustment (前复权/不复权) is unverified, and writing it into the shared
bar cache could mix adjustment families.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from market_desk.filters import normalize_code
from market_desk.numbers import num

log = logging.getLogger("market_desk.ths")

_BASE = "https://d.10jqka.com.cn/v6"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://stockpage.10jqka.com.cn/",
}
_WRAP = re.compile(r"^\s*[\w$.]+\((.*)\)\s*;?\s*$", re.S)


def _ths_symbol(code: str) -> str:
    """``600000`` / ``sh600000`` → ``hs_600000`` (market-agnostic prefix)."""
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(digits) < 6:
        c = normalize_code(code)
        digits = "".join(ch for ch in str(c or "") if ch.isdigit())
    return f"hs_{digits[-6:]}" if len(digits) >= 6 else ""


def _unwrap(text: str) -> dict[str, Any]:
    """Strip the ``quotebridge_v6_...(...)`` JSONP wrapper."""
    body = str(text or "").strip()
    m = _WRAP.match(body)
    if m:
        body = m.group(1)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


async def fetch_minute_trends(client: httpx.AsyncClient, code: str) -> list[dict[str, Any]]:
    """Today's minute points, shaped like ``eastmoney/minute.py`` returns.

    Each point: ``{"time": "09:30", "price": 10.78, "avg": 10.780}``.
    Returns ``[]`` on any failure — callers treat it as "no data", never as 0.
    """
    sym = _ths_symbol(code)
    if not sym:
        return []
    url = f"{_BASE}/time/{sym}/last.js"
    try:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        payload = _unwrap(resp.text)
    except Exception as exc:
        log.debug("ths minute failed %s: %s", sym, type(exc).__name__)
        return []
    row = payload.get(sym) if isinstance(payload.get(sym), dict) else {}
    raw = str((row or {}).get("data") or "")
    out: list[dict[str, Any]] = []
    for chunk in raw.split(";"):
        parts = chunk.split(",")
        if len(parts) < 3:
            continue
        hhmm, price = parts[0].strip(), num(parts[1])
        if not hhmm.isdigit() or len(hhmm) != 4 or price is None:
            continue
        avg = num(parts[3]) if len(parts) >= 4 else None
        item: dict[str, Any] = {"time": f"{hhmm[:2]}:{hhmm[2:]}", "price": price}
        if avg is not None:
            item["avg"] = avg
        out.append(item)
    if out and not out[0]["time"].startswith("09:3"):
        out.insert(0, {"time": "09:30", "price": out[0]["price"]})
    return out


async def fetch_today_snapshot(client: httpx.AsyncClient, code: str) -> dict[str, Any] | None:
    """Today's O/H/L/last/volume/amount for one code (fallback quote shape)."""
    sym = _ths_symbol(code)
    if not sym:
        return None
    url = f"{_BASE}/line/{sym}/01/today.js"
    try:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        payload = _unwrap(resp.text)
    except Exception as exc:
        log.debug("ths today failed %s: %s", sym, type(exc).__name__)
        return None
    row = payload.get(sym) if isinstance(payload.get(sym), dict) else {}
    if not row:
        return None
    last = num(row.get("11"))
    prev = num(row.get("7"))
    if last is None:
        return None
    pct = round((last / prev - 1.0) * 100.0, 2) if prev not in (None, 0) else None
    return {
        "code": normalize_code(code),
        "name": str(row.get("name") or ""),
        "last": last,
        "prev_close": prev,
        "high": num(row.get("8")),
        "low": num(row.get("9")),
        "volume": num(row.get("13")),
        "amount": num(row.get("19")),
        "pct": pct,
        "date": str(row.get("1") or ""),
        "source": "ths",
    }