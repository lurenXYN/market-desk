"""SQLite persistence for daily snapshots and auction locks."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from market_desk.config import DATA_DIR, DB_PATH


def _connect() -> sqlite3.Connection:
    """Open a short-lived SQLite connection with WAL and a busy timeout."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass
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
                created_at TEXT NOT NULL,
                last_buy_date TEXT
            )
            """
        )
        pos_cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(positions)").fetchall()
        }
        if "last_buy_date" not in pos_cols:
            conn.execute("ALTER TABLE positions ADD COLUMN last_buy_date TEXT")
            conn.execute(
                """
                UPDATE positions
                SET last_buy_date = substr(created_at, 1, 10)
                WHERE last_buy_date IS NULL OR last_buy_date = ''
                """
            )
        for col, decl in (
            ("closed_date", "TEXT"),
            ("last_sell_date", "TEXT"),
            ("last_sell_price", "REAL"),
            ("day_sold_qty", "INTEGER DEFAULT 0"),
            ("day_realized_pnl", "REAL DEFAULT 0"),
            ("peak_price", "REAL"),
        ):
            if col not in pos_cols:
                conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {decl}")
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
                fill_price REAL,
                fill_qty INTEGER,
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
        if "fill_price" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN fill_price REAL")
        if "fill_qty" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN fill_qty INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_digest (
                trade_date TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT,
                note TEXT,
                suggest_price REAL,
                stop_price REAL,
                chase_price REAL,
                first_seen_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorite_boards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bk TEXT NOT NULL UNIQUE,
                name TEXT,
                kind TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gap_fade_strikes (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                name TEXT,
                open_gap_pct REAL,
                from_open_pct REAL,
                flagged INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (code, trade_date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_blacklist (
                code TEXT PRIMARY KEY,
                name TEXT,
                reason TEXT,
                source TEXT NOT NULL DEFAULT 'auto',
                strike_n INTEGER DEFAULT 0,
                clean_streak INTEGER DEFAULT 0,
                blocked_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                note TEXT
            )
            """
        )
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
        # Secondary indexes for hot read paths (refresh + review).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_trade_date ON signals(trade_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mainline_switch_date ON mainline_switch(trade_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_board_daily_date ON board_daily(trade_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_positions_code ON positions(code)"
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
    """Return prior-day sector stats keyed by board code, oldest first.

    Only scans a bounded recent window (``days + 4`` calendar cushion) so the
    ``board_daily`` table does not grow into a full-history scan each refresh.
    """
    window = max(int(days) + 4, 8)
    with _connect() as conn:
        date_rows = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM board_daily
            WHERE trade_date < ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (before_date, window),
        ).fetchall()
        dates = [str(r["trade_date"]) for r in date_rows]
        if not dates:
            return {}
        placeholders = ",".join("?" for _ in dates)
        rows = conn.execute(
            f"""
            SELECT trade_date, bk, name, zt_n, dt_n, pct, leader_boards, status
            FROM board_daily
            WHERE trade_date IN ({placeholders})
            ORDER BY trade_date ASC
            """,
            dates,
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        out.setdefault(item["bk"], []).append(item)
    for bk, series in list(out.items()):
        out[bk] = series[-days:]
    return out


_POS_SELECT = """
    id, code, name, buy_price, qty, note, created_at, last_buy_date,
    closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl,
    peak_price
"""


def _position_item(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Normalize a positions table row for API / decorate use."""
    item = dict(row)
    if not item.get("last_buy_date"):
        item["last_buy_date"] = str(item.get("created_at") or "")[:10] or None
    item["day_sold_qty"] = int(item.get("day_sold_qty") or 0)
    item["day_realized_pnl"] = float(item.get("day_realized_pnl") or 0)
    qty = int(item.get("qty") or 0)
    item["closed"] = qty <= 0 and bool(str(item.get("closed_date") or "").strip())
    peak = item.get("peak_price")
    item["peak_price"] = float(peak) if peak not in (None, "") else None
    return item


def touch_position_peaks(peaks: dict[int, float]) -> int:
    """Persist updated hold-peak prices keyed by position id. Returns rows touched."""
    if not peaks:
        return 0
    n = 0
    with _connect() as conn:
        for pid, peak in peaks.items():
            try:
                pid_i = int(pid)
                peak_f = float(peak)
            except (TypeError, ValueError):
                continue
            if peak_f <= 0:
                continue
            cur = conn.execute(
                """
                UPDATE positions
                SET peak_price = ?
                WHERE id = ? AND qty > 0
                  AND (peak_price IS NULL OR peak_price < ?)
                """,
                (peak_f, pid_i, peak_f),
            )
            n += int(cur.rowcount or 0)
        conn.commit()
    return n


def add_position(code: str, name: str, buy_price: float, qty: int, note: str = "") -> dict[str, Any]:
    """Insert a position, or average into an existing open same-code row."""
    code = str(code or "").zfill(6)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    buy_day = now[:10]
    with _connect() as conn:
        existing = conn.execute(
            f"""
            SELECT {_POS_SELECT}
            FROM positions WHERE code = ? ORDER BY id DESC LIMIT 1
            """,
            (code,),
        ).fetchone()
        if existing:
            old_qty = int(existing["qty"] or 0)
            old_px = float(existing["buy_price"] or 0)
            # Reopen a same-day closed row instead of averaging into qty=0.
            if old_qty <= 0:
                conn.execute(
                    """
                    UPDATE positions
                    SET name = ?, buy_price = ?, qty = ?, note = ?, last_buy_date = ?,
                        closed_date = NULL, created_at = ?, peak_price = ?
                    WHERE id = ?
                    """,
                    (
                        name or existing["name"] or code,
                        float(buy_price),
                        int(qty),
                        note or existing["note"] or "",
                        buy_day,
                        now,
                        float(buy_price),
                        int(existing["id"]),
                    ),
                )
                conn.commit()
                return {
                    "id": int(existing["id"]),
                    "code": code,
                    "name": name or existing["name"] or code,
                    "buy_price": float(buy_price),
                    "qty": int(qty),
                    "note": note or existing["note"] or "",
                    "created_at": now,
                    "last_buy_date": buy_day,
                    "reopened": True,
                }
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
                SET name = ?, buy_price = ?, qty = ?, note = ?, last_buy_date = ?,
                    closed_date = NULL
                WHERE id = ?
                """,
                (
                    name or existing["name"] or code,
                    round(avg, 4),
                    new_qty,
                    merged_note,
                    buy_day,
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
                "created_at": existing["created_at"] or now,
                "last_buy_date": buy_day,
                "averaged": True,
            }
        cur = conn.execute(
            """
            INSERT INTO positions(
                code, name, buy_price, qty, note, created_at, last_buy_date,
                closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl,
                peak_price
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 0, ?)
            """,
            (code, name, buy_price, qty, note, now, buy_day, float(buy_price)),
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
        "last_buy_date": buy_day,
    }


def trim_position(
    pid: int,
    qty: int,
    *,
    sell_price: float | None = None,
    trade_date: str | None = None,
) -> dict[str, Any] | None:
    """Reduce shares; keep qty=0 rows as closed-today until the next trade day."""
    sell = int(qty)
    if sell <= 0:
        return None
    day = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_POS_SELECT} FROM positions WHERE id = ?",
            (pid,),
        ).fetchone()
        if not row:
            return None
        hold = int(row["qty"] or 0)
        if hold <= 0:
            return _position_item(row)
        sell = min(sell, hold)
        left = hold - sell
        buy = float(row["buy_price"] or 0)
        px = float(sell_price) if sell_price is not None and float(sell_price) > 0 else buy
        chunk_pnl = round((px - buy) * sell, 2)
        prev_day = str(row["last_sell_date"] or "")[:10]
        if prev_day == day:
            day_sold = int(row["day_sold_qty"] or 0) + sell
            day_pnl = round(float(row["day_realized_pnl"] or 0) + chunk_pnl, 2)
        else:
            day_sold = sell
            day_pnl = chunk_pnl
        closed_date = day if left <= 0 else None
        conn.execute(
            """
            UPDATE positions
            SET qty = ?, closed_date = ?, last_sell_date = ?, last_sell_price = ?,
                day_sold_qty = ?, day_realized_pnl = ?
            WHERE id = ?
            """,
            (left, closed_date, day, round(px, 4), day_sold, day_pnl, pid),
        )
        conn.commit()
        item = _position_item(
            {
                **dict(row),
                "qty": left,
                "closed_date": closed_date,
                "last_sell_date": day,
                "last_sell_price": round(px, 4),
                "day_sold_qty": day_sold,
                "day_realized_pnl": day_pnl,
            }
        )
        item["trimmed"] = sell
        item["sell_price"] = round(px, 4)
        item["realized_chunk"] = chunk_pnl
        return item


