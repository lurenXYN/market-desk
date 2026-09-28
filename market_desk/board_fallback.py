"""Fallback boards / board money-flow / board members from Sina.

East Money answers board ``clist`` calls from the VPS with dropped connections
or ``data: null``, which blanks hot boards, the funds tab and constituents.
Sina keeps working and covers the same three needs:

* boards — Sina industry (``new_*``, ~49) and concept (``gn_*``, ~170) lists;
* money flow — ``MoneyFlow.ssl_bkzj_bk`` (net inflow only, no order buckets);
* members — ``Market_Center.getHQNodeData`` for one Sina node.

Sina names that exactly match an East Money board keep that ``BK`` code (via
``seeds/em_boards.json`` plus any live clist pull) so history, favorites and
news-radar links stay intact; the rest use ``SINA:<node>`` keys.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from market_desk.config import CONCEPT_JUNK_KEYWORDS, CONSTITUENT_TOP, HTTP_HEADERS
from market_desk.filters import is_main_board, normalize_code
from market_desk.numbers import num

log = logging.getLogger("market_desk.board_fallback")

SEED_PATH = Path(__file__).with_name("seeds") / "em_boards.json"
SINA_KEY_PREFIX = "SINA:"
BOARDS_TTL_S = 20.0
FLOW_TTL_S = 30.0
MEMBERS_TTL_S = 90.0
MEMBERS_PAGE_SIZE = 80

_SINA_LISTS: tuple[tuple[str, str], ...] = (
    ("industry", "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"),
    ("concept", "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=class"),
)
_SINA_API = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
_SINA_FLOW_FENLEI = {"industry": "0", "concept": "1"}

# name -> (bk, kind) for East Money boards; bk -> name for reverse lookups.
_EM_BY_NAME: dict[str, tuple[str, str]] | None = None
_EM_NAME_BY_BK: dict[str, str] = {}
# Upper-cased board key -> Sina node, and Sina name -> node (filled by list pulls).
_NODE_BY_KEY: dict[str, str] = {}
_NODE_BY_NAME: dict[str, str] = {}
_BOARDS_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_FLOW_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_MEMBERS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _is_junk(name: str) -> bool:
    return any(k in name for k in CONCEPT_JUNK_KEYWORDS)


def _sina_code(symbol: Any) -> str:
    """Turn a Sina symbol such as ``sh600418`` into a six-digit code."""
    raw = str(symbol or "").strip().lower()
    if raw[:2] in ("sh", "sz", "bj"):
        raw = raw[2:]
    return normalize_code(raw) if raw else ""


def _em_boards() -> dict[str, tuple[str, str]]:
    """Return the East Money ``name -> (bk, kind)`` map, loading the seed once."""
    global _EM_BY_NAME
    if _EM_BY_NAME is None:
        _EM_BY_NAME = {}
        try:
            body = json.loads(SEED_PATH.read_text(encoding="utf-8"))
            for bk, (name, kind) in (body.get("boards") or {}).items():
                _EM_BY_NAME.setdefault(str(name), (str(bk).upper(), str(kind)))
                _EM_NAME_BY_BK[str(bk).upper()] = str(name)
        except Exception:
            log.exception("load East Money board seed failed")
    return _EM_BY_NAME


def remember_em_boards(rows: list[dict[str, Any]]) -> None:
    """Merge live East Money board rows (``bk``/``name``/``kind``) into the name map."""
    names = _em_boards()
    for row in rows:
        bk = str(row.get("bk") or "").upper()
        name = str(row.get("name") or "")
        if bk.startswith("BK") and name:
            names[name] = (bk, str(row.get("kind") or "concept"))
            _EM_NAME_BY_BK[bk] = name


def _board_key(name: str, node: str, kind: str) -> tuple[str, str]:
    """Map a Sina board to ``(bk, kind)``, preferring the East Money code."""
    hit = _em_boards().get(name)
    if hit:
        return hit
    return f"{SINA_KEY_PREFIX}{node}", kind


def _parse_sina_board_js(text: str) -> list[list[str]]:
    """Parse ``var X = {"node": "node,name,..."}`` into comma-split rows."""
    try:
        body = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except ValueError:
        return []
    return [str(line).split(",") for line in body.values() if line]


def _board_from_sina(parts: list[str], kind: str) -> dict[str, Any] | None:
    """Map one Sina list row into the ``eastmoney._board_from_diff`` shape.

    Columns: node, name, count, avg price, change, pct, volume, amount,
    leader symbol, leader pct, leader price, leader change, leader name.
    """
    if len(parts) < 13:
        return None
    node, name = parts[0].strip(), parts[1].strip()
    if not node or not name or _is_junk(name):
        return None
    bk, em_kind = _board_key(name, node, kind)
    _NODE_BY_KEY[bk.upper()] = node
    _NODE_BY_NAME[name] = node
    return {
        "bk": bk,
        "name": name,
        "kind": em_kind,
        "pct": round(num(parts[5], 0.0) or 0.0, 2),
        "amount": num(parts[7], 0.0) or 0.0,
        "up_count": 0,
        "down_count": 0,
        "leader_name": parts[12].strip(),
        "leader_code": _sina_code(parts[8]),
        "leader_pct": round(num(parts[9], 0.0) or 0.0, 2),
        "source": "sina",
    }


async def fetch_hot_boards_sina(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch Sina industry + concept boards in hot-board card shape."""
    global _BOARDS_CACHE
    now = time.time()
    if _BOARDS_CACHE and now - _BOARDS_CACHE[0] < BOARDS_TTL_S:
        return [dict(row) for row in _BOARDS_CACHE[1]]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    errs: list[str] = []
    for kind, url in _SINA_LISTS:
        try:
            resp = await client.get(url, headers=HTTP_HEADERS, timeout=15.0)
            resp.raise_for_status()
            rows = _parse_sina_board_js(resp.content.decode("gbk", errors="ignore"))
        except Exception as exc:
            errs.append(f"{kind}: {type(exc).__name__}")
            continue
        for parts in rows:
            board = _board_from_sina(parts, kind)
            if board and board["bk"] not in seen:
                seen.add(board["bk"])
                out.append(board)
    if not out:
        raise RuntimeError(f"sina boards empty ({'; '.join(errs) or 'no rows'})")
    _BOARDS_CACHE = (now, out)
    return [dict(row) for row in out]


