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
        mv = num(item.get("f20"))
        mv_yi = None if mv is None or mv <= 0 else round(float(mv) / 1e8, 2)
        members.append(
            {
                "code": code,
                "name": str(item.get("f14") or ""),
                "pct": round(num(item.get("f3"), 0.0) or 0.0, 2),
                "turnover": round(num(item.get("f8"), 0.0) or 0.0, 2),
                "price": num(item.get("f2")),
                "high": num(item.get("f15")),
                "low": num(item.get("f16")),
                "mv_yi": mv_yi,
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
        try:
            resp = await client.get(url, headers=HTTP_HEADERS, timeout=6.0)
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
) -> tuple[list[str], list[float]]:
    """Fetch adjusted daily dates and closes (oldest → newest)."""
    bars = await fetch_daily_bars(client, code, limit=limit)
    return [b["date"] for b in bars], [float(b["close"]) for b in bars]


async def fetch_minute_trends(
    client: httpx.AsyncClient,
    code: str,
) -> list[dict[str, Any]]:
    """Fetch today's minute trend points for an intraday sparkline."""
    c = normalize_code(code)
    if not c:
        return []
    url = (
        "https://push2.eastmoney.com/api/qt/stock/trends2/get"
        f"?secid={_secid(c)}&ut={EASTMONEY_UT}"
        "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        "&ndays=1&iscr=0&iscca=0"
    )
    try:
        payload = await _get_json(client, url)
    except Exception:
        return []
    rows = ((payload.get("data") or {}).get("trends")) or []
    out: list[dict[str, Any]] = []
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 2:
            continue
        px = num(parts[1])
        if px is None:
            continue
        # Eastmoney trends: time,price,avg,volume,amount,...
        avg = num(parts[2]) if len(parts) > 2 else None
        vol = num(parts[3]) if len(parts) > 3 else None
        amt = num(parts[4]) if len(parts) > 4 else None
        point: dict[str, Any] = {"time": str(parts[0]), "price": float(px)}
        if avg is not None:
            point["avg"] = float(avg)
        if vol is not None:
            point["volume"] = float(vol)
        if amt is not None:
            point["amount"] = float(amt)
        out.append(point)
    return out


async def fetch_daily_closes(
    client: httpx.AsyncClient,
    code: str,
    limit: int = 60,
) -> list[float]:
    """Fetch adjusted daily closes for trend checks (oldest → newest)."""
    _dates, closes = await fetch_daily_klines(client, code, limit=limit)
    return closes


async def fetch_daily_closes_many(
    client: httpx.AsyncClient,
    codes: list[str],
    limit: int = 60,
) -> dict[str, list[float]]:
    """Fetch daily closes for several codes concurrently."""
    packed = await fetch_daily_klines_many(client, codes, limit=limit)
    return {code: closes for code, (_dates, closes) in packed.items()}


async def fetch_daily_klines_many(
    client: httpx.AsyncClient,
    codes: list[str],
    limit: int = 60,
) -> dict[str, tuple[list[str], list[float]]]:
    """Fetch daily dates+closes for several codes concurrently."""
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
        *[fetch_daily_klines(client, code, limit=limit) for code in uniq]
    )
    return {code: pair for code, pair in zip(uniq, results)}


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