def delete_position(pid: int) -> bool:
    """Delete a position by id. Return True if a row was removed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM positions WHERE id = ?", (pid,))
        conn.commit()
        return cur.rowcount > 0


def purge_stale_closed_positions(trade_date: str | None = None) -> dict[str, int]:
    """Drop fully closed rows from prior trade days; reset stale day-realized fields."""
    day = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    with _connect() as conn:
        cur_del = conn.execute(
            """
            DELETE FROM positions
            WHERE qty <= 0
              AND closed_date IS NOT NULL
              AND closed_date <> ''
              AND closed_date < ?
            """,
            (day,),
        )
        cur_reset = conn.execute(
            """
            UPDATE positions
            SET day_sold_qty = 0, day_realized_pnl = 0
            WHERE qty > 0
              AND last_sell_date IS NOT NULL
              AND last_sell_date <> ''
              AND last_sell_date < ?
            """,
            (day,),
        )
        conn.commit()
        return {
            "deleted_closed": int(cur_del.rowcount or 0),
            "reset_day_pnl": int(cur_reset.rowcount or 0),
        }


def load_positions() -> list[dict[str, Any]]:
    """Return all locally recorded positions, newest first (includes closed-today)."""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {_POS_SELECT}
            FROM positions
            ORDER BY
              CASE WHEN qty > 0 THEN 0 ELSE 1 END,
              id DESC
            """
        ).fetchall()
    return [_position_item(row) for row in rows]


