"""Persistence for the paper trading book (account / lots / fills / log)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect, _table_cols


def ensure_sim_desk_tables(conn: sqlite3.Connection) -> None:
    """Create / migrate sim account, lot and fill tables (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sim_account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            initial_cash REAL NOT NULL,
            cash REAL NOT NULL,
            updated_at TEXT NOT NULL,
            epoch TEXT
        )
        """
    )
    acols = _table_cols(conn, "sim_account")
    if "epoch" not in acols:
        conn.execute("ALTER TABLE sim_account ADD COLUMN epoch TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sim_lot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            kind TEXT,
            signal_type TEXT NOT NULL,
            buy_signal_id INTEGER,
            open_date TEXT NOT NULL,
            open_at TEXT,
            open_px REAL NOT NULL,
            open_weight REAL NOT NULL DEFAULT 1.0,
            size_tag TEXT,
            qty_open INTEGER NOT NULL DEFAULT 0,
            qty INTEGER NOT NULL DEFAULT 0,
            buy_fee REAL NOT NULL DEFAULT 0,
            remaining REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'open',
            peak_px REAL,
            last_px REAL,
            last_mark_at TEXT,
            closed_at TEXT,
            payload TEXT,
            UNIQUE(open_date, code, signal_type)
        )
        """
    )
    cols = _table_cols(conn, "sim_lot")
    if "qty_open" not in cols:
        conn.execute("ALTER TABLE sim_lot ADD COLUMN qty_open INTEGER NOT NULL DEFAULT 0")
    if "qty" not in cols:
        conn.execute("ALTER TABLE sim_lot ADD COLUMN qty INTEGER NOT NULL DEFAULT 0")
    if "buy_fee" not in cols:
        conn.execute("ALTER TABLE sim_lot ADD COLUMN buy_fee REAL NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_lot_status ON sim_lot(status, code)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sim_fill (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_id INTEGER NOT NULL,
            side TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            filled_at TEXT,
            px REAL NOT NULL,
            qty INTEGER NOT NULL DEFAULT 0,
            notional REAL NOT NULL DEFAULT 0,
            fee REAL NOT NULL DEFAULT 0,
            frac REAL NOT NULL,
            weight REAL NOT NULL,
            pnl_pct REAL,
            pnl_cny REAL,
            cash_after REAL,
            exit_mode TEXT,
            role_label TEXT,
            note TEXT,
            UNIQUE(lot_id, trade_date, side, exit_mode)
        )
        """
    )
    fcols = _table_cols(conn, "sim_fill")
    for col, decl in (
        ("qty", "INTEGER NOT NULL DEFAULT 0"),
        ("notional", "REAL NOT NULL DEFAULT 0"),
        ("fee", "REAL NOT NULL DEFAULT 0"),
        ("pnl_cny", "REAL"),
        ("cash_after", "REAL"),
        ("note", "TEXT"),
    ):
        if col not in fcols:
            conn.execute(f"ALTER TABLE sim_fill ADD COLUMN {col} {decl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_fill_lot ON sim_fill(lot_id, trade_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_fill_time ON sim_fill(filled_at DESC, id DESC)"
    )


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def wipe_sim_book(*, initial_cash: float, epoch: str | None = None) -> None:
    """Delete all lots/fills and reset cash to ``initial_cash``."""
    init = float(initial_cash)
    stamp = _now_stamp()
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        conn.execute("DELETE FROM sim_fill")
        conn.execute("DELETE FROM sim_lot")
        row = conn.execute("SELECT 1 FROM sim_account WHERE id = 1").fetchone()
        if row:
            conn.execute(
                """
                UPDATE sim_account
                SET initial_cash = ?, cash = ?, updated_at = ?, epoch = COALESCE(?, epoch)
                WHERE id = 1
                """,
                (init, init, stamp, epoch),
            )
        else:
            conn.execute(
                """
                INSERT INTO sim_account(id, initial_cash, cash, updated_at, epoch)
                VALUES (1, ?, ?, ?, ?)
                """,
                (init, init, stamp, epoch),
            )
        conn.commit()


def get_sim_account_epoch() -> str:
    """Return the book epoch stamped on the singleton account."""
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        row = conn.execute("SELECT epoch FROM sim_account WHERE id = 1").fetchone()
    return str((row["epoch"] if row else "") or "")[:10]


def set_sim_account_epoch(epoch: str) -> None:
    """Stamp the book epoch without wiping cash."""
    stamp = _now_stamp()
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        row = conn.execute("SELECT 1 FROM sim_account WHERE id = 1").fetchone()
        if row:
            conn.execute(
                "UPDATE sim_account SET epoch = ?, updated_at = ? WHERE id = 1",
                (str(epoch)[:10], stamp),
            )
        conn.commit()


def ensure_sim_account(initial_cash: float) -> dict[str, Any]:
    """Return the singleton paper account, creating it when missing."""
    init = float(initial_cash)
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        row = conn.execute("SELECT * FROM sim_account WHERE id = 1").fetchone()
        if not row:
            stamp = _now_stamp()
            conn.execute(
                """
                INSERT INTO sim_account(id, initial_cash, cash, updated_at, epoch)
                VALUES (1, ?, ?, ?, NULL)
                """,
                (init, init, stamp),
            )
            conn.commit()
            return {
                "initial_cash": init,
                "cash": init,
                "updated_at": stamp,
                "epoch": None,
            }
        return {
            "initial_cash": float(row["initial_cash"]),
            "cash": float(row["cash"]),
            "updated_at": str(row["updated_at"] or ""),
            "epoch": str(row["epoch"] or "")[:10] or None,
        }


def get_sim_account(initial_cash: float) -> dict[str, Any]:
    """Load paper account (auto-init)."""
    return ensure_sim_account(initial_cash)


def set_sim_cash(cash: float, *, initial_cash: float | None = None) -> dict[str, Any]:
    """Overwrite cash (and optional initial) on the singleton account."""
    stamp = _now_stamp()
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        row = conn.execute("SELECT * FROM sim_account WHERE id = 1").fetchone()
        if not row:
            init = float(initial_cash if initial_cash is not None else cash)
            conn.execute(
                """
                INSERT INTO sim_account(id, initial_cash, cash, updated_at)
                VALUES (1, ?, ?, ?)
                """,
                (init, float(cash), stamp),
            )
        else:
            if initial_cash is not None:
                conn.execute(
                    """
                    UPDATE sim_account
                    SET cash = ?, initial_cash = ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (float(cash), float(initial_cash), stamp),
                )
            else:
                conn.execute(
                    "UPDATE sim_account SET cash = ?, updated_at = ? WHERE id = 1",
                    (float(cash), stamp),
                )
        conn.commit()
    return get_sim_account(float(initial_cash if initial_cash is not None else cash))


