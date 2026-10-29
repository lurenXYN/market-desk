"""SQLite persistence for execution OrderBook + circuit breaker state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


def ensure_exec_tables(conn: sqlite3.Connection) -> None:
    """Create exec_order / exec_circuit tables (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exec_order (
            orderid TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            status TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exec_order_updated ON exec_order(updated_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exec_circuit (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            trade_date TEXT NOT NULL,
            day_start_equity REAL,
            reject_streak INTEGER NOT NULL DEFAULT 0,
            day_loss_tripped INTEGER NOT NULL DEFAULT 0,
            reject_tripped INTEGER NOT NULL DEFAULT 0,
            last_reject_reason TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def save_orders(rows: list[dict[str, Any]]) -> int:
    """Replace persisted orders with the given serialized OrderData dicts."""
    now = _stamp()
    with _connect() as conn:
        ensure_exec_tables(conn)
        conn.execute("DELETE FROM exec_order")
        for row in rows:
            oid = str(row.get("orderid") or "")
            if not oid:
                continue
            conn.execute(
                """
                INSERT INTO exec_order(orderid, payload, status, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    oid,
                    json.dumps(row, ensure_ascii=False),
                    str(row.get("status") or ""),
                    now,
                ),
            )
        conn.commit()
    return len(rows)


def load_orders(*, limit: int = 80) -> list[dict[str, Any]]:
    """Load persisted orders, newest ``updated_at`` first."""
    lim = max(1, min(int(limit or 80), 200))
    with _connect() as conn:
        ensure_exec_tables(conn)
        cur = conn.execute(
            """
            SELECT payload FROM exec_order
            ORDER BY updated_at DESC, orderid DESC
            LIMIT ?
            """,
            (lim,),
        )
        out: list[dict[str, Any]] = []
        for (payload,) in cur.fetchall():
            try:
                row = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                out.append(row)
        return out


def load_circuit() -> dict[str, Any] | None:
    """Return the singleton circuit row or None."""
    with _connect() as conn:
        ensure_exec_tables(conn)
        cur = conn.execute(
            """
            SELECT trade_date, day_start_equity, reject_streak,
                   day_loss_tripped, reject_tripped, last_reject_reason, updated_at
            FROM exec_circuit WHERE id = 1
            """
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "trade_date": str(row[0] or ""),
            "day_start_equity": float(row[1]) if row[1] is not None else None,
            "reject_streak": int(row[2] or 0),
            "day_loss_tripped": bool(row[3]),
            "reject_tripped": bool(row[4]),
            "last_reject_reason": str(row[5] or ""),
            "updated_at": str(row[6] or ""),
        }


def save_circuit(state: dict[str, Any]) -> None:
    """Upsert the singleton circuit breaker state."""
    now = _stamp()
    with _connect() as conn:
        ensure_exec_tables(conn)
        conn.execute(
            """
            INSERT INTO exec_circuit(
                id, trade_date, day_start_equity, reject_streak,
                day_loss_tripped, reject_tripped, last_reject_reason, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                trade_date=excluded.trade_date,
                day_start_equity=excluded.day_start_equity,
                reject_streak=excluded.reject_streak,
                day_loss_tripped=excluded.day_loss_tripped,
                reject_tripped=excluded.reject_tripped,
                last_reject_reason=excluded.last_reject_reason,
                updated_at=excluded.updated_at
            """,
            (
                str(state.get("trade_date") or "")[:10],
                state.get("day_start_equity"),
                int(state.get("reject_streak") or 0),
                1 if state.get("day_loss_tripped") else 0,
                1 if state.get("reject_tripped") else 0,
                str(state.get("last_reject_reason") or "")[:160],
                now,
            ),
        )
        conn.commit()