def _flow_from_sina(row: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """Map one Sina board money-flow row into ``_board_flow_from_diff`` shape."""
    node = str(row.get("category") or "")
    name = str(row.get("name") or "")
    if not node or not name or _is_junk(name):
        return None
    bk, em_kind = _board_key(name, node, kind)
    _NODE_BY_KEY.setdefault(bk.upper(), node)
    _NODE_BY_NAME.setdefault(name, node)
    ratio = num(row.get("ratioamount"))
    pct = num(row.get("avg_changeratio"), 0.0) or 0.0
    leader_pct = num(row.get("ts_changeratio"), 0.0) or 0.0
    return {
        "bk": bk,
        "name": name,
        "kind": em_kind,
        "period": "day",
        "pct": round(pct * 100.0, 2),
        "main_net": num(row.get("netamount")),
        "main_pct": None if ratio is None else round(ratio * 100.0, 2),
        "super_net": None,
        "large_net": None,
        "mid_net": None,
        "small_net": None,
        "leader_name": str(row.get("ts_name") or ""),
        "leader_code": _sina_code(row.get("ts_symbol")),
        "leader_pct": round(leader_pct * 100.0, 2),
        "source": "sina",
    }


async def fetch_board_flow_sina(
    client: httpx.AsyncClient, kind: str = "industry", limit: int = 80
) -> list[dict[str, Any]]:
    """Fetch today's Sina board money-flow ranked by net inflow."""
    key = f"{kind}:{limit}"
    now = time.time()
    hit = _FLOW_CACHE.get(key)
    if hit and now - hit[0] < FLOW_TTL_S:
        return [dict(row) for row in hit[1]]
    resp = await client.get(
        f"{_SINA_API}/MoneyFlow.ssl_bkzj_bk",
        params={
            "page": 1,
            "num": max(1, int(limit)),
            "sort": "netamount",
            "asc": 0,
            "fenlei": _SINA_FLOW_FENLEI.get(kind, "0"),
        },
        headers=HTTP_HEADERS,
        timeout=15.0,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        raise RuntimeError(f"sina board flow non-list payload: {str(rows)[:60]}")
    target = "industry" if kind == "industry" else "concept"
    out: list[dict[str, Any]] = []
    for row in rows:
        mapped = _flow_from_sina(row or {}, target)
        if mapped:
            out.append(mapped)
    _FLOW_CACHE[key] = (now, out)
    return [dict(row) for row in out]


async def sina_node_for(client: httpx.AsyncClient, bk: str) -> str | None:
    """Resolve a board key (``BK…`` or ``SINA:…``) to a Sina node, or None."""
    key = str(bk or "").strip()
    if key.upper().startswith(SINA_KEY_PREFIX):
        return _NODE_BY_KEY.get(key.upper()) or key[len(SINA_KEY_PREFIX) :]
    node = _NODE_BY_KEY.get(key.upper())
    if node:
        return node
    _em_boards()
    name = _EM_NAME_BY_BK.get(key.upper())
    if not name:
        return None
    if not _NODE_BY_NAME:
        try:
            await fetch_hot_boards_sina(client)
        except Exception:
            return None
    return _NODE_BY_NAME.get(name)


def _member_from_sina(item: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Sina node quote into the ``fetch_board_members`` row shape."""
    code = _sina_code(item.get("code") or item.get("symbol"))
    if not code or not is_main_board(code):
        return None
    mcap_wan = num(item.get("mktcap"))
    return {
        "code": code,
        "name": str(item.get("name") or ""),
        "pct": round(num(item.get("changepercent"), 0.0) or 0.0, 2),
        "turnover": round(num(item.get("turnoverratio"), 0.0) or 0.0, 2),
        "amount": float(num(item.get("amount"), 0.0) or 0.0),
        "price": num(item.get("trade")),
        "high": num(item.get("high")),
        "low": num(item.get("low")),
        "mv_yi": None if not mcap_wan or mcap_wan <= 0 else round(float(mcap_wan) / 1e4, 2),
    }


async def fetch_board_members_sina(
    client: httpx.AsyncClient, bk: str, weakest: bool = False
) -> list[dict[str, Any]]:
    """Fetch main-board constituents of one board from its Sina node.

    Returns [] when the board has no Sina counterpart.
    """
    node = await sina_node_for(client, bk)
    if not node:
        return []
    key = f"{node}:{'w' if weakest else 's'}"
    now = time.time()
    hit = _MEMBERS_CACHE.get(key)
    if hit and now - hit[0] < MEMBERS_TTL_S:
        return [dict(row) for row in hit[1]]
    resp = await client.get(
        f"{_SINA_API}/Market_Center.getHQNodeData",
        params={
            "page": 1,
            "num": MEMBERS_PAGE_SIZE,
            "sort": "changepercent",
            "asc": 1 if weakest else 0,
            "node": node,
        },
        headers=HTTP_HEADERS,
        timeout=15.0,
    )
    resp.raise_for_status()
    rows = resp.json()
    members: list[dict[str, Any]] = []
    for item in rows if isinstance(rows, list) else []:
        mapped = _member_from_sina(item or {})
        if mapped:
            members.append(mapped)
        if len(members) >= CONSTITUENT_TOP:
            break
    _MEMBERS_CACHE[key] = (now, members)
    return [dict(row) for row in members]