def _row_to_lot(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Normalize a sim_lot row for callers."""
    item = dict(row)
    raw = item.pop("payload", None)
    try:
        item["payload"] = json.loads(raw) if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        item["payload"] = {}
    for key in ("open_px", "open_weight", "remaining", "peak_px", "last_px", "buy_fee"):
        if item.get(key) is not None:
            try:
                item[key] = float(item[key])
            except (TypeError, ValueError):
                pass
    for key in ("qty", "qty_open"):
        try:
            item[key] = int(item.get(key) or 0)
        except (TypeError, ValueError):
            item[key] = 0
    return item


def _row_to_fill(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Normalize a sim_fill / trade-log row."""
    item = dict(row)
    for key in ("px", "frac", "weight", "pnl_pct", "pnl_cny", "notional", "fee", "cash_after"):
        if item.get(key) is not None:
            try:
                item[key] = float(item[key])
            except (TypeError, ValueError):
                pass
    try:
        item["qty"] = int(item.get("qty") or 0)
    except (TypeError, ValueError):
        item["qty"] = 0
    return item


def list_open_sim_codes() -> list[str]:
    """Return distinct codes with open sim lots (for quote fetch)."""
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        rows = conn.execute(
            """
            SELECT DISTINCT code FROM sim_lot
            WHERE status = 'open' AND (qty > 0 OR remaining > 0.001)
            """
        ).fetchall()
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        code = str(row["code"] or "").strip().zfill(6)
        if len(code) != 6 or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def insert_sim_lot(row: dict[str, Any]) -> int | None:
    """Insert one open lot; return id, or None when the unique key already exists."""
    payload = row.get("payload")
    if isinstance(payload, dict):
        payload_s = json.dumps(payload, ensure_ascii=False)
    else:
        payload_s = str(payload or "") or None
    qty = int(row.get("qty") or row.get("qty_open") or 0)
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        try:
            cur = conn.execute(
                """
                INSERT INTO sim_lot(
                    code, name, kind, signal_type, buy_signal_id,
                    open_date, open_at, open_px, open_weight, size_tag,
                    qty_open, qty, buy_fee, remaining, status,
                    peak_px, last_px, last_mark_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 'open', ?, ?, ?, ?)
                """,
                (
                    str(row.get("code") or "").zfill(6),
                    row.get("name") or "",
                    row.get("kind") or "stock",
                    str(row.get("signal_type") or "buy"),
                    row.get("buy_signal_id"),
                    str(row.get("open_date") or "")[:10],
                    row.get("open_at"),
                    float(row["open_px"]),
                    float(row.get("open_weight") or 1.0),
                    row.get("size_tag") or "full",
                    qty,
                    qty,
                    float(row.get("buy_fee") or 0),
                    row.get("peak_px") or float(row["open_px"]),
                    row.get("last_px") or float(row["open_px"]),
                    row.get("open_at"),
                    payload_s,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None


def update_sim_lot_marks(
    lot_id: int,
    *,
    last_px: float | None = None,
    peak_px: float | None = None,
    last_mark_at: str | None = None,
) -> None:
    """Update live marks on an open lot."""
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        cols: list[str] = []
        args: list[Any] = []
        if last_px is not None:
            cols.append("last_px = ?")
            args.append(float(last_px))
        if peak_px is not None:
            cols.append("peak_px = ?")
            args.append(float(peak_px))
        if last_mark_at is not None:
            cols.append("last_mark_at = ?")
            args.append(last_mark_at)
        if not cols:
            return
        args.append(int(lot_id))
        conn.execute(f"UPDATE sim_lot SET {', '.join(cols)} WHERE id = ?", args)
        conn.commit()


def apply_sim_buy_fill(
    *,
    lot_id: int,
    trade_date: str,
    filled_at: str | None,
    px: float,
    qty: int,
    fee: float,
    cash_after: float,
    weight: float,
    note: str | None = None,
) -> bool:
    """Append the opening buy row to the trade log."""
    day = str(trade_date or "")[:10]
    if not day or int(qty) <= 0:
        return False
    notional = float(px) * int(qty)
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        try:
            conn.execute(
                """
                INSERT INTO sim_fill(
                    lot_id, side, trade_date, filled_at, px, qty, notional, fee,
                    frac, weight, pnl_pct, pnl_cny, cash_after, exit_mode, role_label, note
                ) VALUES (?, 'buy', ?, ?, ?, ?, ?, ?, 1.0, ?, NULL, NULL, ?, 'open', '买入', ?)
                """,
                (
                    int(lot_id),
                    day,
                    filled_at,
                    float(px),
                    int(qty),
                    notional,
                    float(fee),
                    float(weight),
                    float(cash_after),
                    note or "",
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def apply_sim_sell_fill(
    lot_id: int,
    *,
    trade_date: str,
    filled_at: str | None,
    px: float,
    qty: int,
    fee: float,
    cash_after: float,
    frac: float,
    weight: float,
    pnl_pct: float,
    pnl_cny: float,
    exit_mode: str,
    role_label: str | None = None,
    note: str | None = None,
) -> bool:
    """Record a sell fill, reduce qty/remaining, update cash bookkeeping on lot meta."""
    day = str(trade_date or "")[:10]
    mode = str(exit_mode or "clear")
    sell_qty = int(qty or 0)
    if sell_qty <= 0 or not day:
        return False
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        lot = conn.execute("SELECT * FROM sim_lot WHERE id = ?", (int(lot_id),)).fetchone()
        if not lot:
            return False
        held = int(lot["qty"] or 0)
        remaining = float(lot["remaining"] or 0)
        if held <= 0 and remaining <= 0.001:
            return False
        use_qty = min(sell_qty, held) if held > 0 else sell_qty
        use_frac = min(max(0.0, float(frac)), remaining if remaining > 0 else 1.0)
        notional = float(px) * use_qty
        try:
            conn.execute(
                """
                INSERT INTO sim_fill(
                    lot_id, side, trade_date, filled_at, px, qty, notional, fee,
                    frac, weight, pnl_pct, pnl_cny, cash_after, exit_mode, role_label, note
                ) VALUES (?, 'sell', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(lot_id),
                    day,
                    filled_at,
                    float(px),
                    use_qty,
                    notional,
                    float(fee),
                    use_frac,
                    float(weight),
                    float(pnl_pct),
                    float(pnl_cny),
                    float(cash_after),
                    mode,
                    role_label or "",
                    note or "",
                ),
            )
        except sqlite3.IntegrityError:
            return False
        new_qty = max(0, held - use_qty)
        new_rem = max(0.0, remaining - use_frac) if held <= 0 else (
            (new_qty / float(lot["qty_open"] or held or 1)) if (lot["qty_open"] or held) else 0.0
        )
        if new_qty <= 0:
            new_qty = 0
            new_rem = 0.0
        status = "closed" if new_qty <= 0 else "open"
        closed_at = filled_at if status == "closed" else None
        try:
            meta = json.loads(lot["payload"] or "{}") if lot["payload"] else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["last_sell_price"] = float(px)
        meta["last_sell_date"] = day
        meta["day_sold_qty"] = int(meta.get("day_sold_qty") or 0) + use_qty
        payload_s = json.dumps(meta, ensure_ascii=False)
        conn.execute(
            """
            UPDATE sim_lot
            SET qty = ?, remaining = ?, status = ?, last_px = ?, last_mark_at = ?,
                closed_at = COALESCE(?, closed_at), payload = ?
            WHERE id = ?
            """,
            (
                new_qty,
                new_rem,
                status,
                float(px),
                filled_at,
                closed_at,
                payload_s,
                int(lot_id),
            ),
        )
        conn.commit()
        return True


def load_open_sim_lots() -> list[dict[str, Any]]:
    """Return all open lots with shares (or legacy remaining) left."""
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        rows = conn.execute(
            """
            SELECT * FROM sim_lot
            WHERE status = 'open' AND (qty > 0 OR remaining > 0.001)
            ORDER BY open_date ASC, id ASC
            """
        ).fetchall()
    return [_row_to_lot(r) for r in rows]


def load_sim_lots(*, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Return lots newest-first, optionally filtered by status."""
    lim = max(1, min(int(limit or 200), 500))
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        if status:
            rows = conn.execute(
                """
                SELECT * FROM sim_lot WHERE status = ?
                ORDER BY open_date DESC, id DESC LIMIT ?
                """,
                (str(status), lim),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sim_lot ORDER BY open_date DESC, id DESC LIMIT ?",
                (lim,),
            ).fetchall()
    return [_row_to_lot(r) for r in rows]


def load_sim_fills(*, limit: int = 200, side: str | None = None) -> list[dict[str, Any]]:
    """Return recent fills newest-first (trade log)."""
    lim = max(1, min(int(limit or 200), 500))
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        if side:
            rows = conn.execute(
                """
                SELECT f.*, l.code AS code, l.name AS name, l.open_px AS open_px,
                       l.open_weight AS open_weight, l.size_tag AS size_tag,
                       l.open_date AS open_date, l.signal_type AS signal_type
                FROM sim_fill f
                JOIN sim_lot l ON l.id = f.lot_id
                WHERE f.side = ?
                ORDER BY COALESCE(f.filled_at, f.trade_date) DESC, f.id DESC
                LIMIT ?
                """,
                (str(side), lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT f.*, l.code AS code, l.name AS name, l.open_px AS open_px,
                       l.open_weight AS open_weight, l.size_tag AS size_tag,
                       l.open_date AS open_date, l.signal_type AS signal_type
                FROM sim_fill f
                JOIN sim_lot l ON l.id = f.lot_id
                ORDER BY COALESCE(f.filled_at, f.trade_date) DESC, f.id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
    return [_row_to_fill(r) for r in rows]


def load_sim_fills_for_lot(lot_id: int) -> list[dict[str, Any]]:
    """Return fills for one lot oldest-first."""
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        rows = conn.execute(
            """
            SELECT * FROM sim_fill WHERE lot_id = ?
            ORDER BY trade_date ASC, id ASC
            """,
            (int(lot_id),),
        ).fetchall()
    return [_row_to_fill(r) for r in rows]


def count_sim_sell_fills() -> int:
    """Return number of sell-side fills (for gate sample size)."""
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sim_fill WHERE side = 'sell'"
        ).fetchone()
    return int(row["n"] if row else 0)


def sum_sim_realized_pnl_cny() -> float:
    """Sum realized CNY PnL on sell fills."""
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_cny), 0) AS s FROM sim_fill WHERE side = 'sell'"
        ).fetchone()
    try:
        return float(row["s"] or 0)
    except (TypeError, ValueError):
        return 0.0


def sim_lot_exists(open_date: str, code: str, signal_type: str) -> bool:
    """Return True when a lot already exists for this card key."""
    with _connect() as conn:
        ensure_sim_desk_tables(conn)
        row = conn.execute(
            """
            SELECT 1 FROM sim_lot
            WHERE open_date = ? AND code = ? AND signal_type = ?
            """,
            (str(open_date)[:10], str(code).zfill(6), str(signal_type)),
        ).fetchone()
    return row is not None
