"""Frozen appearance index: which board a code sat under when it was seen.

One row = ``(trade_date, code, source, board_name)`` where ``source`` is
``signal`` (shared buy), ``ma_fan`` or ``pot``. Board names come only from the
frozen signal payload / nightly scan side-notes at write time — never from
today's constituent snapshot — so the history cannot be re-labelled.

``current_boards_for_code`` is a separate read-only view over
``board_members_snap`` and must never feed rankings.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect
from market_desk.db.schema import _ensure_code_board_seen_tables

log = logging.getLogger("market_desk.code_board_seen")

SOURCES = ("signal", "ma_fan", "pot")


def _now() -> str:
    """Return a local timestamp string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def board_key(name: str | None) -> str:
    """Normalize a board name for matching (strip 行业/概念/板块/Ⅱ/Ⅲ suffix)."""
    from market_desk.board_alias import normalize_board_name

    return normalize_board_name(name)


def boards_from_signal(row: dict[str, Any]) -> list[str]:
    """Return frozen board names for one signal row (board_names, source_board, mainline)."""
    p = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    raw = p.get("board_names") or row.get("boards") or []
    out: list[str] = []
    if isinstance(raw, list):
        out = [str(x).strip() for x in raw if str(x).strip()]
    sb = str(p.get("source_board") or row.get("source_board") or "").strip()
    ml = str(row.get("mainline") or p.get("mainline") or "").strip()
    for name in (sb, ml):
        if name and name not in out:
            out.append(name)
    return out


def boards_from_scan_item(item: dict[str, Any]) -> list[str]:
    """Return scan side-note boards for one MA-fan / pot item."""
    raw = item.get("boards") or []
    out: list[str] = []
    if isinstance(raw, list):
        out = [str(x).strip() for x in raw if str(x).strip()]
    ind = str(item.get("industry") or "").strip()
    if ind and ind not in out:
        out.append(ind)
    return out


def _num(value: Any) -> float | None:
    """Parse a float or return None."""
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _insert_rows(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    code: str,
    source: str,
    name: str,
    boards: list[str],
    industry: str = "",
    price: float | None = None,
    pct: float | None = None,
    d3: float | None = None,
    tag: str = "",
    ref_id: int | None = None,
) -> None:
    """Insert one appearance, one row per board (or one placeholder row when none)."""
    names = [b for b in boards if b] or [""]
    at = _now()
    for b in names:
        kind = "industry" if (b and industry and b == industry) else ""
        conn.execute(
            """
            INSERT INTO code_board_seen(
                trade_date, code, source, board_name, board_key, board_kind,
                name, price, pct, d3, tag, ref_id, first_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, code, source, board_name) DO UPDATE SET
                name = excluded.name,
                price = COALESCE(code_board_seen.price, excluded.price),
                pct = COALESCE(excluded.pct, code_board_seen.pct),
                d3 = COALESCE(excluded.d3, code_board_seen.d3),
                tag = CASE WHEN excluded.tag != '' THEN excluded.tag ELSE code_board_seen.tag END,
                ref_id = COALESCE(excluded.ref_id, code_board_seen.ref_id)
            """,
            (
                trade_date, code, source, b, board_key(b) if b else "", kind,
                name, price, pct, d3, tag, ref_id, at,
            ),
        )
    if any(names):
        # A real board arrived: drop the empty placeholder for the same appearance.
        conn.execute(
            """
            DELETE FROM code_board_seen
            WHERE trade_date = ? AND code = ? AND source = ? AND board_name = ''
            """,
            (trade_date, code, source),
        )


def sync_signal_seen(
    conn: sqlite3.Connection,
    *,
    row: dict[str, Any],
    merged_payload: dict[str, Any],
    owner_id: int,
) -> None:
    """Index one shared buy signal after ``upsert_signal`` (never raises)."""
    try:
        sig = str(row.get("signal_type") or "").strip().lower()
        if owner_id != 0 or not sig.startswith("buy") or sig == "buy_sell":
            return
        code = str(row.get("code") or "").strip()
        day = str(row.get("trade_date") or "")[:10]
        if len(code) != 6 or len(day) != 10:
            return
        item = dict(row)
        item["payload"] = merged_payload
        try:
            from market_desk.review.signals import _dragon_hide_from_review

            if _dragon_hide_from_review(item):
                return
        except Exception:
            pass
        _ensure_code_board_seen_tables(conn)
        got = conn.execute(
            """
            SELECT id, price FROM signals
            WHERE trade_date = ? AND code = ? AND signal_type = ? AND owner_user_id = 0
            """,
            (day, code, row.get("signal_type")),
        ).fetchone()
        _insert_rows(
            conn,
            trade_date=day,
            code=code,
            source="signal",
            name=str(row.get("name") or ""),
            boards=boards_from_signal(item),
            price=_num(got["price"]) if got else _num(row.get("price")),
            tag=sig,
            ref_id=int(got["id"]) if got else None,
        )
    except Exception:
        log.exception("sync_signal_seen failed")


