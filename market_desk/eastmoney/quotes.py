"""Limit-up pools and main-board quotes with Tencent / Sina fallback."""

from __future__ import annotations

from typing import Any
import logging
import time
import httpx
from market_desk.config import HTTP_HEADERS
from market_desk.filters import is_main_board, normalize_code
from market_desk.numbers import num

from market_desk.eastmoney.client import (
    _clist_url,
    _fetch_clist_pages,
    _get_json,
    _zt_url,
    clist_in_backoff,
)

log = logging.getLogger("market_desk.eastmoney")


def cached_main_quotes() -> list[dict[str, Any]]:
    """Return a copy of the last successful main-board quote list, or []."""
    if not _MAIN_QUOTES_CACHE:
        return []
    return [dict(row) for row in _MAIN_QUOTES_CACHE[1]]


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
        "seal_fund": num(row.get("fund")),
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
    """Fetch the prior-session limit-up pool as of ``zt_date``.

    ``zt_date`` is the East Money *as-of* day (usually today). The API returns
    stocks that sealed on the previous session, with follow-through stats for
    ``zt_date`` (open/last pct used by the auction strategy board).
    """
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
        # Stock clist f20 = 总市值 (yuan); boards use f20 as成交额 separately.
        "mv_yi": (
            None
            if num(item.get("f20")) is None or float(num(item.get("f20")) or 0) <= 0
            else round(float(num(item.get("f20"))) / 1e8, 2)
        ),
    }


_QUOTE_PROBE: tuple[float, str] = (0.0, "")


_QUOTE_PROBE_EVERY_SEC = 600.0


async def _probe_quote_edges(client: httpx.AsyncClient) -> str:
    """Summarize what each quote edge returns for page 1, at most every 10 minutes.

    Used only after a total main-board failure so the health strip and log show
    the real cause (HTTP status, empty ``data``, connect errors) instead of a
    generic "empty" message.
    """
    global _QUOTE_PROBE
    now = time.time()
    if _QUOTE_PROBE[1] and now - _QUOTE_PROBE[0] < _QUOTE_PROBE_EVERY_SEC:
        return _QUOTE_PROBE[1]
    parts: list[str] = []
    for h in _QUOTES_CLIST_HOSTS:
        tag = h.split(".")[0]
        try:
            resp = await client.get(
                _clist_url("m:1+t:2", pz=100, pn=1, host=h), headers=HTTP_HEADERS, timeout=10.0
            )
            try:
                data = resp.json()
                inner = data.get("data") if isinstance(data, dict) else None
                rc = data.get("rc") if isinstance(data, dict) else "?"
                shape = (
                    f"total={inner.get('total')}" if isinstance(inner, dict) else "data=null"
                )
                parts.append(f"{tag}={resp.status_code} rc={rc} {shape}")
            except ValueError:
                body = " ".join(resp.text[:40].split())
                parts.append(f"{tag}={resp.status_code} {body}")
        except Exception as exc:
            parts.append(f"{tag}={type(exc).__name__} {str(exc)[:60]}")
    summary = " | ".join(parts)
    _QUOTE_PROBE = (now, summary)
    log.warning("quotes edge probe: %s", summary)
    return summary


# Heavy main-board pagination: prefer delay edge (push2 drops mid-list often).
_QUOTES_CLIST_HOSTS: tuple[str, ...] = (
    "push2delay.eastmoney.com",
    "push2.eastmoney.com",
    "push2his.eastmoney.com",
)


