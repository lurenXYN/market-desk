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
                note TEXT,
                skipped INTEGER DEFAULT 0,
                traded INTEGER DEFAULT 0,
                UNIQUE(trade_date, code, signal_type)
            )
            """
        )
        # Lightweight migrations for older local DBs.
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(signals)").fetchall()
        }
        if "note" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN note TEXT")
        if "skipped" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN skipped INTEGER DEFAULT 0")
        if "traded" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN traded INTEGER DEFAULT 0")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_segment (
                trade_date TEXT NOT NULL,
                segment TEXT NOT NULL,
                label TEXT,
                action TEXT,
                mainline TEXT,
                phase TEXT,
                temperature INTEGER,
                reason TEXT,
                size_hint TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (trade_date, segment)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mainline_switch (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                switched_at TEXT NOT NULL,
                from_name TEXT,
                to_name TEXT,
                action TEXT,
                phase TEXT,
                temperature INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_override (
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                verdict TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (trade_date, code)
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
    """Insert a position, or average into an existing same-code row."""
    code = str(code or "").zfill(6)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, name, buy_price, qty, note FROM positions WHERE code = ? ORDER BY id DESC LIMIT 1",
            (code,),
        ).fetchone()
        if existing:
            old_qty = int(existing["qty"] or 0)
            old_px = float(existing["buy_price"] or 0)
            new_qty = old_qty + int(qty)
            if new_qty <= 0:
                conn.execute("DELETE FROM positions WHERE id = ?", (int(existing["id"]),))
                conn.commit()
                return {"id": int(existing["id"]), "deleted": True, "code": code}
            avg = (old_px * old_qty + float(buy_price) * int(qty)) / float(new_qty)
            merged_note = (existing["note"] or "") or note
            if note and existing["note"] and note not in str(existing["note"]):
                merged_note = f"{existing['note']}；{note}"
            conn.execute(
                """
                UPDATE positions
                SET name = ?, buy_price = ?, qty = ?, note = ?
                WHERE id = ?
                """,
                (
                    name or existing["name"] or code,
                    round(avg, 4),
                    new_qty,
                    merged_note,
                    int(existing["id"]),
                ),
            )
            conn.commit()
            return {
                "id": int(existing["id"]),
                "code": code,
                "name": name or existing["name"] or code,
                "buy_price": round(avg, 4),
                "qty": new_qty,
                "note": merged_note,
                "created_at": now,
                "averaged": True,
            }
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


def trim_position(pid: int, qty: int) -> dict[str, Any] | None:
    """Reduce share count on a position; delete the row when qty reaches zero."""
    sell = int(qty)
    if sell <= 0:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, code, name, buy_price, qty, note, created_at FROM positions WHERE id = ?",
            (pid,),
        ).fetchone()
        if not row:
            return None
        left = int(row["qty"] or 0) - sell
        if left <= 0:
            conn.execute("DELETE FROM positions WHERE id = ?", (pid,))
            conn.commit()
            item = dict(row)
            item["qty"] = 0
            item["deleted"] = True
            return item
        conn.execute("UPDATE positions SET qty = ? WHERE id = ?", (left, pid))
        conn.commit()
        item = dict(row)
        item["qty"] = left
        item["trimmed"] = sell
        return item


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
                   outcome_label, outcome_checked_at, note, skipped, traded
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
        item["skipped"] = int(item.get("skipped") or 0)
        item["traded"] = int(item.get("traded") or 0)
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
                   outcome_label, outcome_checked_at, note, skipped, traded
            FROM signals
            WHERE trade_date < ?
              AND (outcome_label IS NULL OR outcome_label = '')
              AND IFNULL(skipped, 0) = 0
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
        item["skipped"] = int(item.get("skipped") or 0)
        item["traded"] = int(item.get("traded") or 0)
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


def update_signal_meta(
    signal_id: int,
    *,
    skipped: int | None = None,
    traded: int | None = None,
    note: str | None = None,
) -> bool:
    """Update user review flags on a signal row."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, note, skipped, traded FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        if not row:
            return False
        new_skipped = int(row["skipped"] or 0) if skipped is None else int(skipped)
        new_traded = int(row["traded"] or 0) if traded is None else int(traded)
        # Keep traded / skipped mutually exclusive when either is set explicitly.
        if traded is not None and int(traded):
            new_skipped = 0
            new_traded = 1
        if skipped is not None and int(skipped):
            new_traded = 0
            new_skipped = 1
        new_note = row["note"] if note is None else note
        cur = conn.execute(
            "UPDATE signals SET skipped = ?, traded = ?, note = ? WHERE id = ?",
            (new_skipped, new_traded, new_note, signal_id),
        )
        conn.commit()
        return cur.rowcount > 0