def sync_ma_fan_seen(conn: sqlite3.Connection, trade_date: str, payload: dict[str, Any]) -> None:
    """Replace the MA-fan / pot appearances of one scan day (never raises)."""
    try:
        day = str(trade_date or "")[:10]
        if len(day) != 10:
            return
        _ensure_code_board_seen_tables(conn)
        conn.execute(
            "DELETE FROM code_board_seen WHERE trade_date = ? AND source IN ('ma_fan', 'pot')",
            (day,),
        )
        for source, key in (("ma_fan", "items"), ("pot", "pot")):
            for it in (payload or {}).get(key) or []:
                if not isinstance(it, dict):
                    continue
                code = str(it.get("code") or "").strip()
                if len(code) != 6:
                    continue
                fwd = it.get("fwd") if isinstance(it.get("fwd"), dict) else {}
                tag = str(it.get("stage") or it.get("theme_tag") or "")
                if source == "pot" and it.get("focus"):
                    tag = (tag + "·浅跌") if tag else "浅跌"
                _insert_rows(
                    conn,
                    trade_date=day,
                    code=code,
                    source=source,
                    name=str(it.get("name") or ""),
                    boards=boards_from_scan_item(it),
                    industry=str(it.get("industry") or "").strip(),
                    price=_num(it.get("close") if it.get("close") is not None else it.get("last")),
                    pct=_num(it.get("pct")),
                    d3=_num(fwd.get("d3")),
                    tag=tag,
                )
    except Exception:
        log.exception("sync_ma_fan_seen failed")


