"""Watchlist, favorite boards, gap-fade strikes and the stock blacklist."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


def add_watchlist(
    code: str,
    name: str = "",
    *,
    note: str = "",
    suggest_price: float | None = None,
    stop_price: float | None = None,
    chase_price: float | None = None,
    user_id: int,
) -> dict[str, Any]:
    """Insert or refresh a personal watchlist row; first_seen/suggest stay locked."""
    uid = int(user_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    code = str(code or "").zfill(6)
    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT id, first_seen_at, suggest_price, stop_price, chase_price
            FROM watchlist WHERE code = ? AND user_id = ?
            """,
            (code, uid),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE watchlist
                SET name = COALESCE(NULLIF(?, ''), name),
                    note = ?,
                    stop_price = COALESCE(?, stop_price),
                    chase_price = COALESCE(?, chase_price)
                WHERE code = ? AND user_id = ?
                """,
                (name, note, stop_price, chase_price, code, uid),
            )
            if existing["suggest_price"] is None and suggest_price is not None:
                conn.execute(
                    "UPDATE watchlist SET suggest_price = ? WHERE code = ? AND user_id = ?",
                    (float(suggest_price), code, uid),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM watchlist WHERE code = ? AND user_id = ?",
                (code, uid),
            ).fetchone()
            return dict(row)
        conn.execute(
            """
            INSERT INTO watchlist(
                user_id, code, name, note, suggest_price, stop_price, chase_price,
                first_seen_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uid, code, name, note, suggest_price, stop_price, chase_price, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM watchlist WHERE code = ? AND user_id = ?",
            (code, uid),
        ).fetchone()
        return dict(row)


