"""Fallback main-board quotes for when East Money list endpoints are blocked.

East Money cuts ``clist`` connections for some IPs (seen on the VPS) while its
per-code endpoints, Tencent and Sina keep working. The fallback keeps breadth /
auction / temperature alive:

* universe — main-board codes, refreshed from Sina node pages at most once a
  day (or from any successful East Money pull) and persisted in ``settings`` so
  restarts do not need Sina;
* quotes — Tencent batch quotes over that universe, in small paced chunks.

Row shape matches ``eastmoney._quote_from_diff``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from market_desk.config import HTTP_HEADERS
from market_desk.filters import is_main_board, normalize_code
from market_desk.numbers import num

log = logging.getLogger("market_desk.quotes_fallback")

UNIVERSE_SETTING_KEY = "main_board_universe"
# SH+SZ main board is ~3200 names. Only lists at least this long are cached /
# persisted (clist often dies mid-list); shorter Sina lists are used uncached.
UNIVERSE_FULL_CODES = 2800
UNIVERSE_MIN_CODES = 1000
TENCENT_CHUNK = 80
TENCENT_CONCURRENCY = 2
TENCENT_CHUNK_DELAY_S = 0.15
SINA_PAGE_DELAY_S = 0.3
SINA_MAX_PAGES = 40

_SINA_NODE_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1"
    "&node={node}&symbol=&_s_r_a=init"
)
# Sorted by symbol, main board comes first; stop at STAR (688) / ChiNext (300).
_SINA_NODES: tuple[tuple[str, str], ...] = (("sh_a", "688000"), ("sz_a", "300000"))

_UNIVERSE: tuple[str, list[str]] | None = None


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def remember_universe(codes: list[str]) -> None:
    """Record today's main-board codes when the list looks complete.

    A longer list replaces a shorter one from the same day; partial pulls
    (below ``UNIVERSE_FULL_CODES``) are ignored.
    """
    global _UNIVERSE
    day = _today()
    if _UNIVERSE and _UNIVERSE[0] == day and len(codes) <= len(_UNIVERSE[1]):
        return
    clean = sorted({c for c in (normalize_code(x) for x in codes) if c and is_main_board(c)})
    if len(clean) < UNIVERSE_FULL_CODES:
        return
    if _UNIVERSE and _UNIVERSE[0] == day and len(clean) <= len(_UNIVERSE[1]):
        return
    _UNIVERSE = (day, clean)
    try:
        from market_desk.db import save_setting

        save_setting(UNIVERSE_SETTING_KEY, {"day": day, "codes": clean})
    except Exception:
        log.exception("persist main-board universe failed")


async def _universe_from_sina(client: httpx.AsyncClient) -> list[str]:
    """Page Sina A-share nodes by symbol and keep main-board codes."""
    out: list[str] = []
    for node, stop_at in _SINA_NODES:
        for page in range(1, SINA_MAX_PAGES + 1):
            resp = await client.get(_SINA_NODE_URL.format(page=page, node=node), timeout=15.0)
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                break
            done = False
            for item in rows:
                code = normalize_code((item or {}).get("code"))
                if not code:
                    continue
                if code >= stop_at:
                    done = True
                    break
                if is_main_board(code):
                    out.append(code)
            if done or len(rows) < 100:
                break
            await asyncio.sleep(SINA_PAGE_DELAY_S)
    return sorted(set(out))


async def load_universe(client: httpx.AsyncClient) -> list[str]:
    """Return main-board codes: memory → today's persisted copy → Sina → any persisted copy."""
    global _UNIVERSE
    day = _today()
    if _UNIVERSE and _UNIVERSE[0] == day:
        return list(_UNIVERSE[1])
    stored: dict[str, Any] = {}
    try:
        from market_desk.db import load_setting

        stored = load_setting(UNIVERSE_SETTING_KEY) or {}
    except Exception:
        stored = {}
    stored_codes = [str(c) for c in (stored.get("codes") or []) if c]
    if stored.get("day") == day and len(stored_codes) >= UNIVERSE_FULL_CODES:
        _UNIVERSE = (day, stored_codes)
        return list(stored_codes)
    try:
        codes = await _universe_from_sina(client)
    except Exception as exc:
        log.warning("sina main-board universe failed: %r", exc)
        codes = []
    if len(codes) >= UNIVERSE_FULL_CODES:
        remember_universe(codes)
        return codes
    if len(stored_codes) >= UNIVERSE_FULL_CODES:
        log.info("main-board universe: reuse stored %s (day=%s)", len(stored_codes), stored.get("day"))
        _UNIVERSE = (day, stored_codes)
        return list(stored_codes)
    if len(codes) >= UNIVERSE_MIN_CODES:
        log.warning("main-board universe partial from sina: %s codes (not cached)", len(codes))
        return codes
    return []


def _row_from_tencent(fields: list[str]) -> dict[str, Any] | None:
    """Map one Tencent ``~`` record into the East Money quote row shape."""
    if len(fields) < 35:
        return None
    code = normalize_code(fields[2])
    if not code or not is_main_board(code):
        return None
    prev = num(fields[4])
    open_px = num(fields[5])
    if open_px is not None and open_px <= 0:
        open_px = None
    open_pct = None
    if prev and open_px is not None:
        open_pct = (open_px / prev - 1.0) * 100.0
    amount_wan = num(fields[37]) if len(fields) > 37 else None
    mv = num(fields[45]) if len(fields) > 45 else None
    return {
        "code": code,
        "name": str(fields[1] or ""),
        "price": num(fields[3]),
        "pct": num(fields[32]),
        "open": open_px,
        "prev": prev,
        "open_pct": open_pct,
        "high": num(fields[33]),
        "low": num(fields[34]),
        "amount": float(amount_wan or 0.0) * 1e4,
        "turnover": num(fields[38], 0.0) if len(fields) > 38 else 0.0,
        "mv_yi": round(float(mv), 2) if mv and mv > 0 else None,
    }


def _parse_tencent(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for chunk in text.split(";"):
        if '="' not in chunk:
            continue
        body = chunk.split('="', 1)[1].rstrip('"\n\r ')
        row = _row_from_tencent(body.split("~"))
        if row:
            out.append(row)
    return out


async def fetch_main_quotes_tencent(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch main-board quotes from Tencent over the cached universe."""
    from market_desk.tencent import tencent_symbol

    codes = await load_universe(client)
    if not codes:
        raise RuntimeError("fallback universe empty (sina + stored)")
    chunks = [codes[i : i + TENCENT_CHUNK] for i in range(0, len(codes), TENCENT_CHUNK)]
    sem = asyncio.Semaphore(TENCENT_CONCURRENCY)
    fails = 0

    async def one(batch: list[str]) -> list[dict[str, Any]]:
        nonlocal fails
        async with sem:
            url = "https://qt.gtimg.cn/q=" + ",".join(tencent_symbol(c) for c in batch)
            try:
                resp = await client.get(url, headers=HTTP_HEADERS, timeout=15.0)
                resp.raise_for_status()
                rows = _parse_tencent(resp.content.decode("gbk", errors="ignore"))
            except Exception as exc:
                fails += 1
                log.debug("tencent batch failed: %r", exc)
                rows = []
            await asyncio.sleep(TENCENT_CHUNK_DELAY_S)
            return rows

    parts = await asyncio.gather(*[one(b) for b in chunks])
    out = [row for part in parts for row in part]
    if not out:
        raise RuntimeError(f"tencent fallback returned no rows ({fails}/{len(chunks)} batches failed)")
    return out
