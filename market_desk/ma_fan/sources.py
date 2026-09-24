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


async def enrich_hits_meta(
    client: httpx.AsyncClient, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach industry, total market cap and shareholder counts onto hits.

    Only rows without ``industry`` are fetched, so merging later slices does not
    re-request earlier ones. Costs one quote batch plus 1–2 holder batches.
    """
    need = [
        normalize_code(h.get("code")) or ""
        for h in items
        if "industry" not in h and normalize_code(h.get("code"))
    ]
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
        h["industry"] = str(m.get("industry") or "")
        if m.get("mv_yi") is not None:
            h["mv_yi"] = m["mv_yi"]
        hd = holders.get(code) or {}
        h["holder_num"] = hd.get("holder_num")
        h["holder_chg_pct"] = hd.get("holder_chg_pct")
        h["holder_avg_wan"] = hd.get("holder_avg_wan")
        h["holder_end"] = hd.get("holder_end")
    return items
