"""Hot boards, board fund flow, and board members with Sina fallback."""

from __future__ import annotations

from typing import Any
import asyncio
import logging
import time
import httpx
from market_desk import board_fallback
from market_desk.config import CONCEPT_JUNK_KEYWORDS, CONSTITUENT_TOP
from market_desk.filters import is_main_board, normalize_code
from market_desk.numbers import num

from market_desk.eastmoney.client import _diff_rows, _fetch_clist_pages, _get_clist_json
from market_desk.eastmoney.quotes import _QUOTES_CLIST_HOSTS

log = logging.getLogger("market_desk.eastmoney")


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
        "amount": num(item.get("f6"), 0.0) or 0.0,
        "mv": num(item.get("f20"), 0.0) or 0.0,
        "up_count": int(num(item.get("f104"), 0) or 0),
        "down_count": int(num(item.get("f105"), 0) or 0),
        "leader_name": str(item.get("f128") or ""),
        "leader_code": normalize_code(item.get("f140")),
        "leader_pct": round(num(item.get("f136"), 0.0) or 0.0, 2),
    }


def _hot_board_from_flow(row: dict[str, Any]) -> dict[str, Any]:
    """Map a fund-flow board row into hot-board card shape (degraded fields)."""
    return {
        "bk": row["bk"],
        "name": row["name"],
        "kind": row.get("kind") or "concept",
        "pct": row.get("pct") or 0.0,
        "amount": 0.0,
        "up_count": 0,
        "down_count": 0,
        "leader_name": str(row.get("leader_name") or ""),
        "leader_code": normalize_code(row.get("leader_code")),
        "leader_pct": round(num(row.get("leader_pct"), 0.0) or 0.0, 2),
    }


_BOARDS_SOURCE = "eastmoney"


# After boards clist comes back empty, skip board / flow / member clist calls
# for this long and serve them from Sina (``board_fallback``).
_BOARDS_CLIST_PAUSE_UNTIL = 0.0


_BOARDS_CLIST_PAUSE_SEC = 600.0


def _boards_clist_paused() -> bool:
    return time.time() < _BOARDS_CLIST_PAUSE_UNTIL


