"""Hot boards, board fund flow, and board members with Sina fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import asyncio
import logging
import time
import zlib
import httpx
from market_desk import board_fallback
from market_desk.calendar import is_trading_day
from market_desk.config import (
    BOARD_FLOW_TTL_SEC,
    BOARD_MEMBERS_TTL_JITTER,
    BOARD_MEMBERS_TTL_SEC,
    BOARDS_DEGRADE_STREAK,
    BOARDS_INDUSTRY_TTL_SEC,
    BOARDS_PROBE_RETRY_SEC,
    BOARDS_RESTORE_STREAK,
    CONCEPT_JUNK_KEYWORDS,
    CONSTITUENT_TOP,
)
from market_desk.filters import is_main_board, normalize_code
from market_desk.numbers import num

from market_desk.eastmoney.client import _diff_rows, _fetch_clist_pages, _get_clist_json
from market_desk.eastmoney.quotes import _QUOTES_CLIST_HOSTS

log = logging.getLogger("market_desk.eastmoney")

try:
    from zoneinfo import ZoneInfo

    _CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    _CN_TZ = timezone(timedelta(hours=8))


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

# Consecutive successful EM probes while still serving Sina (restore gate).
_BOARDS_RESTORE_OK = 0
# Consecutive in-session EM failures while still on East Money (degrade gate).
_BOARDS_FAIL_STREAK = 0
# Today's source flips: [{"at": "HH:MM:SS", "to": "sina|eastmoney", "why": str}].
_BOARDS_SWITCH_DAY = ""
_BOARDS_SWITCH_LOG: list[dict[str, Any]] = []


def _cn_now() -> datetime:
    return datetime.now(_CN_TZ)


def in_decision_window(now: datetime | None = None) -> bool:
    """Return True inside continuous trading (09:30–11:30, 13:00–15:00) on a trading day.

    Board-source flips are only allowed outside this window, except for the
    unavoidable East Money → Sina degrade when East Money stops answering.
    """
    cur = now or _cn_now()
    if not is_trading_day(cur):
        return False
    minutes = cur.hour * 60 + cur.minute
    return (9 * 60 + 30 <= minutes < 11 * 60 + 30) or (13 * 60 <= minutes < 15 * 60)


def _boards_clist_paused() -> bool:
    """Return True when board / flow / member calls must skip East Money.

    Besides the timed pause, a Sina-degraded source stays locked for the rest
    of the half-session so boards, members and flow come from one universe.
    """
    if time.time() < _BOARDS_CLIST_PAUSE_UNTIL:
        return True
    return _BOARDS_SOURCE == "sina" and in_decision_window()


def _set_boards_source(source: str, why: str) -> None:
    """Switch the board source and append the flip to today's switch log."""
    global _BOARDS_SOURCE, _BOARDS_SWITCH_DAY, _BOARDS_SWITCH_LOG
    if source == _BOARDS_SOURCE:
        return
    now = _cn_now()
    day = now.strftime("%Y-%m-%d")
    if day != _BOARDS_SWITCH_DAY:
        _BOARDS_SWITCH_DAY = day
        _BOARDS_SWITCH_LOG = []
    _BOARDS_SWITCH_LOG.append({"at": now.strftime("%H:%M:%S"), "to": source, "why": why})
    _BOARDS_SWITCH_LOG = _BOARDS_SWITCH_LOG[-40:]
    log.warning(
        "boards source %s -> %s (%s) · flips today=%s",
        _BOARDS_SOURCE, source, why, len(_BOARDS_SWITCH_LOG),
    )
    _BOARDS_SOURCE = source


def boards_switch_stats() -> dict[str, Any]:
    """Return today's board-source flip count and the most recent flips."""
    day = _cn_now().strftime("%Y-%m-%d")
    rows = list(_BOARDS_SWITCH_LOG) if _BOARDS_SWITCH_DAY == day else []
    return {
        "source": _BOARDS_SOURCE,
        "switches": len(rows),
        "log": rows[-6:],
        "restore_ok": int(_BOARDS_RESTORE_OK),
        "locked": _BOARDS_SOURCE == "sina" and in_decision_window(),
    }