def position_buy_day(row: dict[str, Any] | None) -> str:
    """Return YYYY-MM-DD of the latest buy for a position row."""
    if not row:
        return ""
    day = str(row.get("last_buy_date") or "").strip()[:10]
    if day:
        return day
    return str(row.get("created_at") or "").strip()[:10]


def is_t1_locked(row: dict[str, Any] | None, trade_date: str | None) -> bool:
    """Return True when A-share T+1 blocks selling this position today."""
    buy_day = position_buy_day(row)
    day = str(trade_date or "").strip()[:10]
    return bool(buy_day and day and buy_day == day)


def upsert_signal(row: dict[str, Any]) -> None:
    """Insert or refresh a same-day signal keyed by date + code + type.

    ``signaled_at`` and ``price`` are kept from the first insert so the review
    panel shows the first watch time and the first suggested entry price.
    Payload is merged; first non-empty ``board_names`` is locked so later hot-board
    rotations cannot wipe the所属板块 column.
    """
    trade_date = row.get("trade_date")
    code = row.get("code")
    signal_type = row.get("signal_type")
    incoming = dict(row.get("payload") or {})
    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT signaled_at, price, payload
            FROM signals
            WHERE trade_date = ? AND code = ? AND signal_type = ?
            """,
            (trade_date, code, signal_type),
        ).fetchone()
        old_payload: dict[str, Any] = {}
        if existing and existing["payload"]:
            try:
                raw = json.loads(existing["payload"])
                if isinstance(raw, dict):
                    old_payload = raw
            except json.JSONDecodeError:
                old_payload = {}
        merged = {**old_payload, **incoming}
        old_boards = [
            str(x).strip()
            for x in (old_payload.get("board_names") or [])
            if str(x).strip()
        ]
        new_boards = [
            str(x).strip()
            for x in (incoming.get("board_names") or [])
            if str(x).strip()
        ]
        if old_boards and not new_boards:
            merged["board_names"] = old_boards
            if old_payload.get("vs_mainline") is not None and not incoming.get("vs_mainline"):
                merged["vs_mainline"] = old_payload.get("vs_mainline")
            if "board_match" in old_payload and "board_match" not in incoming:
                merged["board_match"] = old_payload.get("board_match")
        elif old_boards and new_boards and old_boards != new_boards:
            # Prefer richer first capture; only replace when newly resolved from empty.
            pass
        now_payload = json.dumps(merged, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO signals(
                trade_date, signaled_at, signal_type, action, phase, mainline,
                code, name, kind, price, last, ready, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, code, signal_type) DO UPDATE SET
                action = excluded.action,
                phase = excluded.phase,
                mainline = excluded.mainline,
                name = excluded.name,
                kind = excluded.kind,
                last = excluded.last,
                ready = excluded.ready,
                payload = excluded.payload
            """,
            (
                trade_date,
                row.get("signaled_at"),
                signal_type,
                row.get("action"),
                row.get("phase"),
                row.get("mainline"),
                code,
                row.get("name"),
                row.get("kind"),
                row.get("price"),
                row.get("last"),
                int(row.get("ready") or 0),
                now_payload,
            ),
        )
        conn.commit()