async def fetch_hot_boards(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch concept gainers and the full industry universe.

    Uses ``bypass_backoff`` so quote-edge cooldowns do not wipe board cards.
    When East Money returns nothing (blocked IP), board clist calls are paused
    for ``_BOARDS_CLIST_PAUSE_SEC`` and boards come from Sina instead.
    """
    global _BOARDS_SOURCE, _BOARDS_CLIST_PAUSE_UNTIL
    if not _boards_clist_paused():
        out = await _hot_boards_from_clist(client)
        if out:
            _BOARDS_SOURCE = "eastmoney"
            return out
        _BOARDS_CLIST_PAUSE_UNTIL = time.time() + _BOARDS_CLIST_PAUSE_SEC
    try:
        rows = await board_fallback.fetch_hot_boards_sina(client)
    except Exception as exc:
        log.warning("hot boards: clist empty and sina fallback failed: %r", exc)
        return []
    if _BOARDS_SOURCE != "sina":
        log.warning("hot boards via Sina fallback (%s rows)", len(rows))
    _BOARDS_SOURCE = "sina"
    return rows


async def _hot_boards_from_clist(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Pull hot boards from East Money clist (gainers, then fund-flow rows)."""
    out: list[dict[str, Any]] = []
    try:
        concept_payload, _host = await _get_clist_json(
            client, "m:90+t:3", pz=80, bypass_backoff=True
        )
        for item in _diff_rows(concept_payload):
            mapped = _board_from_diff(item, "concept")
            if mapped:
                out.append(mapped)
    except Exception:
        pass
    try:
        # Prefer delay edge for multi-page industry (same as quotes resilience).
        industry_rows = await _fetch_clist_pages(
            client,
            "m:90+t:2",
            pz=100,
            max_pages=5,
            hosts=list(_QUOTES_CLIST_HOSTS),
            page_sleep=0.15,
            allow_partial=True,
            bypass_backoff=True,
        )
        for item in industry_rows:
            mapped = _board_from_diff(item, "industry")
            if mapped:
                out.append(mapped)
    except Exception:
        pass
    if out:
        board_fallback.remember_em_boards(out)
        return out
    # Same clist edge as fund-flow; keeps boards non-empty when gainers API blips.
    flow_c, flow_i = await asyncio.gather(
        _board_fund_flow_clist(client, "concept", 80, "day"),
        _board_fund_flow_clist(client, "industry", 100, "day"),
    )
    for row in [*flow_c, *flow_i]:
        out.append(_hot_board_from_flow(row))
    return out


_FLOW_FIELDS = (
    ",f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205"
)


# East Money period configs: sort fid + field map for main / share / size buckets.
_FLOW_PERIODS: dict[str, dict[str, Any]] = {
    "day": {
        "fid": "f62",
        "fields": (
            "f12,f13,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,"
            "f128,f140,f136,f204,f205"
        ),
        "main": "f62",
        "main_pct": "f184",
        "super": "f66",
        "large": "f72",
        "mid": "f78",
        "small": "f84",
        "pct": "f3",
    },
    "week": {  # ≈5 trading days
        "fid": "f164",
        "fields": (
            "f12,f13,f14,f2,f3,f109,f164,f165,f166,f167,f168,f169,f170,f171,f172,f173,"
            "f128,f140,f136,f204,f205,f257,f258"
        ),
        "main": "f164",
        "main_pct": "f165",
        "super": "f166",
        "large": "f168",
        "mid": "f170",
        "small": "f172",
        "pct": "f109",  # 5-day pct when available
    },
    "month": {  # ≈10 trading days (旬 proxy)
        "fid": "f174",
        "fields": (
            "f12,f13,f14,f2,f3,f160,f174,f175,f176,f177,f178,f179,f180,f181,f182,f183,"
            "f128,f140,f136,f204,f205,f260,f261"
        ),
        "main": "f174",
        "main_pct": "f175",
        "super": "f176",
        "large": "f178",
        "mid": "f180",
        "small": "f182",
        "pct": "f160",  # 10-day pct when available
    },
}


async def fetch_board_fund_flow(
    client: httpx.AsyncClient,
    kind: str = "industry",
    limit: int = 80,
    period: str = "day",
) -> list[dict[str, Any]]:
    """Fetch board money-flow ranked by main-force net inflow for one period.

    Day flow falls back to Sina (net inflow only) when clist is paused or
    returns nothing; week / month have no fallback and may come back empty.
    """
    rows = [] if _boards_clist_paused() else await _board_fund_flow_clist(
        client, kind, limit, period
    )
    if rows or period != "day":
        return rows
    try:
        return await board_fallback.fetch_board_flow_sina(client, kind, limit)
    except Exception as exc:
        log.debug("sina board flow fallback failed (%s): %r", kind, exc)
        return []


async def _board_fund_flow_clist(
    client: httpx.AsyncClient,
    kind: str,
    limit: int,
    period: str,
) -> list[dict[str, Any]]:
    """Fetch one period of board money-flow from East Money clist."""
    cfg = _FLOW_PERIODS.get(period) or _FLOW_PERIODS["day"]
    fs = "m:90+t:2" if kind == "industry" else "m:90+t:3"
    fid = cfg["fid"]
    fields = cfg["fields"]
    base_fields = {
        "f12", "f13", "f14", "f2", "f3", "f4", "f5", "f6", "f8",
        "f15", "f16", "f17", "f18", "f9", "f20",
        "f104", "f105", "f128", "f140", "f141", "f136",
    }
    extra = "," + ",".join(
        x for x in fields.split(",") if x and x not in base_fields
    )
    payload: dict[str, Any] = {}
    try:
        payload, _host = await _get_clist_json(
            client,
            fs,
            pz=limit,
            pn=1,
            po=1,
            fid=fid,
            extra_fields=extra,
            bypass_backoff=True,
        )
    except Exception:
        payload = {}
    out: list[dict[str, Any]] = []
    for item in _diff_rows(payload):
        mapped = _board_flow_from_diff(item, kind, cfg=cfg, period=period)
        if mapped:
            out.append(mapped)
    return out


def _board_flow_from_diff(
    item: dict[str, Any],
    kind: str,
    *,
    cfg: dict[str, Any] | None = None,
    period: str = "day",
) -> dict[str, Any] | None:
    """Map a clist money-flow row into a normalized board flow dict."""
    conf = cfg or _FLOW_PERIODS["day"]
    name = str(item.get("f14") or "")
    if not name or _is_junk_board(name):
        return None
    code = str(item.get("f12") or "")
    if not code.startswith("BK"):
        return None
    leader = str(item.get("f204") or item.get("f257") or item.get("f128") or "")
    leader_code = normalize_code(
        item.get("f205") or item.get("f258") or item.get("f140")
    )
    pct_key = conf.get("pct") or "f3"
    pct_val = num(item.get(pct_key))
    if pct_val is None:
        pct_val = num(item.get("f3"), 0.0) or 0.0
    return {
        "bk": code,
        "name": name,
        "kind": kind,
        "period": period,
        "pct": round(float(pct_val), 2),
        "main_net": num(item.get(conf["main"])),
        "main_pct": num(item.get(conf["main_pct"])),
        "super_net": num(item.get(conf["super"])),
        "large_net": num(item.get(conf["large"])),
        "mid_net": num(item.get(conf["mid"])),
        "small_net": num(item.get(conf["small"])),
        "leader_name": leader,
        "leader_code": leader_code,
        "leader_pct": round(num(item.get("f136"), 0.0) or 0.0, 2),
    }


async def fetch_board_members(
    client: httpx.AsyncClient, bk: str, weakest: bool = False
) -> list[dict[str, Any]]:
    """Fetch board constituents; weakest=True returns the largest losers first.

    Constituent lists change slowly intraday; cache briefly to cut refresh fan-out.
    Falls back to the board's Sina node when clist is paused or returns nothing.
    """
    key = f"{str(bk or '').upper()}:{'w' if weakest else 's'}"
    now = time.time()
    hit = _BOARD_MEMBERS_CACHE.get(key)
    if hit and now - hit[0] < _BOARD_MEMBERS_TTL_SEC:
        return [dict(row) for row in hit[1]]
    members: list[dict[str, Any]] = []
    if str(bk or "").upper().startswith("BK") and not _boards_clist_paused():
        members = await _board_members_clist(client, bk, weakest)
    if not members:
        try:
            members = await board_fallback.fetch_board_members_sina(client, bk, weakest=weakest)
        except Exception as exc:
            log.debug("sina board members fallback failed (%s): %r", bk, exc)
            members = []
    if members:
        _BOARD_MEMBERS_CACHE[key] = (now, members)
    return [dict(row) for row in members]


async def _board_members_clist(
    client: httpx.AsyncClient, bk: str, weakest: bool
) -> list[dict[str, Any]]:
    """Fetch main-board constituents of one East Money board via clist."""
    try:
        payload, _host = await _get_clist_json(
            client,
            f"b:{bk}+f:!50",
            pz=30,
            po=0 if weakest else 1,
            bypass_backoff=True,
        )
    except Exception:
        return []
    members: list[dict[str, Any]] = []
    for item in _diff_rows(payload):
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
                "amount": float(num(item.get("f6"), 0.0) or 0.0),
                "price": num(item.get("f2")),
                "high": num(item.get("f15")),
                "low": num(item.get("f16")),
                "mv_yi": mv_yi,
            }
        )
        if len(members) >= CONSTITUENT_TOP:
            break
    return members


_BOARD_MEMBERS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


_BOARD_MEMBERS_TTL_SEC = 90.0
