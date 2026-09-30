"""Outbound data sources: Sina amount ranking and East Money meta / holders."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from market_desk.eastmoney import fetch_holder_stats_many, fetch_stock_meta_many
from market_desk.filters import is_chinext_or_star, is_main_board, is_st, normalize_code
from market_desk.ma_fan.job import MA_FAN_PAGE_DELAY_S

log = logging.getLogger("market_desk.ma_fan")


def _board_ok(code: str, boards: str) -> bool:
    """Return True when ``code`` is in the requested board set."""
    if boards == "main":
        return is_main_board(code)
    if boards == "growth":
        return is_chinext_or_star(code)
    return is_main_board(code) or is_chinext_or_star(code)


async def load_universe(
    client: httpx.AsyncClient,
    *,
    boards: str = "all",
    need: int = 400,
    min_amount_yi: float = 1.2,
) -> list[dict[str, Any]]:
    """Load amount-ranked names from Sina until ``need`` rows (or pages run out)."""
    if boards == "main":
        nodes = ["hs_a"]
    elif boards == "growth":
        nodes = ["cyb", "kcb"]
    else:
        nodes = ["hs_a", "cyb", "kcb"]
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    per_page = 80
    want = max(50, int(need or 400))
    pages = max(3, min(20, (want // max(1, len(nodes)) // per_page) + 3))
    for node in nodes:
        for page in range(1, pages + 1):
            url = (
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/"
                "json_v2.php/Market_Center.getHQNodeData"
                f"?page={page}&num={per_page}&sort=amount&asc=0&node={node}"
                "&symbol=&_s_r_a=init"
            )
            try:
                resp = await client.get(url, timeout=20.0)
                resp.raise_for_status()
                rows = resp.json()
            except Exception as exc:
                log.warning("ma_fan universe page failed node=%s page=%s: %r", node, page, exc)
                break
            if not isinstance(rows, list) or not rows:
                break
            for item in rows:
                if not isinstance(item, dict):
                    continue
                code = normalize_code(item.get("code"))
                name = str(item.get("name") or "")
                if not code or code in seen:
                    continue
                if not _board_ok(code, boards) or is_st(name):
                    continue
                try:
                    amt = float(item.get("amount") or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                if amt < min_amount_yi * 1e8:
                    continue
                try:
                    pct = float(
                        item.get("changepercent") or item.get("changePercent") or 0
                    )
                except (TypeError, ValueError):
                    pct = None
                seen.add(code)
                pool.append(
                    {
                        "code": code,
                        "name": name,
                        "amount": amt,
                        "pct": pct,
                        "last": item.get("trade"),
                        "mktcap_wan": item.get("mktcap"),
                    }
                )
            await asyncio.sleep(MA_FAN_PAGE_DELAY_S)
            if len(pool) >= want * 2:
                break
        if len(pool) >= want * 2:
            break
    pool.sort(key=lambda x: float(x.get("amount") or 0), reverse=True)
    return pool[:want]


def _wan_to_yi(v: Any) -> float | None:
    """Convert a 万元 amount (Sina ``mktcap``) into 亿元, or None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f / 1e4, 1) if f > 0 else None


def _needs_meta(h: dict[str, Any]) -> bool:
    """Return True when a hit still lacks industry (missing key or empty string)."""
    return bool(normalize_code(h.get("code"))) and not str(h.get("industry") or "").strip()


async def enrich_hits_meta(
    client: httpx.AsyncClient, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach industry, total market cap and shareholder counts onto hits.

    Rows whose ``industry`` is missing or empty are (re)fetched, so a transient
    source failure in one slice is repaired by the next slice or page load.
    Fetched blanks never overwrite values already present. Costs one quote batch
    plus 1–2 holder batches.
    """
    need = [normalize_code(h.get("code")) for h in items if _needs_meta(h)]
    if not need:
        return items
    meta: dict[str, dict[str, Any]] = {}
    holders: dict[str, dict[str, Any]] = {}
    try:
        meta = await fetch_stock_meta_many(client, need)
    except Exception:
        log.exception("ma_fan stock meta failed n=%s", len(need))
    try:
        holders = await fetch_holder_stats_many(client, need)
    except Exception:
        log.exception("ma_fan holder stats failed n=%s", len(need))
    wanted = set(need)
    for h in items:
        code = normalize_code(h.get("code")) or ""
        if code not in wanted:
            continue
        m = meta.get(code) or {}
        industry = str(m.get("industry") or "").strip()
        if industry or "industry" not in h:
            h["industry"] = industry
        if m.get("mv_yi") is not None:
            h["mv_yi"] = m["mv_yi"]
        hd = holders.get(code) or {}
        for key in ("holder_num", "holder_chg_pct", "holder_avg_wan", "holder_end"):
            if hd.get(key) is not None or key not in h:
                h[key] = hd.get(key)
    return items


# Page-load backfill: at most one outbound attempt per trade date per window.
_BACKFILL_AT: dict[str, float] = {}
MA_FAN_BACKFILL_RETRY_S = 600.0


async def backfill_day_meta(
    day: str, body: dict[str, Any], *, timeout_s: float = 8.0
) -> bool:
    """Fill missing industry / holder meta on a stored day payload in place.

    Throttled per ``day`` so repeated page loads do not hammer the source.

    Returns:
        True when at least one row gained a non-empty industry.
    """
    import time

    items = list(body.get("items") or [])
    if not any(_needs_meta(h) for h in items):
        return False
    now = time.monotonic()
    if now - _BACKFILL_AT.get(day, -1e9) < MA_FAN_BACKFILL_RETRY_S:
        return False
    _BACKFILL_AT[day] = now
    before = sum(1 for h in items if not _needs_meta(h))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s), trust_env=False) as client:
            await asyncio.wait_for(enrich_hits_meta(client, items), timeout=timeout_s * 2)
    except Exception as exc:
        log.warning("ma_fan meta backfill failed day=%s: %r", day, exc)
        return False
    after = sum(1 for h in items if not _needs_meta(h))
    body["items"] = items
    if after > before:
        log.info("ma_fan meta backfill day=%s filled=%s", day, after - before)
    return after > before
