"""East Money public snapshot clients."""

from __future__ import annotations

from typing import Any

import asyncio
import httpx

from market_desk.config import (
    CONCEPT_JUNK_KEYWORDS,
    CONSTITUENT_TOP,
    EASTMONEY_UT,
    HTTP_HEADERS,
    ZT_UT,
)
from market_desk.filters import is_main_board, normalize_code
from market_desk.numbers import num


def _zt_url(path: str, trade_date: str, extra: str = "") -> str:
    return (
        f"https://push2ex.eastmoney.com/{path}"
        f"?ut={ZT_UT}&dpt=wz.ztzt&Pageindex=0&pagesize=100&date={trade_date}{extra}"
    )


def _clist_url(
    fs: str,
    pz: int = 100,
    pn: int = 1,
    extra_fields: str = "",
    po: int = 1,
) -> str:
    fields = (
        "f12,f13,f14,f2,f3,f4,f5,f6,f8,f15,f16,f17,f18,f9,f20,"
        "f104,f105,f128,f140,f141,f136"
        + extra_fields
    )
    return (
        "https://push2delay.eastmoney.com/api/qt/clist/get"
        f"?pn={pn}&pz={pz}&po={po}&np=1&fltt=2&invt=2&fid=f3"
        f"&ut={EASTMONEY_UT}&fs={fs}&fields={fields}"
    )


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.get(url, headers=HTTP_HEADERS, timeout=25.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _pool_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    return data.get("pool") or []


def _map_zt_row(row: dict[str, Any], kind: str) -> dict[str, Any] | None:
    code = normalize_code(row.get("c"))
    if not is_main_board(code):
        return None
    name = str(row.get("n") or "")
    pct = num(row.get("zdp"), 0.0) or 0.0
    lbc = int(num(row.get("lbc"), 0) or 0)
    zttj = row.get("zttj") or {}
    days = int(num(zttj.get("days"), lbc) or lbc)
    return {
        "code": code,
        "name": name,
        "pct": round(pct, 2),
        "amount": num(row.get("amount"), 0.0) or 0.0,
        "turnover": num(row.get("hs"), 0.0) or 0.0,
        "boards": days or lbc,
        "explode_count": int(num(row.get("zbc"), 0) or 0),
        "industry": str(row.get("hybk") or ""),
        "first_seal": row.get("fbt"),
        "kind": kind,
    }


async def fetch_zt_pool(client: httpx.AsyncClient, trade_date: str) -> list[dict[str, Any]]:
    """Fetch today's limit-up pool and keep main-board names only."""
    url = _zt_url("getTopicZTPool", trade_date, "&sort=fbt:asc")
    payload = await _get_json(client, url)
    out: list[dict[str, Any]] = []
    for row in _pool_rows(payload):
        mapped = _map_zt_row(row, "zt")
        if mapped:
            out.append(mapped)
    return out


async def fetch_zb_pool(client: httpx.AsyncClient, trade_date: str) -> list[dict[str, Any]]:
    """Fetch today's broken-seal pool."""
    url = _zt_url("getTopicZBPool", trade_date, "&sort=fbt:asc")
    payload = await _get_json(client, url)
    if payload.get("data") is None:
        return []
    out: list[dict[str, Any]] = []
    for row in _pool_rows(payload):
        mapped = _map_zt_row(row, "zb")
        if mapped:
            out.append(mapped)
    return out


async def fetch_yesterday_zt(
    client: httpx.AsyncClient, zt_date: str
) -> list[dict[str, Any]]:
    """Fetch yesterday's limit-ups with today's follow-through stats."""
    url = _zt_url("getYesterdayZTPool", zt_date, "&sort=zdp:desc")
    payload = await _get_json(client, url)
    if payload.get("data") is None:
        return []
    out: list[dict[str, Any]] = []
    for row in _pool_rows(payload):
        code = normalize_code(row.get("c"))
        if not is_main_board(code):
            continue
        out.append(
            {
                "code": code,
                "name": str(row.get("n") or ""),
                "pct": round(num(row.get("zdp"), 0.0) or 0.0, 2),
                "boards_yesterday": int(num(row.get("ylbc"), 0) or 0),
                "industry": str(row.get("hybk") or ""),
            }
        )
    return out


async def previous_trade_date(client: httpx.AsyncClient, today: date) -> str:
    """Walk backward until yesterday's limit-up pool responds."""
    cursor = today - timedelta(days=1)
    for _ in range(10):
        if cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
            continue
        key = cursor.strftime("%Y%m%d")
        try:
            rows = await fetch_yesterday_zt(client, key)
        except httpx.HTTPError:
            rows = []
        if rows:
            return key
        cursor -= timedelta(days=1)
    return (today - timedelta(days=1)).strftime("%Y%m%d")


def _quote_from_diff(item: dict[str, Any]) -> dict[str, Any] | None:
    code = normalize_code(item.get("f12"))
    if not is_main_board(code):
        return None
    prev = num(item.get("f18"))
    open_px = num(item.get("f17"))
    open_pct = None
    if prev and prev != 0 and open_px is not None:
        open_pct = (open_px / prev - 1.0) * 100.0
    return {
        "code": code,
        "name": str(item.get("f14") or ""),
        "price": num(item.get("f2")),
        "pct": num(item.get("f3")),
        "open": open_px,
        "prev": prev,
        "open_pct": open_pct,
        "high": num(item.get("f15")),
        "low": num(item.get("f16")),
        "amount": num(item.get("f6"), 0.0) or 0.0,
        "turnover": num(item.get("f8"), 0.0) or 0.0,
    }


def _diff_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    diff = ((payload.get("data") or {}).get("diff")) or []
    if isinstance(diff, dict):
        return list(diff.values())
    return list(diff)


async def _fetch_clist_pages(
    client: httpx.AsyncClient, fs: str, pz: int = 100, max_pages: int = 25
) -> list[dict[str, Any]]:
    """Page through an East Money list endpoint until exhausted."""
    rows: list[dict[str, Any]] = []
    total = None
    for pn in range(1, max_pages + 1):
        payload = await _get_json(client, _clist_url(fs, pz=pz, pn=pn))
        chunk = _diff_rows(payload)
        if not chunk:
            break
        rows.extend(chunk)
        total = ((payload.get("data") or {}).get("total")) or total
        if total is not None and len(rows) >= int(total):
            break
        if len(chunk) < pz:
            break
    return rows


async def fetch_main_quotes(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch Shanghai and Shenzhen main-board quotes with pagination."""
    sh_rows, sz_rows = await asyncio.gather(
        _fetch_clist_pages(client, "m:1+t:2"),
        _fetch_clist_pages(client, "m:0+t:6"),
    )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*sh_rows, *sz_rows]:
        mapped = _quote_from_diff(item)
        if mapped and mapped["code"] not in seen:
            seen.add(mapped["code"])
            out.append(mapped)
    if not out:
        raise RuntimeError("main-board quote lists were empty")
    return out


def _is_junk_board(name: str) -> bool:
    return any(k in name for k in CONCEPT_JUNK_KEYWORDS)


def _board_from_diff(item: dict[str, Any], kind: str) -> dict[str, Any] | None:
    name = str(item.get("f14") or "")
    if not name or _is_junk_board(name):
        return None
    code = str(item.get("f12") or "")
    if not code.startswith("BK"):
        return None
    return {
        "bk": code,
        "name": name,
        "kind": kind,
        "pct": round(num(item.get("f3"), 0.0) or 0.0, 2),
        "amount": num(item.get("f20"), 0.0) or 0.0,
        "up_count": int(num(item.get("f104"), 0) or 0),
        "down_count": int(num(item.get("f105"), 0) or 0),
        "leader_name": str(item.get("f128") or ""),
        "leader_code": normalize_code(item.get("f140")),
        "leader_pct": round(num(item.get("f136"), 0.0) or 0.0, 2),
    }


async def fetch_hot_boards(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch concept gainers and the full industry universe."""
    concept_payload, industry_rows = await asyncio.gather(
        _get_json(client, _clist_url("m:90+t:3", pz=80)),
        _fetch_clist_pages(client, "m:90+t:2", pz=100, max_pages=5),
    )
    out: list[dict[str, Any]] = []
    for item in _diff_rows(concept_payload):
        mapped = _board_from_diff(item, "concept")
        if mapped:
            out.append(mapped)
    for item in industry_rows:
        mapped = _board_from_diff(item, "industry")
        if mapped:
            out.append(mapped)
    return out


async def fetch_board_members(
    client: httpx.AsyncClient, bk: str, weakest: bool = False
) -> list[dict[str, Any]]:
    """Fetch board constituents; weakest=True returns the largest losers first."""
    url = _clist_url(f"b:{bk}+f:!50", pz=30, po=0 if weakest else 1)
    try:
        payload = await _get_json(client, url)
    except httpx.HTTPError:
        return []
    diff = ((payload.get("data") or {}).get("diff")) or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    members: list[dict[str, Any]] = []
    for item in diff:
        code = normalize_code(item.get("f12"))
        if not is_main_board(code):
            continue
        members.append(
            {
                "code": code,
                "name": str(item.get("f14") or ""),
                "pct": round(num(item.get("f3"), 0.0) or 0.0, 2),
                "turnover": round(num(item.get("f8"), 0.0) or 0.0, 2),
                "price": num(item.get("f2")),
                "high": num(item.get("f15")),
                "low": num(item.get("f16")),
            }
        )
        if len(members) >= CONSTITUENT_TOP:
            break
    return members


def _secid(code: str) -> str:
    """Map a six-digit code to an East Money secid."""
    c = normalize_code(code)
    if c.startswith(("5", "6", "9")):
        return f"1.{c}"
    return f"0.{c}"


async def fetch_daily_closes(
    client: httpx.AsyncClient,
    code: str,
    limit: int = 60,
) -> list[float]:
    """Fetch adjusted daily closes for trend checks (oldest → newest)."""
    c = normalize_code(code)
    if not c:
        return []
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={_secid(c)}&ut={EASTMONEY_UT}"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&end=20500101&lmt={limit}"
    )
    try:
        payload = await _get_json(client, url)
    except Exception:
        return []
    rows = ((payload.get("data") or {}).get("klines")) or []
    closes: list[float] = []
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 3:
            continue
        px = num(parts[2])
        if px is not None:
            closes.append(float(px))
    return closes


async def fetch_daily_closes_many(
    client: httpx.AsyncClient,
    codes: list[str],
    limit: int = 60,
) -> dict[str, list[float]]:
    """Fetch daily closes for several codes concurrently."""
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
    results = await asyncio.gather(
        *[fetch_daily_closes(client, code, limit=limit) for code in uniq]
    )
    return {code: closes for code, closes in zip(uniq, results)}