def upsert_session_segment(row: dict[str, Any]) -> None:
    """Upsert today's conclusion for one intraday segment."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO session_segment(
                trade_date, segment, label, action, mainline, phase,
                temperature, reason, size_hint, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, segment) DO UPDATE SET
                label = excluded.label,
                action = excluded.action,
                mainline = excluded.mainline,
                phase = excluded.phase,
                temperature = excluded.temperature,
                reason = excluded.reason,
                size_hint = excluded.size_hint,
                updated_at = excluded.updated_at
            """,
            (
                row.get("trade_date"),
                row.get("segment"),
                row.get("label"),
                row.get("action"),
                row.get("mainline"),
                row.get("phase"),
                row.get("temperature"),
                row.get("reason"),
                row.get("size_hint"),
                row.get("updated_at"),
            ),
        )
        conn.commit()


def load_session_segments(trade_date: str) -> list[dict[str, Any]]:
    """Return saved segment conclusions for a trade date."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, segment, label, action, mainline, phase,
                   temperature, reason, size_hint, updated_at
            FROM session_segment
            WHERE trade_date = ?
            """,
            (trade_date,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_mainline_switch(row: dict[str, Any]) -> None:
    """Append a mainline switch event for the day."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO mainline_switch(
                trade_date, switched_at, from_name, to_name, action, phase, temperature
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("trade_date"),
                row.get("switched_at"),
                row.get("from_name"),
                row.get("to_name"),
                row.get("action"),
                row.get("phase"),
                row.get("temperature"),
            ),
        )
        conn.commit()


def load_mainline_switches(trade_date: str, limit: int = 40) -> list[dict[str, Any]]:
    """Return today's mainline switches, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, trade_date, switched_at, from_name, to_name, action, phase, temperature
            FROM mainline_switch
            WHERE trade_date = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (trade_date, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_trend_override(trade_date: str, code: str, verdict: str) -> dict[str, Any]:
    """Save a manual up/down trend judgment for one ticker on a trade date."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    code = str(code or "").zfill(6)
    verdict = "up" if verdict == "up" else "down"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO trend_override(trade_date, code, verdict, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(trade_date, code) DO UPDATE SET
                verdict = excluded.verdict,
                updated_at = excluded.updated_at
            """,
            (trade_date, code, verdict, now),
        )
        conn.commit()
    return {"trade_date": trade_date, "code": code, "verdict": verdict, "updated_at": now}


def load_trend_overrides(trade_date: str) -> dict[str, str]:
    """Return manual trend judgments keyed by code for a trade date."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT code, verdict FROM trend_override WHERE trade_date = ?",
            (trade_date,),
        ).fetchall()
    return {str(r["code"]).zfill(6): str(r["verdict"]) for r in rows}


def delete_trend_override(trade_date: str, code: str) -> bool:
    """Remove a manual trend judgment."""
    code = str(code or "").zfill(6)
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM trend_override WHERE trade_date = ? AND code = ?",
            (trade_date, code),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_signal(signal_id: int) -> bool:
    """Hard-delete one review signal row."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM signals WHERE id = ?", (signal_id,))
        conn.commit()
        return cur.rowcount > 0