def backfill_code_board_seen(*, force: bool = False) -> dict[str, int]:
    """Fill the index from existing ``signals`` and ``ma_fan_day`` rows once.

    Skipped when the table already has rows unless ``force``.
    """
    from market_desk.db.signals import _SIGNAL_SELECT, _decode_signal_rows

    out = {"signals": 0, "ma_fan_days": 0}
    with _connect() as conn:
        _ensure_code_board_seen_tables(conn)
        have = conn.execute("SELECT COUNT(*) FROM code_board_seen").fetchone()[0]
        if have and not force:
            return out
        if force:
            conn.execute("DELETE FROM code_board_seen")
        rows = conn.execute(
            _SIGNAL_SELECT
            + """
            WHERE signal_type LIKE 'buy%' AND COALESCE(owner_user_id, 0) = 0
            ORDER BY trade_date, id
            """
        ).fetchall()
        for item in _decode_signal_rows(rows):
            before = conn.total_changes
            sync_signal_seen(
                conn,
                row={
                    "trade_date": item.get("trade_date"),
                    "code": item.get("code"),
                    "signal_type": item.get("signal_type"),
                    "name": item.get("name"),
                    "price": item.get("price"),
                    "mainline": item.get("mainline"),
                    "kind": item.get("kind"),
                },
                merged_payload=item.get("payload") or {},
                owner_id=0,
            )
            if conn.total_changes > before:
                out["signals"] += 1
        try:
            day_rows = conn.execute("SELECT trade_date, payload FROM ma_fan_day").fetchall()
        except sqlite3.Error:
            day_rows = []
        for dr in day_rows:
            try:
                body = json.loads(dr["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(body, dict):
                sync_ma_fan_seen(conn, str(dr["trade_date"]), body)
                out["ma_fan_days"] += 1
        conn.commit()
    return out


_SEEN_SELECT = """
    SELECT t.trade_date, t.code, t.source, t.board_name, t.board_kind, t.name,
           t.price, t.pct, t.d3, t.tag, t.ref_id,
           s.signal_type AS signal_type, s.outcome_label AS outcome_label,
           s.outcome_day3_pct AS outcome_day3_pct, s.mainline AS mainline
    FROM code_board_seen t
    LEFT JOIN signals s ON s.id = t.ref_id
"""


def _fold_seen_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Collapse board rows that share ``(trade_date, code, source)``."""
    folded: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for r in rows:
        item = dict(r)
        key = (str(item["trade_date"]), str(item["code"]), str(item["source"]))
        cur = folded.get(key)
        if cur is None:
            item["boards"] = [item["board_name"]] if item["board_name"] else []
            item["boards_text"] = item["board_name"] or ""
            folded[key] = item
            order.append(key)
            continue
        if item["board_name"] and item["board_name"] not in cur["boards"]:
            cur["boards"].append(item["board_name"])
            cur["boards_text"] = " / ".join(cur["boards"])
    return [folded[k] for k in order]


def load_seen_by_code(code: str, *, limit: int = 400) -> list[dict[str, Any]]:
    """Return every indexed appearance of one code, newest first (boards folded per appearance)."""
    c = str(code or "").strip()
    if len(c) != 6:
        return []
    with _connect() as conn:
        _ensure_code_board_seen_tables(conn)
        rows = conn.execute(
            _SEEN_SELECT
            + """
            WHERE t.code = ?
            ORDER BY t.trade_date DESC, t.source, t.board_name
            LIMIT ?
            """,
            (c, max(1, min(int(limit), 1000))),
        ).fetchall()
    return _fold_seen_rows(rows)


def load_seen_by_board(
    query: str,
    *,
    days: int = 20,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return appearances under a board keyword within the latest ``days`` indexed dates."""
    q = str(query or "").strip()
    key = board_key(q).lower()
    if not q or not key:
        return []
    with _connect() as conn:
        _ensure_code_board_seen_tables(conn)
        day_rows = conn.execute(
            "SELECT DISTINCT trade_date FROM code_board_seen ORDER BY trade_date DESC LIMIT ?",
            (max(1, min(int(days), 60)),),
        ).fetchall()
        if not day_rows:
            return []
        since = str(day_rows[-1]["trade_date"])
        rows = conn.execute(
            _SEEN_SELECT
            + """
            WHERE t.board_key != '' AND t.trade_date >= ?
              AND (LOWER(t.board_key) LIKE '%' || ? || '%' OR ? LIKE '%' || LOWER(t.board_key) || '%')
            ORDER BY t.trade_date DESC, t.code, t.source
            LIMIT ?
            """,
            (since, key, key, max(1, min(int(limit), 400))),
        ).fetchall()
    return _fold_seen_rows(rows)


# Inverted constituent cache: code -> [{name, kind, at}]. Rebuilt when the
# snap table fingerprint (row count + max at) changes; avoids LIKE full scans.
_CURRENT_BOARD_CACHE: dict[str, list[dict[str, str]]] = {}
_CURRENT_BOARD_FP: tuple[int, str] | None = None


def _refresh_current_board_cache(conn: sqlite3.Connection) -> None:
    """Rebuild the code → boards inverted index from ``board_members_snap``."""
    global _CURRENT_BOARD_CACHE, _CURRENT_BOARD_FP
    fp_row = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(at), '') FROM board_members_snap"
    ).fetchone()
    fp = (int(fp_row[0] or 0), str(fp_row[1] or ""))
    if _CURRENT_BOARD_FP == fp and _CURRENT_BOARD_CACHE is not None:
        return
    by_code: dict[str, list[dict[str, str]]] = {}
    seen_pair: dict[str, set[str]] = {}
    rows = conn.execute(
        """
        SELECT source, name, kind, at, codes FROM board_members_snap
        WHERE COALESCE(name, '') != ''
        ORDER BY CASE source WHEN 'em' THEN 0 ELSE 1 END, n
        """
    ).fetchall()
    for r in rows:
        name = str(r["name"] or "")
        kind = str(r["kind"] or "")
        at = str(r["at"] or "")
        key = board_key(name)
        if not name or not key:
            continue
        try:
            codes = json.loads(r["codes"] or "[]")
        except (TypeError, ValueError):
            codes = []
        item = {"name": name, "kind": kind, "at": at}
        for raw in codes:
            c = str(raw or "").strip()
            if len(c) != 6:
                continue
            used = seen_pair.setdefault(c, set())
            if key in used:
                continue
            used.add(key)
            by_code.setdefault(c, []).append(item)
    _CURRENT_BOARD_CACHE = by_code
    _CURRENT_BOARD_FP = fp


def current_boards_for_code(code: str) -> list[dict[str, str]]:
    """Return today's constituent boards for a code (read-only; not history truth)."""
    c = str(code or "").strip()
    if len(c) != 6:
        return []
    try:
        with _connect() as conn:
            _refresh_current_board_cache(conn)
    except sqlite3.Error:
        return []
    return list(_CURRENT_BOARD_CACHE.get(c) or [])