def load_watchlist(*, user_id: int | None = None) -> list[dict[str, Any]]:
    """Return personal watchlist rows for one user (empty if no user)."""
    if user_id is None:
        return []
    uid = int(user_id)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, code, name, note, suggest_price, stop_price, chase_price,
                   first_seen_at, created_at
            FROM watchlist
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (uid,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_watchlist(item_id: int, *, user_id: int | None = None) -> bool:
    """Delete one watchlist row by id."""
    with _connect() as conn:
        if user_id is not None:
            cur = conn.execute(
                "DELETE FROM watchlist WHERE id = ? AND user_id = ?",
                (int(item_id), int(user_id)),
            )
        else:
            cur = conn.execute("DELETE FROM watchlist WHERE id = ?", (int(item_id),))
        conn.commit()
        return cur.rowcount > 0


def add_favorite_board(
    bk: str,
    name: str = "",
    *,
    kind: str = "",
    note: str = "",
    user_id: int,
) -> dict[str, Any]:
    """Insert or refresh a personally favored sector board."""
    uid = int(user_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bk = str(bk or "").strip().upper()
    if not bk.startswith("BK"):
        raise ValueError("bk must look like BKXXXX")
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM favorite_boards WHERE bk = ? AND user_id = ?",
            (bk, uid),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE favorite_boards
                SET name = COALESCE(NULLIF(?, ''), name),
                    kind = COALESCE(NULLIF(?, ''), kind),
                    note = ?
                WHERE bk = ? AND user_id = ?
                """,
                (name, kind, note, bk, uid),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM favorite_boards WHERE bk = ? AND user_id = ?",
                (bk, uid),
            ).fetchone()
            return dict(row)
        conn.execute(
            """
            INSERT INTO favorite_boards(user_id, bk, name, kind, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uid, bk, name, kind, note, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM favorite_boards WHERE bk = ? AND user_id = ?",
            (bk, uid),
        ).fetchone()
        return dict(row)


def load_favorite_boards(*, user_id: int | None = None) -> list[dict[str, Any]]:
    """Return favored boards for one user (empty if no user)."""
    if user_id is None:
        return []
    uid = int(user_id)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, bk, name, kind, note, created_at
            FROM favorite_boards
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (uid,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_favorite_board(item_id: int, *, user_id: int | None = None) -> bool:
    """Delete one favored board by id."""
    with _connect() as conn:
        if user_id is not None:
            cur = conn.execute(
                "DELETE FROM favorite_boards WHERE id = ? AND user_id = ?",
                (int(item_id), int(user_id)),
            )
        else:
            cur = conn.execute(
                "DELETE FROM favorite_boards WHERE id = ?", (int(item_id),)
            )
        conn.commit()
        return cur.rowcount > 0


def delete_favorite_board_by_bk(bk: str, *, user_id: int | None = None) -> bool:
    """Delete one favored board by East Money board code."""
    key = str(bk or "").strip().upper()
    if not key:
        return False
    with _connect() as conn:
        if user_id is not None:
            cur = conn.execute(
                "DELETE FROM favorite_boards WHERE bk = ? AND user_id = ?",
                (key, int(user_id)),
            )
        else:
            cur = conn.execute("DELETE FROM favorite_boards WHERE bk = ?", (key,))
        conn.commit()
        return cur.rowcount > 0


def upsert_gap_fade_strike(
    code: str,
    trade_date: str,
    *,
    name: str = "",
    open_gap_pct: float | None = None,
    from_open_pct: float | None = None,
    flagged: bool = True,
) -> None:
    """Upsert one code/day gap-fade observation."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    code = str(code or "").zfill(6)
    day = str(trade_date or "")[:10]
    if not code or not day:
        return
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO gap_fade_strikes(
                code, trade_date, name, open_gap_pct, from_open_pct, flagged, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code, trade_date) DO UPDATE SET
                name = COALESCE(NULLIF(excluded.name, ''), gap_fade_strikes.name),
                open_gap_pct = excluded.open_gap_pct,
                from_open_pct = excluded.from_open_pct,
                flagged = excluded.flagged,
                updated_at = excluded.updated_at
            """,
            (
                code,
                day,
                name,
                open_gap_pct,
                from_open_pct,
                1 if flagged else 0,
                now,
            ),
        )
        conn.commit()


def count_gap_fade_strikes(code: str, *, since_date: str) -> int:
    """Count flagged gap-fade days for a code since a date (inclusive)."""
    code = str(code or "").zfill(6)
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM gap_fade_strikes
            WHERE code = ? AND trade_date >= ? AND flagged = 1
            """,
            (code, str(since_date)[:10]),
        ).fetchone()
    return int(row["n"] if row else 0)


def load_gap_fade_strike_codes(trade_date: str) -> set[str]:
    """Return codes flagged as gap-fade on a trade date."""
    day = str(trade_date or "")[:10]
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT code FROM gap_fade_strikes
            WHERE trade_date = ? AND flagged = 1
            """,
            (day,),
        ).fetchall()
    return {str(r["code"]).zfill(6) for r in rows}


def add_stock_blacklist(
    code: str,
    name: str = "",
    *,
    reason: str = "",
    source: str = "manual",
    note: str = "",
    strike_n: int = 0,
) -> dict[str, Any]:
    """Insert or refresh an active blacklist row."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    code = str(code or "").zfill(6)
    if len(code) != 6 or not code.isdigit():
        raise ValueError("code must be a 6-digit ticker")
    source = str(source or "manual").strip() or "manual"
    with _connect() as conn:
        existing = conn.execute(
            "SELECT code, blocked_at, clean_streak, strike_n FROM stock_blacklist WHERE code = ?",
            (code,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE stock_blacklist
                SET name = COALESCE(NULLIF(?, ''), name),
                    reason = COALESCE(NULLIF(?, ''), reason),
                    source = ?,
                    note = ?,
                    strike_n = CASE WHEN ? > 0 THEN ? ELSE strike_n END,
                    clean_streak = 0,
                    updated_at = ?
                WHERE code = ?
                """,
                (
                    name,
                    reason,
                    source,
                    note,
                    int(strike_n),
                    int(strike_n),
                    now,
                    code,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO stock_blacklist(
                    code, name, reason, source, strike_n, clean_streak,
                    blocked_at, updated_at, note
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (code, name, reason, source, int(strike_n), now, now, note),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM stock_blacklist WHERE code = ?", (code,)).fetchone()
        return dict(row)


def load_stock_blacklist() -> list[dict[str, Any]]:
    """Return active blacklist rows, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT code, name, reason, source, strike_n, clean_streak,
                   blocked_at, updated_at, note
            FROM stock_blacklist
            ORDER BY updated_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def load_blacklist_codes() -> set[str]:
    """Return the set of blacklisted six-digit codes."""
    return {str(r.get("code") or "").zfill(6) for r in load_stock_blacklist() if r.get("code")}


def delete_stock_blacklist(code: str) -> bool:
    """Remove one code from the blacklist."""
    code = str(code or "").zfill(6)
    if not code:
        return False
    with _connect() as conn:
        cur = conn.execute("DELETE FROM stock_blacklist WHERE code = ?", (code,))
        conn.commit()
        return cur.rowcount > 0


def bump_blacklist_clean_streak(code: str, *, normal: bool) -> dict[str, Any] | None:
    """Update clean-day streak for an active blacklist row; return row or None."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    code = str(code or "").zfill(6)
    with _connect() as conn:
        row = conn.execute("SELECT * FROM stock_blacklist WHERE code = ?", (code,)).fetchone()
        if not row:
            return None
        streak = 0 if not normal else int(row["clean_streak"] or 0) + 1
        conn.execute(
            """
            UPDATE stock_blacklist
            SET clean_streak = ?, updated_at = ?
            WHERE code = ?
            """,
            (streak, now, code),
        )
        conn.commit()
        out = conn.execute("SELECT * FROM stock_blacklist WHERE code = ?", (code,)).fetchone()
        return dict(out) if out else None
