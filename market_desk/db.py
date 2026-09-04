"""SQLite persistence for daily snapshots and auction locks."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from market_desk.config import DATA_DIR, DB_PATH


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they do not exist."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_snapshot (
                trade_date TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auction_lock (
                trade_date TEXT PRIMARY KEY,
                median_open REAL,
                high_open_share REAL,
                tone TEXT,
                payload TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS board_daily (
                trade_date TEXT NOT NULL,
                bk TEXT NOT NULL,
                name TEXT,
                zt_n INTEGER,
                dt_n INTEGER,
                pct REAL,
                leader_boards INTEGER,
                status TEXT,
                PRIMARY KEY (trade_date, bk)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                name TEXT,
                buy_price REAL NOT NULL,
                qty INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                signaled_at TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                action TEXT,
                phase TEXT,
                mainline TEXT,
                code TEXT NOT NULL,
                name TEXT,
                kind TEXT,
                price REAL NOT NULL,
                last REAL,
                ready INTEGER,
                payload TEXT,
                outcome_day1_pct REAL,
                outcome_day3_pct REAL,
                outcome_mfe_pct REAL,
                outcome_mae_pct REAL,
                outcome_label TEXT,
                outcome_checked_at TEXT,
                UNIQUE(trade_date, code, signal_type)
            )
            """
        )
        conn.commit()


def save_daily(trade_date: str, payload: dict[str, Any]) -> None:
    """Upsert today's compact daily row used by the history table."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_snapshot(trade_date, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (trade_date, json.dumps(payload, ensure_ascii=False), now),
        )
        conn.commit()


def load_daily(limit: int = 14) -> list[dict[str, Any]]:
    """Return recent daily snapshots, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT trade_date, payload FROM daily_snapshot ORDER BY trade_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = json.loads(row["payload"])
        item["trade_date"] = row["trade_date"]
        out.append(item)
    return out


def save_auction(trade_date: str, payload: dict[str, Any]) -> None:
    """Lock the 09:25 auction summary for a trading day."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO auction_lock(
                trade_date, median_open, high_open_share, tone, payload, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                median_open = excluded.median_open,
                high_open_share = excluded.high_open_share,
                tone = excluded.tone,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                trade_date,
                payload.get("median_open"),
                payload.get("high_open_share"),
                payload.get("tone"),
                json.dumps(payload, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()


def load_auction(trade_date: str) -> dict[str, Any] | None:
    """Return the locked auction summary for the given date."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload FROM auction_lock WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["payload"])


def save_board_daily(trade_date: str, rows: list[dict[str, Any]]) -> None:
    """Upsert today's compact sector rows used for concentration history."""
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO board_daily(
                trade_date, bk, name, zt_n, dt_n, pct, leader_boards, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, bk) DO UPDATE SET
                name = excluded.name,
                zt_n = excluded.zt_n,
                dt_n = excluded.dt_n,
                pct = excluded.pct,
                leader_boards = excluded.leader_boards,
                status = excluded.status
            """,
            [
                (
                    trade_date,
                    r.get("bk") or "",
                    r.get("name") or "",
                    int(r.get("zt_n") or 0),
                    int(r.get("dt_n") or 0),
                    r.get("pct"),
                    int(r.get("leader_boards") or 0),
                    r.get("status") or "",
                )
                for r in rows
                if r.get("bk")
            ],
        )
        conn.commit()


def load_board_hist_map(before_date: str, days: int = 8) -> dict[str, list[dict[str, Any]]]:
    """Return prior-day sector stats keyed by board code, oldest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, bk, name, zt_n, dt_n, pct, leader_boards, status
            FROM board_daily
            WHERE trade_date < ?
            ORDER BY trade_date ASC
            """,
            (before_date,),
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        out.setdefault(item["bk"], []).append(item)
    for bk, series in list(out.items()):
        out[bk] = series[-days:]
    return out


def add_position(code: str, name: str, buy_price: float, qty: int, note: str = "") -> dict[str, Any]:
    """Insert a local position row and return it."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO positions(code, name, buy_price, qty, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (code, name, buy_price, qty, note, now),
        )
        pid = int(cur.lastrowid)
        conn.commit()
    return {
        "id": pid,
        "code": code,
        "name": name,
        "buy_price": buy_price,
        "qty": qty,
        "note": note,
        "created_at": now,
    }


