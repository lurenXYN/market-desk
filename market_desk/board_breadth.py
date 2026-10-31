"""Fill Sina board ``up_count`` / ``down_count`` from member snaps × live quotes.

East Money clist ships breadth fields; Sina board lists leave them at 0, which
skews hot ranking and ice status. Prefer East Money constituent snapshots when
present, else Sina snaps, then fall back to already-fetched member rows.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("market_desk.board_breadth")


def _quote_map(quotes: list[dict[str, Any]] | dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Index quotes by 6-digit code."""
    if isinstance(quotes, dict):
        return {str(k).zfill(6): v for k, v in quotes.items() if isinstance(v, dict)}
    out: dict[str, dict[str, Any]] = {}
    for q in quotes or []:
        if not isinstance(q, dict):
            continue
        code = str(q.get("code") or "").zfill(6)
        if code and code != "000000":
            out[code] = q
    return out


def _codes_for_board(
    board: dict[str, Any],
    snaps: dict[str, dict[str, Any]],
) -> list[str]:
    """Resolve constituent codes: snap by bk / name, else live ``pool`` / ``members``."""
    bk = str(board.get("bk") or "").upper()
    name = str(board.get("name") or board.get("sina_name") or "")
    hit = snaps.get(bk)
    if not hit and name:
        for row in snaps.values():
            if str(row.get("name") or "") == name:
                hit = row
                break
    if hit and hit.get("codes"):
        return [str(c).zfill(6) for c in hit["codes"] if c]
    pool = board.get("pool") or board.get("members") or []
    codes: list[str] = []
    for m in pool:
        if not isinstance(m, dict):
            continue
        c = str(m.get("code") or "").zfill(6)
        if c and c != "000000":
            codes.append(c)
    return codes


def breadth_from_codes(
    codes: list[str],
    quotes_by_code: dict[str, dict[str, Any]],
) -> tuple[int, int, int]:
    """Return ``(ups, downs, known)`` from quote pct (flat ignored)."""
    ups = downs = known = 0
    for code in codes:
        q = quotes_by_code.get(str(code).zfill(6))
        if not q:
            continue
        pct = q.get("pct")
        if pct is None:
            continue
        try:
            p = float(pct)
        except (TypeError, ValueError):
            continue
        known += 1
        if p > 0:
            ups += 1
        elif p < 0:
            downs += 1
    return ups, downs, known


def breadth_from_members(members: list[dict[str, Any]] | None) -> tuple[int, int, int]:
    """Return ``(ups, downs, known)`` from member row pct fields."""
    ups = downs = known = 0
    for m in members or []:
        if not isinstance(m, dict):
            continue
        pct = m.get("pct")
        if pct is None:
            continue
        try:
            p = float(pct)
        except (TypeError, ValueError):
            continue
        known += 1
        if p > 0:
            ups += 1
        elif p < 0:
            downs += 1
    return ups, downs, known


def load_member_snaps_merged() -> dict[str, dict[str, Any]]:
    """Load EM then Sina snaps keyed by uppercase bk (EM wins on collision)."""
    from market_desk.db import load_board_members_snap

    out: dict[str, dict[str, Any]] = {}
    for source in ("sina", "em"):
        try:
            snaps = load_board_members_snap(source, with_codes=True)
        except Exception:
            log.debug("load board_members_snap(%s) failed", source, exc_info=True)
            continue
        for key, row in (snaps or {}).items():
            bk = str(key or row.get("key") or "").upper()
            if not bk:
                continue
            codes = list(row.get("codes") or [])
            if not codes:
                continue
            out[bk] = {
                "key": bk,
                "name": row.get("name"),
                "kind": row.get("kind"),
                "codes": codes,
                "source": source,
            }
    return out


def apply_board_breadth_from_quotes(
    boards: list[dict[str, Any]] | None,
    quotes: list[dict[str, Any]] | dict[str, Any] | None,
    *,
    snaps: dict[str, dict[str, Any]] | None = None,
    min_known: int = 3,
) -> int:
    """Fill zero ``up_count``/``down_count`` on board rows. Return how many were filled."""
    if not boards:
        return 0
    qmap = _quote_map(quotes)
    if not qmap:
        return 0
    snap_map = snaps if snaps is not None else load_member_snaps_merged()
    filled = 0
    for board in boards:
        if not isinstance(board, dict):
            continue
        if int(board.get("up_count") or 0) + int(board.get("down_count") or 0) > 0:
            continue
        codes = _codes_for_board(board, snap_map)
        ups, downs, known = breadth_from_codes(codes, qmap)
        if known < min_known:
            continue
        board["up_count"] = ups
        board["down_count"] = downs
        board["breadth_from"] = "snap_quotes"
        filled += 1
    return filled


def fill_card_breadth_from_members(card: dict[str, Any]) -> bool:
    """When a card still has zero breadth, derive it from enriched member pcts."""
    if int(card.get("up_count") or 0) + int(card.get("down_count") or 0) > 0:
        return False
    members = card.get("pool") or card.get("members") or []
    ups, downs, known = breadth_from_members(members if isinstance(members, list) else [])
    if known < 3:
        return False
    card["up_count"] = ups
    card["down_count"] = downs
    card["breadth_from"] = "members"
    return True