async def fetch_main_quotes(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch Shanghai and Shenzhen main-board quotes with pagination.

    Results are cached to cut the heaviest clist fan-out on the 20s tick.
    Auction uses a medium TTL (not sub-5s): full-market paging cannot keep up
    with call-auction poll cadence and trips edge disconnects.
    Partial pages are accepted when the edge dies mid-list (≥200 rows).
    When clist fails outright (East Money blocks list endpoints for some IPs),
    clist is paused for ``_QUOTES_CLIST_PAUSE_SEC`` and quotes come from the
    Tencent fallback in ``quotes_fallback``.
    """
    from datetime import datetime, timezone, timedelta

    global _MAIN_QUOTES_CACHE, _QUOTES_CLIST_PAUSE_UNTIL, _MAIN_QUOTES_SOURCE
    now = time.time()
    ttl = _MAIN_QUOTES_TTL_SEC
    try:
        local = datetime.now(timezone(timedelta(hours=8)))
        mins = local.hour * 60 + local.minute
        if 9 * 60 + 15 <= mins < 9 * 60 + 30:
            ttl = _MAIN_QUOTES_AUCTION_TTL_SEC
        elif 9 * 60 + 30 <= mins < 10 * 60:
            ttl = _MAIN_QUOTES_OPEN_TTL_SEC
    except Exception:
        pass
    if _MAIN_QUOTES_CACHE and now - _MAIN_QUOTES_CACHE[0] < ttl:
        return [dict(row) for row in _MAIN_QUOTES_CACHE[1]]
    if clist_in_backoff() and _MAIN_QUOTES_CACHE:
        return [dict(row) for row in _MAIN_QUOTES_CACHE[1]]

    from market_desk import quotes_fallback

    out: list[dict[str, Any]] = []
    clist_note = "clist paused"
    if now >= _QUOTES_CLIST_PAUSE_UNTIL:
        out, errs = await _main_quotes_from_clist(client)
        if len(out) >= _QUOTES_CLIST_MIN_ROWS:
            quotes_fallback.remember_universe([row["code"] for row in out])
            if _MAIN_QUOTES_SOURCE != "eastmoney":
                log.info("main-board quotes back on East Money (%s rows)", len(out))
            _MAIN_QUOTES_SOURCE = "eastmoney"
        else:
            # Hammering a blocked edge every tick keeps the IP blocked; rest it.
            _QUOTES_CLIST_PAUSE_UNTIL = now + _QUOTES_CLIST_PAUSE_SEC
            why = "; ".join(errs) or "pages returned no rows"
            clist_note = f"[{await _probe_quote_edges(client)}] ({why})"
            out = []
    if not out:
        try:
            out = await quotes_fallback.fetch_main_quotes_tencent(client)
        except Exception as exc:
            if _MAIN_QUOTES_CACHE:
                return [dict(row) for row in _MAIN_QUOTES_CACHE[1]]
            raise RuntimeError(
                f"main-board quotes empty {clist_note}; fallback: {str(exc)[:80]}"
            ) from exc
        if _MAIN_QUOTES_SOURCE != "tencent":
            log.warning(
                "main-board quotes via Tencent fallback (%s rows); %s", len(out), clist_note
            )
        _MAIN_QUOTES_SOURCE = "tencent"
    # Never let an emergency ~200-row sample replace a full-market cache —
    # that poisons ups/downs (e.g. 200/0) for the whole TTL window.
    if _MAIN_QUOTES_CACHE:
        cached_n = len(_MAIN_QUOTES_CACHE[1])
        if len(out) < MAIN_QUOTES_BREADTH_MIN and cached_n >= MAIN_QUOTES_BREADTH_MIN:
            return [dict(row) for row in _MAIN_QUOTES_CACHE[1]]
        if len(out) < cached_n and len(out) < MAIN_QUOTES_BREADTH_MIN:
            return [dict(row) for row in _MAIN_QUOTES_CACHE[1]]
    _MAIN_QUOTES_CACHE = (now, out)
    return [dict(row) for row in out]


async def _main_quotes_from_clist(
    client: httpx.AsyncClient,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Page SH + SZ main-board clist lists; return mapped rows and error notes."""
    hosts = list(_QUOTES_CLIST_HOSTS)
    sh_rows: list[dict[str, Any]] = []
    sz_rows: list[dict[str, Any]] = []
    errs: list[str] = []
    # Fetch markets independently so one edge death does not discard the other.
    try:
        sh_rows = await _fetch_clist_pages(
            client,
            "m:1+t:2",
            max_pages=18,
            hosts=hosts,
            page_sleep=0.2,
            allow_partial=True,
        )
    except Exception as exc:
        sh_rows = []
        errs.append(f"sh {type(exc).__name__}: {str(exc)[:80]}")
    try:
        sz_rows = await _fetch_clist_pages(
            client,
            "m:0+t:6",
            max_pages=18,
            hosts=hosts,
            page_sleep=0.2,
            allow_partial=True,
        )
    except Exception as exc:
        sz_rows = []
        errs.append(f"sz {type(exc).__name__}: {str(exc)[:80]}")
    # Emergency sample: a few pages beat a hard empty (breadth / auction still usable).
    if len(sh_rows) + len(sz_rows) < 100:
        for fs, bucket in (("m:1+t:2", "sh"), ("m:0+t:6", "sz")):
            try:
                sample = await _fetch_clist_pages(
                    client,
                    fs,
                    max_pages=4,
                    hosts=hosts,
                    page_sleep=0.35,
                    allow_partial=True,
                )
            except Exception:
                sample = []
            if bucket == "sh" and sample:
                sh_rows = sample
            elif bucket == "sz" and sample:
                sz_rows = sample
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*sh_rows, *sz_rows]:
        mapped = _quote_from_diff(item)
        if mapped and mapped["code"] not in seen:
            seen.add(mapped["code"])
            out.append(mapped)
    return out, errs


_MAIN_QUOTES_CACHE: tuple[float, list[dict[str, Any]]] | None = None


_MAIN_QUOTES_SOURCE = "eastmoney"


# After a total clist failure, skip clist for this long and use the fallback.
_QUOTES_CLIST_PAUSE_UNTIL = 0.0


_QUOTES_CLIST_PAUSE_SEC = 600.0


# Fewer clist rows than this counts as a failed pull.
_QUOTES_CLIST_MIN_ROWS = 50


_MAIN_QUOTES_TTL_SEC = 240.0


_MAIN_QUOTES_OPEN_TTL_SEC = 90.0


# Auction used to be 4s and re-pulled ~36 clist pages every tick → disconnect storm.
_MAIN_QUOTES_AUCTION_TTL_SEC = 60.0


# Full SH+SZ main board is ~3000+ names; below this, breadth must not be trusted.
MAIN_QUOTES_BREADTH_MIN = 800