def delete_position(pid: int) -> bool:
    """Delete a position by id. Return True if a row was removed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM positions WHERE id = ?", (pid,))
        conn.commit()
        return cur.rowcount > 0


def load_positions() -> list[dict[str, Any]]:
    """Return all locally recorded positions, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, code, name, buy_price, qty, note, created_at
            FROM positions
            ORDER BY id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_signal(row: dict[str, Any]) -> None:
    """Insert or refresh a same-day signal keyed by date + code + type."""
    now_payload = json.dumps(row.get("payload") or {}, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO signals(
                trade_date, signaled_at, signal_type, action, phase, mainline,
                code, name, kind, price, last, ready, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, code, signal_type) DO UPDATE SET
                signaled_at = excluded.signaled_at,
                action = excluded.action,
                phase = excluded.phase,
                mainline = excluded.mainline,
                name = excluded.name,
                kind = excluded.kind,
                price = excluded.price,
                last = excluded.last,
                ready = excluded.ready,
                payload = excluded.payload
            """,
            (
                row.get("trade_date"),
                row.get("signaled_at"),
                row.get("signal_type"),
                row.get("action"),
                row.get("phase"),
                row.get("mainline"),
                row.get("code"),
                row.get("name"),
                row.get("kind"),
                row.get("price"),
                row.get("last"),
                int(row.get("ready") or 0),
                now_payload,
            ),
        )
        conn.commit()


def load_signals(limit: int = 60) -> list[dict[str, Any]]:
    """Return recent signals, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, trade_date, signaled_at, signal_type, action, phase, mainline,
                   code, name, kind, price, last, ready, payload,
                   outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
                   outcome_label, outcome_checked_at
            FROM signals
            ORDER BY trade_date DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.get("payload")
        if isinstance(raw, str) and raw:
            try:
                item["payload"] = json.loads(raw)
            except json.JSONDecodeError:
                item["payload"] = {}
        else:
            item["payload"] = {}
        out.append(item)
    return out


def load_unscored_signals(before_date: str, limit: int = 80) -> list[dict[str, Any]]:
    """Return signals before a date that still lack an outcome label."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, trade_date, signaled_at, signal_type, action, phase, mainline,
                   code, name, kind, price, last, ready, payload,
                   outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
                   outcome_label, outcome_checked_at
            FROM signals
            WHERE trade_date < ? AND (outcome_label IS NULL OR outcome_label = '')
            ORDER BY trade_date ASC, id ASC
            LIMIT ?
            """,
            (before_date, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.get("payload")
        if isinstance(raw, str) and raw:
            try:
                item["payload"] = json.loads(raw)
            except json.JSONDecodeError:
                item["payload"] = {}
        else:
            item["payload"] = {}
        out.append(item)
    return out


def mark_signal_outcome(signal_id: int, outcome: dict[str, Any]) -> bool:
    """Persist scored outcome fields for one signal row."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE signals SET
                outcome_day1_pct = ?,
                outcome_day3_pct = ?,
                outcome_mfe_pct = ?,
                outcome_mae_pct = ?,
                outcome_label = ?,
                outcome_checked_at = ?
            WHERE id = ?
            """,
            (
                outcome.get("outcome_day1_pct"),
                outcome.get("outcome_day3_pct"),
                outcome.get("outcome_mfe_pct"),
                outcome.get("outcome_mae_pct"),
                outcome.get("outcome_label"),
                outcome.get("outcome_checked_at"),
                signal_id,
            ),
        )
        conn.commit()
        return cur.rowcount > 0