def load_signal(signal_id: int) -> dict[str, Any] | None:
    """Return one signal row by id, or None."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, trade_date, signaled_at, signal_type, action, phase, mainline,
                   code, name, kind, price, last, ready, payload,
                   outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
                   outcome_label, outcome_checked_at, note, skipped, traded,
                   fill_price, fill_qty
            FROM signals
            WHERE id = ?
            """,
            (int(signal_id),),
        ).fetchone()
    if not row:
        return None
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
    return item


_SIGNAL_SELECT = """
    SELECT id, trade_date, signaled_at, signal_type, action, phase, mainline,
           code, name, kind, price, last, ready, payload,
           outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
           outcome_label, outcome_checked_at, note, skipped, traded,
           fill_price, fill_qty
    FROM signals
"""


def _decode_signal_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Normalize SQLite signal rows into API-ready dicts."""
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


def load_signals(limit: int = 60) -> list[dict[str, Any]]:
    """Return recent signals, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            _SIGNAL_SELECT
            + """
            ORDER BY trade_date DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return _decode_signal_rows(rows)


def load_signals_for_date(trade_date: str) -> list[dict[str, Any]]:
    """Return all signals for one trade date, newest first."""
    day = str(trade_date or "").strip()[:10]
    if not day:
        return []
    with _connect() as conn:
        rows = conn.execute(
            _SIGNAL_SELECT
            + """
            WHERE trade_date = ?
            ORDER BY id DESC
            """,
            (day,),
        ).fetchall()
    return _decode_signal_rows(rows)


def list_signal_trade_dates(limit: int = 40) -> list[str]:
    """Return distinct trade dates that have signals, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM signals
            WHERE trade_date IS NOT NULL AND trade_date != ''
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [str(r["trade_date"]) for r in rows if r["trade_date"]]


def load_unscored_signals(before_date: str, limit: int = 80) -> list[dict[str, Any]]:
    """Return signals before a date that still lack an outcome label."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, trade_date, signaled_at, signal_type, action, phase, mainline,
                   code, name, kind, price, last, ready, payload,
                   outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
                   outcome_label, outcome_checked_at, note, skipped, traded,
                   fill_price, fill_qty
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
    fill_price: float | None = None,
    fill_qty: int | None = None,
) -> bool:
    """Update user review flags and optional fill fields on a signal row."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, note, skipped, traded, fill_price, fill_qty FROM signals WHERE id = ?",
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
        new_fill_px = row["fill_price"] if fill_price is None else float(fill_price)
        new_fill_qty = row["fill_qty"] if fill_qty is None else int(fill_qty)
        cur = conn.execute(
            """
            UPDATE signals
            SET skipped = ?, traded = ?, note = ?, fill_price = ?, fill_qty = ?
            WHERE id = ?
            """,
            (new_skipped, new_traded, new_note, new_fill_px, new_fill_qty, signal_id),
        )
        conn.commit()
        return cur.rowcount > 0


def save_review_digest(trade_date: str, payload: dict[str, Any]) -> None:
    """Upsert one trade-date review digest for historical charts."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO review_digest(trade_date, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (trade_date, json.dumps(payload, ensure_ascii=False), now),
        )
        conn.commit()


def load_review_digests(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent review digests, oldest-first for charting."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, payload, updated_at
            FROM review_digest
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in reversed(list(rows)):
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["trade_date"] = row["trade_date"]
        payload["updated_at"] = row["updated_at"]
        out.append(payload)
    return out


def load_setting(key: str) -> Any | None:
    """Load one JSON settings value by key."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return None


