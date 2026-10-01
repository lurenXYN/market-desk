"""Stock meta (float cap, industry) and holder statistics."""

from __future__ import annotations

from typing import Any
import asyncio
import httpx
from market_desk.config import HTTP_HEADERS
from market_desk.filters import normalize_code
from market_desk.numbers import num

from market_desk.eastmoney.bars import _secid


async def fetch_stock_meta_many(
    client: httpx.AsyncClient,
    codes: list[str],
    *,
    chunk_size: int = 80,
) -> dict[str, dict[str, Any]]:
    """Fetch industry (``f100``) and total market cap (``f20``) keyed by code.

    One ``ulist.np`` request per ``chunk_size`` codes; tries the delay host when
    the primary push2 host fails. Missing codes are simply absent.

    Returns:
        ``{code: {"industry": str, "mv_yi": float | None}}``.
    """
    uniq = list(dict.fromkeys(c for c in (normalize_code(x) for x in codes) if c))
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(uniq), max(1, chunk_size)):
        chunk = uniq[i : i + chunk_size]
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": "f12,f14,f20,f100",
            "secids": ",".join(_secid(c) for c in chunk),
        }
        rows: list[dict[str, Any]] = []
        # Both hosts drop connections intermittently; a second round with backoff
        # recovers most of the blanks that used to stick on the MA-fan list.
        hosts = ("push2.eastmoney.com", "push2delay.eastmoney.com") * 2
        for attempt, host in enumerate(hosts):
            try:
                resp = await client.get(
                    f"https://{host}/api/qt/ulist.np/get",
                    params=params,
                    headers=HTTP_HEADERS,
                    timeout=15.0,
                )
                resp.raise_for_status()
                rows = list(((resp.json() or {}).get("data") or {}).get("diff") or [])
                if rows:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3 * (attempt + 1))
        for row in rows:
            code = normalize_code(row.get("f12"))
            if not code:
                continue
            mv = num(row.get("f20"))
            industry = str(row.get("f100") or "").strip()
            out[code] = {
                "industry": "" if industry in ("-", "--") else industry,
                "mv_yi": None if mv is None or mv <= 0 else round(float(mv) / 1e8, 1),
            }
    return out


# Shareholder counts move quarterly; cache aggressively to keep review snappy.
_HOLDER_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


_HOLDER_CACHE_TTL_SEC = 6 * 3600


def _is_equity_code(code: str) -> bool:
    """Return True for A-share equities (skip ETFs/funds for holder stats)."""
    c = normalize_code(code)
    if not c or len(c) != 6:
        return False
    if c.startswith(("15", "51", "56", "58")):
        return False
    return c.startswith(("0", "3", "6"))


def _map_holder_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one datacenter holder-count row."""
    code = normalize_code(row.get("SECURITY_CODE"))
    if not code:
        return None
    holder_n = num(row.get("HOLDER_NUM"))
    prev_n = num(row.get("PRE_HOLDER_NUM"))
    chg = num(row.get("HOLDER_NUM_CHANGE"))
    ratio = num(row.get("HOLDER_NUM_RATIO"))
    avg_cap = num(row.get("AVG_MARKET_CAP"))
    end_raw = str(row.get("END_DATE") or "")
    notice_raw = str(row.get("HOLD_NOTICE_DATE") or "")
    return {
        "code": code,
        "name": str(row.get("SECURITY_NAME_ABBR") or ""),
        "holder_num": None if holder_n is None else int(holder_n),
        "holder_prev": None if prev_n is None else int(prev_n),
        "holder_chg": None if chg is None else int(chg),
        "holder_chg_pct": None if ratio is None else round(float(ratio), 2),
        # East Money stores 户均市值 in 元; expose both raw and 万元.
        "holder_avg_cap": None if avg_cap is None else round(float(avg_cap), 0),
        "holder_avg_wan": None if avg_cap is None else round(float(avg_cap) / 1e4, 2),
        "holder_end": end_raw[:10] if end_raw else None,
        "holder_notice": notice_raw[:10] if notice_raw else None,
    }


async def fetch_holder_stats_many(
    client: httpx.AsyncClient,
    codes: list[str],
    *,
    chunk_size: int = 40,
) -> dict[str, dict[str, Any]]:
    """Fetch latest shareholder-count stats keyed by six-digit code.

    Uses East Money datacenter ``RPT_HOLDERNUMLATEST`` (quarterly disclosure).
    ETF / fund codes are skipped. Results are cached for several hours.
    """
    import time

    now = time.time()
    want: list[str] = []
    out: dict[str, dict[str, Any]] = {}
    for raw in codes:
        code = normalize_code(raw)
        if not code or not _is_equity_code(code):
            continue
        hit = _HOLDER_CACHE.get(code)
        if hit and now - hit[0] < _HOLDER_CACHE_TTL_SEC:
            out[code] = dict(hit[1])
            continue
        want.append(code)
    if not want:
        return out

    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    headers = {
        **HTTP_HEADERS,
        "Referer": "https://data.eastmoney.com/",
    }
    for i in range(0, len(want), max(1, chunk_size)):
        chunk = want[i : i + chunk_size]
        filt = f'(SECURITY_CODE in ({",".join(repr(c) for c in chunk)}))'.replace("'", '"')
        params = {
            "reportName": "RPT_HOLDERNUMLATEST",
            "columns": (
                "SECURITY_CODE,SECURITY_NAME_ABBR,END_DATE,HOLD_NOTICE_DATE,"
                "HOLDER_NUM,PRE_HOLDER_NUM,HOLDER_NUM_CHANGE,HOLDER_NUM_RATIO,"
                "AVG_MARKET_CAP,AVG_HOLD_NUM"
            ),
            "filter": filt,
            "pageNumber": "1",
            "pageSize": str(max(len(chunk), 20)),
            "sortColumns": "HOLD_NOTICE_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        }
        try:
            last_error: Exception | None = None
            payload: dict[str, Any] = {}
            for attempt in range(3):
                try:
                    resp = await client.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=25.0,
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    await asyncio.sleep(0.4 * (attempt + 1))
            if last_error is not None:
                continue
        except Exception:
            continue
        rows = ((payload.get("result") or {}).get("data")) or []
        seen_chunk: set[str] = set()
        for row in rows:
            mapped = _map_holder_row(row)
            if not mapped:
                continue
            code = mapped["code"]
            if code in seen_chunk:
                continue
            seen_chunk.add(code)
            _HOLDER_CACHE[code] = (now, mapped)
            out[code] = dict(mapped)
        # Negative-cache misses briefly so we do not hammer empty codes each refresh.
        for code in chunk:
            if code not in out:
                empty = {
                    "code": code,
                    "holder_num": None,
                    "holder_prev": None,
                    "holder_chg": None,
                    "holder_chg_pct": None,
                    "holder_avg_cap": None,
                    "holder_avg_wan": None,
                    "holder_end": None,
                    "holder_notice": None,
                }
                _HOLDER_CACHE[code] = (now, empty)
                out[code] = dict(empty)
    return out