async def fetch_hot_boards(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch concept gainers and the full industry universe.

    Uses ``bypass_backoff`` so quote-edge cooldowns do not wipe board cards.
    When East Money returns nothing (blocked IP), boards come from Sina and the
    source stays on Sina until the next break; there East Money is re-probed and
    only restored after ``BOARDS_RESTORE_STREAK`` consecutive successes.
    """
    global _BOARDS_CLIST_PAUSE_UNTIL, _BOARDS_RESTORE_OK, _BOARDS_FAIL_STREAK
    if not _boards_clist_paused():
        out = await _hot_boards_from_clist(client)
        if out:
            _BOARDS_FAIL_STREAK = 0
            if _BOARDS_SOURCE != "sina":
                return out
            _BOARDS_RESTORE_OK += 1
            if _BOARDS_RESTORE_OK >= int(BOARDS_RESTORE_STREAK):
                _BOARDS_RESTORE_OK = 0
                _set_boards_source("eastmoney", "restored")
                return out
        else:
            _BOARDS_RESTORE_OK = 0
            in_window = in_decision_window()
            if _BOARDS_SOURCE != "sina" and in_window:
                _BOARDS_FAIL_STREAK += 1
                if _BOARDS_FAIL_STREAK < int(BOARDS_DEGRADE_STREAK):
                    # One blip: empty boards make refresh reuse last round's cards.
                    return []
            _BOARDS_FAIL_STREAK = 0
            pause = _BOARDS_CLIST_PAUSE_SEC if in_window else float(BOARDS_PROBE_RETRY_SEC)
            _BOARDS_CLIST_PAUSE_UNTIL = time.time() + pause
    try:
        rows = await board_fallback.fetch_hot_boards_sina(client)
    except Exception as exc:
        log.warning("hot boards: clist empty and sina fallback failed: %r", exc)
        return []
    if _BOARDS_SOURCE != "sina":
        log.warning("hot boards via Sina fallback (%s rows)", len(rows))
    _set_boards_source("sina", "clist empty")
    return rows


async def probe_boards_source(client: httpx.AsyncClient) -> str:
    """Re-probe East Money boards during a break while the source is Sina.

    Called from the idle refresh loop at lunch so the afternoon session can
    open on East Money instead of staying locked on Sina until the close.
    """
    if _BOARDS_SOURCE != "sina" or _boards_clist_paused():
        return _BOARDS_SOURCE
    await fetch_hot_boards(client)
    return _BOARDS_SOURCE


async def _hot_boards_from_clist(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Pull hot boards from East Money clist (gainers, then fund-flow rows).

    Concept gainers are fetched every call; the paged industry universe is reused
    for ``BOARDS_INDUSTRY_TTL_SEC`` but only while the concept call itself succeeds,
    so a blocked edge is still noticed on the very next round.
    """
    global _INDUSTRY_ROWS_CACHE
    out: list[dict[str, Any]] = []
    concept_ok = False
    try:
        concept_payload, _host = await _get_clist_json(
            client, "m:90+t:3", pz=80, bypass_backoff=True
        )
        for item in _diff_rows(concept_payload):
            mapped = _board_from_diff(item, "concept")
            if mapped:
                out.append(mapped)
        concept_ok = bool(out)
    except Exception:
        pass
    cached = _INDUSTRY_ROWS_CACHE
    if concept_ok and cached and time.time() - cached[0] < float(BOARDS_INDUSTRY_TTL_SEC):
        out.extend(dict(row) for row in cached[1])
    else:
        industry: list[dict[str, Any]] = []
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
                    industry.append(mapped)
        except Exception:
            pass
        if industry:
            _INDUSTRY_ROWS_CACHE = (time.time(), [dict(row) for row in industry])
        out.extend(industry)
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
    Non-empty East Money rows are cached per period (``BOARD_FLOW_TTL_SEC``).
    """
    key = (kind, int(limit), period)
    hit = _EM_FLOW_CACHE.get(key)
    ttl = float(BOARD_FLOW_TTL_SEC.get(period, 60.0))
    if hit and time.time() - hit[0] < ttl and not _boards_clist_paused():
        return [dict(row) for row in hit[1]]
    rows = [] if _boards_clist_paused() else await _board_fund_flow_clist(
        client, kind, limit, period
    )
    if rows:
        _EM_FLOW_CACHE[key] = (time.time(), [dict(row) for row in rows])
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
    if hit and now - hit[0] < _members_ttl(key):
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


async def fetch_board_codes_em(client: httpx.AsyncClient, bk: str) -> list[str]:
    """Return every constituent code of one East Money board (alias overlap learning).

    Returns [] when board clist is paused / locked on Sina or the call fails.
    """
    if not str(bk or "").upper().startswith("BK") or _boards_clist_paused():
        return []
    try:
        rows = await _fetch_clist_pages(
            client, f"b:{bk}", pz=100, max_pages=12, page_sleep=0.2, bypass_backoff=True
        )
    except Exception:
        return []
    return list(dict.fromkeys(c for c in (normalize_code(r.get("f12")) for r in rows) if c))


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


def _members_ttl(key: str) -> float:
    """Per-board cache TTL: base plus a stable 0..jitter offset so refetches stagger."""
    jitter = int(BOARD_MEMBERS_TTL_JITTER)
    extra = zlib.crc32(key.encode("utf-8")) % (jitter + 1) if jitter > 0 else 0
    return float(BOARD_MEMBERS_TTL_SEC) + float(extra)


_BOARD_MEMBERS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


# Paged East Money industry universe: (fetched_at, mapped rows).
_INDUSTRY_ROWS_CACHE: tuple[float, list[dict[str, Any]]] | None = None


# East Money board fund flow keyed by (kind, limit, period): (fetched_at, rows).
_EM_FLOW_CACHE: dict[tuple[str, int, str], tuple[float, list[dict[str, Any]]]] = {}