def save_setting(key: str, value: Any) -> None:
    """Persist one JSON settings value by key."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), now),
        )
        conn.commit()


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


def delete_mainline_switch(switch_id: int) -> None:
    """Remove one mainline switch row (used to drop flip-flop noise)."""
    with _connect() as conn:
        conn.execute("DELETE FROM mainline_switch WHERE id = ?", (int(switch_id),))
        conn.commit()


def try_add_mainline_switch(row: dict[str, Any], min_seconds: int = 300) -> bool:
    """Append a switch unless it is rapid noise or an immediate flip-flop.

    Returns True when a row was inserted. Inside ``min_seconds``:
    - A→B then B→A (flip) deletes the noisy prior switch.
    - A→B then B→C coalesces into a single A→C row (no intermediate spam).
    """
    trade_date = row.get("trade_date")
    from_name = (row.get("from_name") or "").strip()
    to_name = (row.get("to_name") or "").strip()
    switched_at = row.get("switched_at") or ""
    if not trade_date or not from_name or not to_name or from_name == to_name:
        return False
    with _connect() as conn:
        last = conn.execute(
            """
            SELECT id, switched_at, from_name, to_name
            FROM mainline_switch
            WHERE trade_date = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (trade_date,),
        ).fetchone()
        if last:
            age = _switch_age_seconds(last["switched_at"], switched_at)
            flip = (
                (last["from_name"] or "") == to_name
                and (last["to_name"] or "") == from_name
            )
            if age is not None and age < int(min_seconds):
                if flip:
                    conn.execute(
                        "DELETE FROM mainline_switch WHERE id = ?",
                        (int(last["id"]),),
                    )
                    conn.commit()
                    return False
                # Coalesce A→B→C into A→C inside the debounce window.
                conn.execute(
                    """
                    UPDATE mainline_switch
                    SET switched_at = ?, to_name = ?, action = ?, phase = ?, temperature = ?
                    WHERE id = ?
                    """,
                    (
                        switched_at,
                        to_name,
                        row.get("action"),
                        row.get("phase"),
                        row.get("temperature"),
                        int(last["id"]),
                    ),
                )
                conn.commit()
                return False
        conn.execute(
            """
            INSERT INTO mainline_switch(
                trade_date, switched_at, from_name, to_name, action, phase, temperature
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_date,
                switched_at,
                from_name,
                to_name,
                row.get("action"),
                row.get("phase"),
                row.get("temperature"),
            ),
        )
        conn.commit()
    return True


def _switch_age_seconds(prev_at: str | None, cur_at: str | None) -> float | None:
    """Return seconds between two ``YYYY-MM-DD HH:MM:SS`` timestamps."""
    if not prev_at or not cur_at:
        return None
    try:
        prev = datetime.strptime(str(prev_at)[:19], "%Y-%m-%d %H:%M:%S")
        cur = datetime.strptime(str(cur_at)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return (cur - prev).total_seconds()


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


def add_watchlist(
    code: str,
    name: str = "",
    *,
    note: str = "",
    suggest_price: float | None = None,
    stop_price: float | None = None,
    chase_price: float | None = None,
) -> dict[str, Any]:
    """Insert or refresh a personal watchlist row; first_seen/suggest stay locked."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    code = str(code or "").zfill(6)
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, first_seen_at, suggest_price, stop_price, chase_price FROM watchlist WHERE code = ?",
            (code,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE watchlist
                SET name = COALESCE(NULLIF(?, ''), name),
                    note = ?,
                    stop_price = COALESCE(?, stop_price),
                    chase_price = COALESCE(?, chase_price)
                WHERE code = ?
                """,
                (name, note, stop_price, chase_price, code),
            )
            if existing["suggest_price"] is None and suggest_price is not None:
                conn.execute(
                    "UPDATE watchlist SET suggest_price = ? WHERE code = ?",
                    (float(suggest_price), code),
                )
            conn.commit()
            row = conn.execute("SELECT * FROM watchlist WHERE code = ?", (code,)).fetchone()
            return dict(row)
        conn.execute(
            """
            INSERT INTO watchlist(
                code, name, note, suggest_price, stop_price, chase_price, first_seen_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (code, name, note, suggest_price, stop_price, chase_price, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM watchlist WHERE code = ?", (code,)).fetchone()
        return dict(row)


def load_watchlist() -> list[dict[str, Any]]:
    """Return personal watchlist rows, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, code, name, note, suggest_price, stop_price, chase_price,
                   first_seen_at, created_at
            FROM watchlist
            ORDER BY id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def delete_watchlist(item_id: int) -> bool:
    """Delete one watchlist row by id."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE id = ?", (int(item_id),))
        conn.commit()
        return cur.rowcount > 0


def add_favorite_board(
    bk: str,
    name: str = "",
    *,
    kind: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Insert or refresh a personally favored sector board."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bk = str(bk or "").strip().upper()
    if not bk.startswith("BK"):
        raise ValueError("bk must look like BKXXXX")
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM favorite_boards WHERE bk = ?",
            (bk,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE favorite_boards
                SET name = COALESCE(NULLIF(?, ''), name),
                    kind = COALESCE(NULLIF(?, ''), kind),
                    note = ?
                WHERE bk = ?
                """,
                (name, kind, note, bk),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM favorite_boards WHERE bk = ?", (bk,)).fetchone()
            return dict(row)
        conn.execute(
            """
            INSERT INTO favorite_boards(bk, name, kind, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (bk, name, kind, note, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM favorite_boards WHERE bk = ?", (bk,)).fetchone()
        return dict(row)


def load_favorite_boards() -> list[dict[str, Any]]:
    """Return personally favored boards, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, bk, name, kind, note, created_at
            FROM favorite_boards
            ORDER BY id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def delete_favorite_board(item_id: int) -> bool:
    """Delete one favored board by id."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM favorite_boards WHERE id = ?", (int(item_id),))
        conn.commit()
        return cur.rowcount > 0


def delete_favorite_board_by_bk(bk: str) -> bool:
    """Delete one favored board by East Money board code."""
    bk = str(bk or "").strip().upper()
    if not bk:
        return False
    with _connect() as conn:
        cur = conn.execute("DELETE FROM favorite_boards WHERE bk = ?", (bk,))
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


def export_backup_payload() -> dict[str, Any]:
    """Export core local tables as a JSON-serializable backup dict."""
    with _connect() as conn:
        signals = [dict(r) for r in conn.execute("SELECT * FROM signals ORDER BY id").fetchall()]
        positions = [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY id").fetchall()]
        watchlist = [dict(r) for r in conn.execute("SELECT * FROM watchlist ORDER BY id").fetchall()]
        favorite_boards = [
            dict(r) for r in conn.execute("SELECT * FROM favorite_boards ORDER BY id").fetchall()
        ]
        stock_blacklist = [
            dict(r) for r in conn.execute("SELECT * FROM stock_blacklist ORDER BY code").fetchall()
        ]
        digests = [dict(r) for r in conn.execute("SELECT * FROM review_digest ORDER BY trade_date").fetchall()]
        settings = [dict(r) for r in conn.execute("SELECT * FROM settings").fetchall()]
        overrides = [dict(r) for r in conn.execute("SELECT * FROM trend_override").fetchall()]
    return {
        "version": 1,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "signals": signals,
        "positions": positions,
        "watchlist": watchlist,
        "favorite_boards": favorite_boards,
        "stock_blacklist": stock_blacklist,
        "review_digest": digests,
        "settings": settings,
        "trend_override": overrides,
    }


def import_backup_payload(payload: dict[str, Any], *, replace: bool = False) -> dict[str, int]:
    """Import a backup payload. When replace=True, clear target tables first."""
    if not isinstance(payload, dict):
        raise ValueError("backup payload must be an object")
    counts = {
        "signals": 0,
        "positions": 0,
        "watchlist": 0,
        "favorite_boards": 0,
        "stock_blacklist": 0,
        "review_digest": 0,
        "settings": 0,
        "trend_override": 0,
    }
    with _connect() as conn:
        if replace:
            for table in (
                "signals",
                "positions",
                "watchlist",
                "favorite_boards",
                "stock_blacklist",
                "review_digest",
                "settings",
                "trend_override",
            ):
                conn.execute(f"DELETE FROM {table}")
        for row in payload.get("positions") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO positions(
                    code, name, buy_price, qty, note, created_at, last_buy_date,
                    closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("code") or "").zfill(6),
                    row.get("name") or "",
                    float(row.get("buy_price") or 0),
                    int(row.get("qty") or 0),
                    row.get("note") or "",
                    row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("last_buy_date") or str(row.get("created_at") or "")[:10] or None,
                    row.get("closed_date"),
                    row.get("last_sell_date"),
                    row.get("last_sell_price"),
                    int(row.get("day_sold_qty") or 0),
                    float(row.get("day_realized_pnl") or 0),
                ),
            )
            counts["positions"] += 1
        for row in payload.get("watchlist") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO watchlist(
                    code, name, note, suggest_price, stop_price, chase_price, first_seen_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    note = excluded.note,
                    stop_price = excluded.stop_price,
                    chase_price = excluded.chase_price
                """,
                (
                    str(row.get("code") or "").zfill(6),
                    row.get("name") or "",
                    row.get("note") or "",
                    row.get("suggest_price"),
                    row.get("stop_price"),
                    row.get("chase_price"),
                    row.get("first_seen_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["watchlist"] += 1
        for row in payload.get("favorite_boards") or []:
            if not isinstance(row, dict):
                continue
            bk = str(row.get("bk") or "").strip().upper()
            if not bk.startswith("BK"):
                continue
            conn.execute(
                """
                INSERT INTO favorite_boards(bk, name, kind, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(bk) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    note = excluded.note
                """,
                (
                    bk,
                    row.get("name") or "",
                    row.get("kind") or "",
                    row.get("note") or "",
                    row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["favorite_boards"] += 1
        for row in payload.get("stock_blacklist") or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").zfill(6)
            if len(code) != 6 or not code.isdigit():
                continue
            conn.execute(
                """
                INSERT INTO stock_blacklist(
                    code, name, reason, source, strike_n, clean_streak,
                    blocked_at, updated_at, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    reason = excluded.reason,
                    source = excluded.source,
                    strike_n = excluded.strike_n,
                    clean_streak = excluded.clean_streak,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    code,
                    row.get("name") or "",
                    row.get("reason") or "",
                    row.get("source") or "manual",
                    int(row.get("strike_n") or 0),
                    int(row.get("clean_streak") or 0),
                    row.get("blocked_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("note") or "",
                ),
            )
            counts["stock_blacklist"] += 1
        for row in payload.get("signals") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO signals(
                    trade_date, signaled_at, signal_type, action, phase, mainline,
                    code, name, kind, price, last, ready, payload,
                    outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
                    outcome_label, outcome_checked_at, note, skipped, traded, fill_price, fill_qty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, code, signal_type) DO UPDATE SET
                    action = excluded.action,
                    phase = excluded.phase,
                    mainline = excluded.mainline,
                    name = excluded.name,
                    kind = excluded.kind,
                    last = excluded.last,
                    ready = excluded.ready,
                    payload = excluded.payload,
                    note = excluded.note,
                    skipped = excluded.skipped,
                    traded = excluded.traded,
                    fill_price = excluded.fill_price,
                    fill_qty = excluded.fill_qty,
                    outcome_day1_pct = excluded.outcome_day1_pct,
                    outcome_day3_pct = excluded.outcome_day3_pct,
                    outcome_mfe_pct = excluded.outcome_mfe_pct,
                    outcome_mae_pct = excluded.outcome_mae_pct,
                    outcome_label = excluded.outcome_label,
                    outcome_checked_at = excluded.outcome_checked_at
                """,
                (
                    row.get("trade_date"),
                    row.get("signaled_at"),
                    row.get("signal_type"),
                    row.get("action"),
                    row.get("phase"),
                    row.get("mainline"),
                    str(row.get("code") or "").zfill(6),
                    row.get("name"),
                    row.get("kind"),
                    row.get("price"),
                    row.get("last"),
                    int(row.get("ready") or 0),
                    row.get("payload")
                    if isinstance(row.get("payload"), str)
                    else json.dumps(row.get("payload") or {}, ensure_ascii=False),
                    row.get("outcome_day1_pct"),
                    row.get("outcome_day3_pct"),
                    row.get("outcome_mfe_pct"),
                    row.get("outcome_mae_pct"),
                    row.get("outcome_label"),
                    row.get("outcome_checked_at"),
                    row.get("note"),
                    int(row.get("skipped") or 0),
                    int(row.get("traded") or 0),
                    row.get("fill_price"),
                    row.get("fill_qty"),
                ),
            )
            counts["signals"] += 1
        for row in payload.get("review_digest") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO review_digest(trade_date, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    row.get("trade_date"),
                    row.get("payload")
                    if isinstance(row.get("payload"), str)
                    else json.dumps(row.get("payload") or {}, ensure_ascii=False),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["review_digest"] += 1
        for row in payload.get("settings") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (
                    row.get("key"),
                    row.get("value")
                    if isinstance(row.get("value"), str)
                    else json.dumps(row.get("value"), ensure_ascii=False),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["settings"] += 1
        for row in payload.get("trend_override") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO trend_override(trade_date, code, verdict, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_date, code) DO UPDATE SET
                    verdict = excluded.verdict,
                    updated_at = excluded.updated_at
                """,
                (
                    row.get("trade_date"),
                    str(row.get("code") or "").zfill(6),
                    row.get("verdict"),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["trend_override"] += 1
        conn.commit()
    return counts
