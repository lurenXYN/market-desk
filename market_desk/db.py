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
            CREATE TABLE IF NOT EXISTS fund_flow_daily (
                trade_date TEXT NOT NULL,
                bk TEXT NOT NULL,
                kind TEXT NOT NULL,
                name TEXT,
                pct REAL,
                main_net REAL,
                main_pct REAL,
                super_net REAL,
                large_net REAL,
                leader_name TEXT,
                leader_code TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (trade_date, bk, kind)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fund_flow_daily_date
            ON fund_flow_daily(trade_date)
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
            ("entry_board", "TEXT"),
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
            CREATE TABLE IF NOT EXISTS theme_reputation (
                theme_key TEXT PRIMARY KEY,
                fade_n INTEGER NOT NULL DEFAULT 0,
                persist_n INTEGER NOT NULL DEFAULT 0,
                score_adj REAL NOT NULL DEFAULT 0,
                auto_adj REAL NOT NULL DEFAULT 0,
                manual_adj REAL NOT NULL DEFAULT 0,
                manual_note TEXT,
                last_fade_date TEXT,
                last_persist_date TEXT,
                label TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        theme_rep_cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(theme_reputation)").fetchall()
        }
        for col, decl in (
            ("auto_adj", "REAL NOT NULL DEFAULT 0"),
            ("manual_adj", "REAL NOT NULL DEFAULT 0"),
            ("manual_note", "TEXT"),
            ("trade_adj", "REAL NOT NULL DEFAULT 0"),
        ):
            if col not in theme_rep_cols:
                conn.execute(f"ALTER TABLE theme_reputation ADD COLUMN {col} {decl}")
        # Backfill auto_adj from score_adj when still zero after upgrade.
        conn.execute(
            """
            UPDATE theme_reputation
            SET auto_adj = score_adj - COALESCE(manual_adj, 0) - COALESCE(trade_adj, 0)
            WHERE ABS(COALESCE(auto_adj, 0)) < 1e-9
              AND ABS(COALESCE(score_adj, 0)) > 1e-9
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_reputation (
                code TEXT PRIMARY KEY,
                name TEXT,
                win_n INTEGER NOT NULL DEFAULT 0,
                loss_n INTEGER NOT NULL DEFAULT 0,
                fake_n INTEGER NOT NULL DEFAULT 0,
                score_adj REAL NOT NULL DEFAULT 0,
                streak_loss INTEGER NOT NULL DEFAULT 0,
                last_pnl_pct REAL,
                last_trade_date TEXT,
                label TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS theme_day_outcome (
                trade_date TEXT NOT NULL,
                next_date TEXT NOT NULL,
                theme_key TEXT NOT NULL,
                outcome TEXT NOT NULL,
                mainline TEXT,
                zt_n INTEGER,
                next_zt_n INTEGER,
                pct REAL,
                next_pct REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (trade_date, next_date, theme_key)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_theme_outcome_theme ON theme_day_outcome(theme_key, next_date DESC)"
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
        _ensure_auth_and_user_scope(conn)
        conn.commit()


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return column names for a SQLite table."""
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_auth_and_user_scope(conn: sqlite3.Connection) -> None:
    """Create users/sessions and migrate personal tables to user_id scope."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            status TEXT NOT NULL DEFAULT 'pending',
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            approved_by INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)"
    )

    # positions: add user_id (no unique constraint historically)
    pos_cols = _table_cols(conn, "positions")
    if "user_id" not in pos_cols:
        conn.execute("ALTER TABLE positions ADD COLUMN user_id INTEGER")
        conn.execute(
            "UPDATE positions SET user_id = 1 WHERE user_id IS NULL"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_positions_user ON positions(user_id)"
    )

    # watchlist: rebuild if still globally unique on code
    _migrate_watchlist_user_scope(conn)
    _migrate_favorite_boards_user_scope(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_user_meta (
            user_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            skipped INTEGER NOT NULL DEFAULT 0,
            traded INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            fill_price REAL,
            fill_qty INTEGER,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, signal_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_user_meta_signal "
        "ON signal_user_meta(signal_id)"
    )
    # One-shot: copy legacy global traded/fill flags onto admin (user_id=1).
    legacy = conn.execute(
        "SELECT COUNT(*) AS n FROM signal_user_meta"
    ).fetchone()
    if legacy and int(legacy["n"] or 0) == 0:
        conn.execute(
            """
            INSERT OR IGNORE INTO signal_user_meta(
                user_id, signal_id, skipped, traded, note, fill_price, fill_qty, updated_at
            )
            SELECT 1, id,
                   COALESCE(skipped, 0), COALESCE(traded, 0), note,
                   fill_price, fill_qty, datetime('now','localtime')
            FROM signals
            WHERE COALESCE(traded, 0) != 0
               OR COALESCE(skipped, 0) != 0
               OR fill_price IS NOT NULL
               OR fill_qty IS NOT NULL
               OR (note IS NOT NULL AND note != '')
            """
        )


def _migrate_watchlist_user_scope(conn: sqlite3.Connection) -> None:
    """Ensure watchlist is unique per (user_id, code)."""
    cols = _table_cols(conn, "watchlist")
    if "user_id" in cols:
        # Already migrated or fresh create with user_id — ensure index.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_user_code "
            "ON watchlist(user_id, code)"
        )
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            note TEXT,
            suggest_price REAL,
            stop_price REAL,
            chase_price REAL,
            first_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, code)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO watchlist_v2(
            id, user_id, code, name, note, suggest_price, stop_price, chase_price,
            first_seen_at, created_at
        )
        SELECT id, 1, code, name, note, suggest_price, stop_price, chase_price,
               first_seen_at, created_at
        FROM watchlist
        """
    )
    conn.execute("DROP TABLE watchlist")
    conn.execute("ALTER TABLE watchlist_v2 RENAME TO watchlist")


def _migrate_favorite_boards_user_scope(conn: sqlite3.Connection) -> None:
    """Ensure favorite_boards is unique per (user_id, bk)."""
    cols = _table_cols(conn, "favorite_boards")
    if "user_id" in cols:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_fav_boards_user_bk "
            "ON favorite_boards(user_id, bk)"
        )
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS favorite_boards_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bk TEXT NOT NULL,
            name TEXT,
            kind TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, bk)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO favorite_boards_v2(id, user_id, bk, name, kind, note, created_at)
        SELECT id, 1, bk, name, kind, note, created_at FROM favorite_boards
        """
    )
    conn.execute("DROP TABLE favorite_boards")
    conn.execute("ALTER TABLE favorite_boards_v2 RENAME TO favorite_boards")


def user_count() -> int:
    """Return number of registered users."""
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"] if row else 0)


def create_user(
    username: str,
    password_hash: str,
    *,
    role: str = "user",
    status: str = "pending",
    must_change_password: bool = False,
) -> dict[str, Any]:
    """Insert a user row and return it."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO users(
                username, password_hash, role, status, must_change_password, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(username).strip(),
                password_hash,
                role,
                status,
                1 if must_change_password else 0,
                now,
            ),
        )
        uid = int(cur.lastrowid)
        if status == "active":
            conn.execute(
                "UPDATE users SET approved_at = ? WHERE id = ?",
                (now, uid),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row)


def get_user_by_username(username: str) -> dict[str, Any] | None:
    """Load one user by username."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (str(username or "").strip(),),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Load one user by id."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (int(user_id),),
        ).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    """Return all users, pending first then newest."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM users
            ORDER BY
              CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
              id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def approve_user(user_id: int, admin_id: int) -> dict[str, Any] | None:
    """Mark a user active after admin approval."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET status = 'active', approved_at = ?, approved_by = ?
            WHERE id = ?
            """,
            (now, int(admin_id), int(user_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
    return dict(row) if row else None


def reject_user(user_id: int, admin_id: int) -> dict[str, Any] | None:
    """Mark a registration rejected."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET status = 'rejected', approved_at = ?, approved_by = ?
            WHERE id = ?
            """,
            (now, int(admin_id), int(user_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
    return dict(row) if row else None


def update_user_password(
    user_id: int,
    password_hash: str,
    *,
    must_change: bool = False,
) -> None:
    """Replace a user's password hash."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, must_change_password = ?
            WHERE id = ?
            """,
            (password_hash, 1 if must_change else 0, int(user_id)),
        )
        conn.commit()


def create_session(user_id: int, token: str, expires_at: str) -> None:
    """Persist a login session token."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO auth_sessions(token, user_id, expires_at, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token, int(user_id), expires_at, now, now),
        )
        conn.commit()


def delete_session(token: str) -> None:
    """Remove one session token."""
    with _connect() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
        conn.commit()


def touch_session(token: str) -> None:
    """Bump last_seen_at for an active session."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE token = ?",
            (now, token),
        )
        conn.commit()


def get_session_user(token: str) -> dict[str, Any] | None:
    """Return the user row for a non-expired session token."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT u.*
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at >= ?
            """,
            (token, now),
        ).fetchone()
    return dict(row) if row else None


def load_user_setting(user_id: int, key: str) -> Any | None:
    """Load one JSON setting scoped to a user."""
    return load_setting(f"user:{int(user_id)}:{key}")


def save_user_setting(user_id: int, key: str, value: Any) -> None:
    """Persist one JSON setting scoped to a user."""
    save_setting(f"user:{int(user_id)}:{key}", value)


def save_daily(trade_date: str, payload: dict[str, Any], *, merge: bool = True) -> None:
    """Upsert today's compact daily row used by the history table.

    When ``merge`` is True (default), patch onto any existing payload for the
    day so later writes (e.g. mainline) do not wipe metrics already saved.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    day = str(trade_date or "")[:10]
    data = dict(payload or {})
    with _connect() as conn:
        if merge and day:
            row = conn.execute(
                "SELECT payload FROM daily_snapshot WHERE trade_date = ?",
                (day,),
            ).fetchone()
            if row and row["payload"]:
                try:
                    old = json.loads(row["payload"])
                    if isinstance(old, dict):
                        merged = dict(old)
                        merged.update({k: v for k, v in data.items() if v is not None})
                        # Preserve prior mainline when new payload omits it.
                        if not str(merged.get("mainline") or "").strip():
                            prev_ml = str(old.get("mainline") or "").strip()
                            if prev_ml:
                                merged["mainline"] = prev_ml
                        data = merged
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        conn.execute(
            """
            INSERT INTO daily_snapshot(trade_date, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (day, json.dumps(data, ensure_ascii=False), now),
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


def save_fund_flow_daily(trade_date: str, rows: list[dict[str, Any]]) -> int:
    """Upsert one trade day's board money-flow prints for local week/month sums."""
    day = str(trade_date or "")[:10]
    if not day or not rows:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = []
    for r in rows:
        bk = str(r.get("bk") or "").strip()
        kind = str(r.get("kind") or "").strip() or "industry"
        if not bk:
            continue
        payload.append(
            (
                day,
                bk,
                kind,
                str(r.get("name") or ""),
                r.get("pct"),
                r.get("main_net"),
                r.get("main_pct"),
                r.get("super_net"),
                r.get("large_net"),
                str(r.get("leader_name") or ""),
                str(r.get("leader_code") or ""),
                now,
            )
        )
    if not payload:
        return 0
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO fund_flow_daily(
                trade_date, bk, kind, name, pct, main_net, main_pct,
                super_net, large_net, leader_name, leader_code, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, bk, kind) DO UPDATE SET
                name = excluded.name,
                pct = excluded.pct,
                main_net = excluded.main_net,
                main_pct = excluded.main_pct,
                super_net = excluded.super_net,
                large_net = excluded.large_net,
                leader_name = excluded.leader_name,
                leader_code = excluded.leader_code,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        conn.commit()
    return len(payload)


def list_fund_flow_dates(through: str, limit: int = 40) -> list[str]:
    """Return recent trade dates that have fund-flow rows (newest first)."""
    day = str(through or "")[:10]
    lim = max(1, min(int(limit or 40), 120))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM fund_flow_daily
            WHERE trade_date <= ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (day, lim),
        ).fetchall()
    return [str(r["trade_date"]) for r in rows]


def load_fund_flow_for_dates(dates: list[str]) -> list[dict[str, Any]]:
    """Load raw daily fund-flow rows for the given trade dates."""
    days = [str(d)[:10] for d in dates if str(d or "").strip()]
    if not days:
        return []
    placeholders = ",".join("?" for _ in days)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT trade_date, bk, kind, name, pct, main_net, main_pct,
                   super_net, large_net, leader_name, leader_code
            FROM fund_flow_daily
            WHERE trade_date IN ({placeholders})
            """,
            days,
        ).fetchall()
    return [
        {
            "trade_date": str(r["trade_date"]),
            "bk": str(r["bk"] or ""),
            "kind": str(r["kind"] or ""),
            "name": str(r["name"] or ""),
            "pct": r["pct"],
            "main_net": r["main_net"],
            "main_pct": r["main_pct"],
            "super_net": r["super_net"],
            "large_net": r["large_net"],
            "leader_name": str(r["leader_name"] or ""),
            "leader_code": str(r["leader_code"] or ""),
        }
        for r in rows
    ]


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


def load_board_rows_for_date(trade_date: str) -> list[dict[str, Any]]:
    """Return all board_daily rows for one trade date."""
    day = str(trade_date or "")[:10]
    if not day:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, bk, name, zt_n, dt_n, pct, leader_boards, status
            FROM board_daily
            WHERE trade_date = ?
            """,
            (day,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_theme_reputation(row: dict[str, Any], *, preserve_manual: bool = True) -> None:
    """Upsert one theme reputation aggregate.

    When ``preserve_manual`` is True, keep existing manual_adj / manual_note /
    trade_adj and recompute ``score_adj = auto_adj + manual_adj + trade_adj``.
    """
    theme = str(row.get("theme_key") or "").strip()
    if not theme:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    auto = float(row.get("auto_adj") if row.get("auto_adj") is not None else row.get("score_adj") or 0)
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT manual_adj, manual_note, trade_adj
            FROM theme_reputation WHERE theme_key = ?
            """,
            (theme,),
        ).fetchone()
        if preserve_manual and cur is not None:
            manual = float(cur["manual_adj"] or 0)
            note = cur["manual_note"]
            trade = float(cur["trade_adj"] or 0) if "trade_adj" in cur.keys() else 0.0
        else:
            manual = float(row.get("manual_adj") or 0)
            note = row.get("manual_note")
            trade = float(row.get("trade_adj") or 0)
        combined = round(auto + manual + trade, 2)
        from market_desk.theme_memory import label_for_rep

        label = label_for_rep(
            float(row.get("fade_n") or 0),
            float(row.get("persist_n") or 0),
            combined,
        )
        conn.execute(
            """
            INSERT INTO theme_reputation(
                theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(theme_key) DO UPDATE SET
                fade_n = excluded.fade_n,
                persist_n = excluded.persist_n,
                score_adj = excluded.score_adj,
                auto_adj = excluded.auto_adj,
                manual_adj = excluded.manual_adj,
                trade_adj = excluded.trade_adj,
                manual_note = excluded.manual_note,
                last_fade_date = excluded.last_fade_date,
                last_persist_date = excluded.last_persist_date,
                label = excluded.label,
                updated_at = excluded.updated_at
            """,
            (
                theme,
                int(row.get("fade_n") or 0),
                int(row.get("persist_n") or 0),
                combined,
                auto,
                manual,
                trade,
                note,
                row.get("last_fade_date"),
                row.get("last_persist_date"),
                label,
                now,
            ),
        )
        conn.commit()


def set_theme_manual_adj(
    theme_key: str,
    *,
    manual_adj: float | None = None,
    delta: float | None = None,
    note: str | None = None,
    clear: bool = False,
) -> dict[str, Any] | None:
    """Set or nudge manual theme reputation; returns the updated row."""
    from market_desk.config import THEME_MANUAL_ADJ_MAX, THEME_MANUAL_ADJ_MIN, THEME_REP_ADJ_MAX, THEME_REP_ADJ_MIN
    from market_desk.mainline import theme_key as canon_theme

    theme = canon_theme(str(theme_key or "").strip()) or str(theme_key or "").strip()
    if not theme:
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                   trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            FROM theme_reputation WHERE theme_key = ?
            """,
            (theme,),
        ).fetchone()
        trade = 0.0
        if cur is None:
            auto = 0.0
            fade_n = 0
            persist_n = 0
            last_fade = None
            last_persist = None
            old_manual = 0.0
            old_note = None
        else:
            row = dict(cur)
            trade = float(row.get("trade_adj") or 0)
            auto = float(row.get("auto_adj") or 0)
            if abs(auto) < 1e-9 and abs(float(row.get("score_adj") or 0)) > 1e-9:
                auto = (
                    float(row.get("score_adj") or 0)
                    - float(row.get("manual_adj") or 0)
                    - trade
                )
            fade_n = int(row.get("fade_n") or 0)
            persist_n = int(row.get("persist_n") or 0)
            last_fade = row.get("last_fade_date")
            last_persist = row.get("last_persist_date")
            old_manual = float(row.get("manual_adj") or 0)
            old_note = row.get("manual_note")
        if clear:
            manual = 0.0
            note_out = None
        elif manual_adj is not None:
            manual = float(manual_adj)
            note_out = note if note is not None else old_note
        elif delta is not None:
            manual = old_manual + float(delta)
            note_out = note if note is not None else old_note
        else:
            manual = old_manual
            note_out = note if note is not None else old_note
        manual = max(float(THEME_MANUAL_ADJ_MIN), min(float(THEME_MANUAL_ADJ_MAX), round(manual, 2)))
        combined = max(
            float(THEME_REP_ADJ_MIN),
            min(float(THEME_REP_ADJ_MAX), round(auto + manual + trade, 2)),
        )
        from market_desk.theme_memory import label_for_rep

        label = label_for_rep(fade_n, persist_n, combined)
        conn.execute(
            """
            INSERT INTO theme_reputation(
                theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(theme_key) DO UPDATE SET
                score_adj = excluded.score_adj,
                auto_adj = excluded.auto_adj,
                manual_adj = excluded.manual_adj,
                trade_adj = excluded.trade_adj,
                manual_note = excluded.manual_note,
                label = excluded.label,
                updated_at = excluded.updated_at
            """,
            (
                theme,
                fade_n,
                persist_n,
                combined,
                round(auto, 2),
                manual,
                round(trade, 2),
                note_out,
                last_fade,
                last_persist,
                label,
                now,
            ),
        )
        conn.commit()
        out = conn.execute(
            """
            SELECT theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                   trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            FROM theme_reputation WHERE theme_key = ?
            """,
            (theme,),
        ).fetchone()
    return dict(out) if out else None


def bump_theme_trade_adj(theme_key: str, delta: float) -> dict[str, Any] | None:
    """Apply a decaying trade-PnL nudge onto theme ``trade_adj``."""
    from market_desk.config import (
        THEME_REP_ADJ_MAX,
        THEME_REP_ADJ_MIN,
        THEME_TRADE_ADJ_MAX,
        THEME_TRADE_ADJ_MIN,
    )
    from market_desk.mainline import theme_key as canon_theme
    from market_desk.theme_memory import label_for_rep

    theme = canon_theme(str(theme_key or "").strip()) or str(theme_key or "").strip()
    if not theme or abs(float(delta)) < 1e-9:
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT fade_n, persist_n, auto_adj, manual_adj, trade_adj, score_adj
            FROM theme_reputation WHERE theme_key = ?
            """,
            (theme,),
        ).fetchone()
        if cur is None:
            auto = 0.0
            manual = 0.0
            trade = 0.0
            fade_n = 0
            persist_n = 0
        else:
            row = dict(cur)
            auto = float(row.get("auto_adj") or 0)
            manual = float(row.get("manual_adj") or 0)
            trade = float(row.get("trade_adj") or 0) * 0.92  # mild forget before bump
            fade_n = int(row.get("fade_n") or 0)
            persist_n = int(row.get("persist_n") or 0)
        trade = max(
            float(THEME_TRADE_ADJ_MIN),
            min(float(THEME_TRADE_ADJ_MAX), round(trade + float(delta), 2)),
        )
        combined = max(
            float(THEME_REP_ADJ_MIN),
            min(float(THEME_REP_ADJ_MAX), round(auto + manual + trade, 2)),
        )
        label = label_for_rep(fade_n, persist_n, combined)
        conn.execute(
            """
            INSERT INTO theme_reputation(
                theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                trade_adj, label, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(theme_key) DO UPDATE SET
                score_adj = excluded.score_adj,
                trade_adj = excluded.trade_adj,
                label = excluded.label,
                updated_at = excluded.updated_at
            """,
            (theme, fade_n, persist_n, combined, auto, manual, trade, label, now),
        )
        conn.commit()
        out = conn.execute(
            """
            SELECT theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                   trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            FROM theme_reputation WHERE theme_key = ?
            """,
            (theme,),
        ).fetchone()
    return dict(out) if out else None


def upsert_stock_reputation(
    *,
    code: str,
    name: str = "",
    win: bool = False,
    loss: bool = False,
    fake: bool = False,
    pnl_pct: float | None = None,
    trade_date: str | None = None,
) -> dict[str, Any] | None:
    """Update one ticker's trade reputation from a closed-lot outcome."""
    from market_desk.config import (
        ADAPT_STOCK_REP_ADJ_MAX,
        ADAPT_STOCK_REP_ADJ_MIN,
        ADAPT_STOCK_WATCH_STREAK,
    )
    from market_desk.filters import normalize_code

    c = normalize_code(code)
    if len(c) != 6:
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    day = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT code, name, win_n, loss_n, fake_n, score_adj, streak_loss,
                   last_pnl_pct, last_trade_date, label
            FROM stock_reputation WHERE code = ?
            """,
            (c,),
        ).fetchone()
        if cur is None:
            win_n = loss_n = fake_n = streak = 0
            adj = 0.0
            nm = name or c
        else:
            row = dict(cur)
            win_n = int(row.get("win_n") or 0)
            loss_n = int(row.get("loss_n") or 0)
            fake_n = int(row.get("fake_n") or 0)
            streak = int(row.get("streak_loss") or 0)
            adj = float(row.get("score_adj") or 0) * 0.94
            nm = name or str(row.get("name") or c)
        if win:
            win_n += 1
            streak = 0
            adj += 1.2
        if loss:
            loss_n += 1
            streak += 1
            adj -= 2.0
        if fake:
            fake_n += 1
            adj -= 1.5
        adj = max(float(ADAPT_STOCK_REP_ADJ_MIN), min(float(ADAPT_STOCK_REP_ADJ_MAX), round(adj, 2)))
        if streak >= int(ADAPT_STOCK_WATCH_STREAK):
            label = "观察名单"
        elif adj <= -6:
            label = "易骗线"
        elif adj <= -2:
            label = "偏坑"
        elif adj >= 3:
            label = "偏顺"
        else:
            label = "中性"
        conn.execute(
            """
            INSERT INTO stock_reputation(
                code, name, win_n, loss_n, fake_n, score_adj, streak_loss,
                last_pnl_pct, last_trade_date, label, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                win_n = excluded.win_n,
                loss_n = excluded.loss_n,
                fake_n = excluded.fake_n,
                score_adj = excluded.score_adj,
                streak_loss = excluded.streak_loss,
                last_pnl_pct = excluded.last_pnl_pct,
                last_trade_date = excluded.last_trade_date,
                label = excluded.label,
                updated_at = excluded.updated_at
            """,
            (
                c,
                nm,
                win_n,
                loss_n,
                fake_n,
                adj,
                streak,
                round(float(pnl_pct), 2) if pnl_pct is not None else None,
                day,
                label,
                now,
            ),
        )
        conn.commit()
        out = conn.execute(
            """
            SELECT code, name, win_n, loss_n, fake_n, score_adj, streak_loss,
                   last_pnl_pct, last_trade_date, label, updated_at
            FROM stock_reputation WHERE code = ?
            """,
            (c,),
        ).fetchone()
    return dict(out) if out else None


def load_stock_reputation_one(code: str) -> dict[str, Any] | None:
    """Load one stock reputation row by code."""
    from market_desk.filters import normalize_code

    c = normalize_code(code)
    if len(c) != 6:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT code, name, win_n, loss_n, fake_n, score_adj, streak_loss,
                   last_pnl_pct, last_trade_date, label, updated_at
            FROM stock_reputation WHERE code = ?
            """,
            (c,),
        ).fetchone()
    return dict(row) if row else None


def load_theme_reputation() -> list[dict[str, Any]]:
    """Return all theme reputation rows."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                   trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            FROM theme_reputation
            ORDER BY score_adj ASC, fade_n DESC
            """
        ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        trade = float(item.get("trade_adj") or 0)
        if item.get("auto_adj") is None:
            item["auto_adj"] = (
                float(item.get("score_adj") or 0)
                - float(item.get("manual_adj") or 0)
                - trade
            )
        if item.get("manual_adj") is None:
            item["manual_adj"] = 0.0
        if item.get("trade_adj") is None:
            item["trade_adj"] = 0.0
        out.append(item)
    return out


def record_theme_day_outcome(row: dict[str, Any]) -> None:
    """Insert or replace one prior→next theme outcome."""
    theme = str(row.get("theme_key") or "").strip()
    prior = str(row.get("trade_date") or "")[:10]
    nxt = str(row.get("next_date") or "")[:10]
    outcome = str(row.get("outcome") or "").strip()
    if not theme or not prior or not nxt or outcome not in ("fade", "persist"):
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO theme_day_outcome(
                trade_date, next_date, theme_key, outcome, mainline,
                zt_n, next_zt_n, pct, next_pct, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, next_date, theme_key) DO UPDATE SET
                outcome = excluded.outcome,
                mainline = excluded.mainline,
                zt_n = excluded.zt_n,
                next_zt_n = excluded.next_zt_n,
                pct = excluded.pct,
                next_pct = excluded.next_pct,
                updated_at = excluded.updated_at
            """,
            (
                prior,
                nxt,
                theme,
                outcome,
                row.get("mainline"),
                row.get("zt_n"),
                row.get("next_zt_n"),
                row.get("pct"),
                row.get("next_pct"),
                now,
            ),
        )
        conn.commit()


def load_theme_day_outcomes(trade_date: str) -> list[dict[str, Any]]:
    """Return theme outcomes recorded for a prior trade date."""
    day = str(trade_date or "")[:10]
    if not day:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, next_date, theme_key, outcome, mainline,
                   zt_n, next_zt_n, pct, next_pct, updated_at
            FROM theme_day_outcome
            WHERE trade_date = ?
            """,
            (day,),
        ).fetchall()
    return [dict(r) for r in rows]


def load_theme_outcomes_for_theme(theme_key: str, limit: int = 12) -> list[dict[str, Any]]:
    """Return newest-first outcomes for one theme."""
    theme = str(theme_key or "").strip()
    if not theme:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, next_date, theme_key, outcome, mainline,
                   zt_n, next_zt_n, pct, next_pct, updated_at
            FROM theme_day_outcome
            WHERE theme_key = ?
            ORDER BY next_date DESC
            LIMIT ?
            """,
            (theme, max(1, int(limit))),
        ).fetchall()
    return [dict(r) for r in rows]


_POS_SELECT = """
    id, user_id, code, name, buy_price, qty, note, created_at, last_buy_date,
    closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl,
    peak_price, entry_board
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
    item["entry_board"] = str(item.get("entry_board") or "").strip() or None
    try:
        item["user_id"] = int(item["user_id"]) if item.get("user_id") is not None else None
    except (TypeError, ValueError):
        item["user_id"] = None
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


def add_position(
    code: str,
    name: str,
    buy_price: float,
    qty: int,
    note: str = "",
    *,
    entry_board: str = "",
    user_id: int,
) -> dict[str, Any]:
    """Insert a position, or average into an existing open same-code row."""
    uid = int(user_id)
    code = str(code or "").zfill(6)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    buy_day = now[:10]
    board = str(entry_board or "").strip() or None
    with _connect() as conn:
        existing = conn.execute(
            f"""
            SELECT {_POS_SELECT}
            FROM positions
            WHERE code = ? AND user_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (code, uid),
        ).fetchone()
        if existing:
            old_qty = int(existing["qty"] or 0)
            old_px = float(existing["buy_price"] or 0)
            if old_qty <= 0:
                conn.execute(
                    """
                    UPDATE positions
                    SET name = ?, buy_price = ?, qty = ?, note = ?, last_buy_date = ?,
                        closed_date = NULL, created_at = ?, peak_price = ?, entry_board = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        name or existing["name"] or code,
                        float(buy_price),
                        int(qty),
                        note or existing["note"] or "",
                        buy_day,
                        now,
                        float(buy_price),
                        board or (str(existing["entry_board"] or "").strip() or None),
                        int(existing["id"]),
                        uid,
                    ),
                )
                conn.commit()
                return {
                    "id": int(existing["id"]),
                    "user_id": uid,
                    "code": code,
                    "name": name or existing["name"] or code,
                    "buy_price": float(buy_price),
                    "qty": int(qty),
                    "note": note or existing["note"] or "",
                    "created_at": now,
                    "last_buy_date": buy_day,
                    "entry_board": board or (str(existing["entry_board"] or "").strip() or None),
                    "reopened": True,
                }
            new_qty = old_qty + int(qty)
            if new_qty <= 0:
                conn.execute(
                    "DELETE FROM positions WHERE id = ? AND user_id = ?",
                    (int(existing["id"]), uid),
                )
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
                    closed_date = NULL, entry_board = COALESCE(?, entry_board)
                WHERE id = ? AND user_id = ?
                """,
                (
                    name or existing["name"] or code,
                    round(avg, 4),
                    new_qty,
                    merged_note,
                    buy_day,
                    board,
                    int(existing["id"]),
                    uid,
                ),
            )
            conn.commit()
            return {
                "id": int(existing["id"]),
                "user_id": uid,
                "code": code,
                "name": name or existing["name"] or code,
                "buy_price": round(avg, 4),
                "qty": new_qty,
                "note": merged_note,
                "created_at": existing["created_at"] or now,
                "last_buy_date": buy_day,
                "entry_board": board or (str(existing["entry_board"] or "").strip() or None),
                "averaged": True,
            }
        cur = conn.execute(
            """
            INSERT INTO positions(
                user_id, code, name, buy_price, qty, note, created_at, last_buy_date,
                closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl,
                peak_price, entry_board
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 0, ?, ?)
            """,
            (uid, code, name, buy_price, qty, note, now, buy_day, float(buy_price), board),
        )
        pid = int(cur.lastrowid)
        conn.commit()
    return {
        "id": pid,
        "user_id": uid,
        "code": code,
        "name": name,
        "buy_price": buy_price,
        "qty": qty,
        "note": note,
        "created_at": now,
        "last_buy_date": buy_day,
        "entry_board": board,
    }


def trim_position(
    pid: int,
    qty: int,
    *,
    sell_price: float | None = None,
    trade_date: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    """Reduce shares; keep qty=0 rows as closed-today until the next trade day."""
    sell = int(qty)
    if sell <= 0:
        return None
    day = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    with _connect() as conn:
        if user_id is not None:
            row = conn.execute(
                f"SELECT {_POS_SELECT} FROM positions WHERE id = ? AND user_id = ?",
                (pid, int(user_id)),
            ).fetchone()
        else:
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


def delete_position(pid: int, *, user_id: int | None = None) -> bool:
    """Delete a position by id. Return True if a row was removed."""
    with _connect() as conn:
        if user_id is not None:
            cur = conn.execute(
                "DELETE FROM positions WHERE id = ? AND user_id = ?",
                (pid, int(user_id)),
            )
        else:
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


def load_positions(*, user_id: int | None = None) -> list[dict[str, Any]]:
    """Return positions for one user (empty when user_id is None)."""
    if user_id is None:
        return []
    uid = int(user_id)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {_POS_SELECT}
            FROM positions
            WHERE user_id = ?
            ORDER BY
              CASE WHEN qty > 0 THEN 0 ELSE 1 END,
              id DESC
            """,
            (uid,),
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

        # Gate evolution: keep first_ready / fail history across same-day upserts.
        ready_now = 1 if row.get("ready") else 0
        signaled = str(row.get("signaled_at") or "")
        if ready_now:
            merged["ever_ready"] = True
            if not old_payload.get("first_ready_at"):
                merged["first_ready_at"] = signaled or old_payload.get("first_ready_at")
            else:
                merged["first_ready_at"] = old_payload.get("first_ready_at")
        else:
            if old_payload.get("ever_ready"):
                merged["ever_ready"] = True
            if old_payload.get("first_ready_at"):
                merged["first_ready_at"] = old_payload.get("first_ready_at")
        new_fails = [
            str(x).strip()
            for x in (incoming.get("confirm_fail") or [])
            if str(x).strip()
        ]
        hist = [
            str(x).strip()
            for x in (old_payload.get("confirm_fail_hist") or [])
            if str(x).strip()
        ]
        for flag in new_fails:
            if flag not in hist:
                hist.append(flag)
        if hist:
            merged["confirm_fail_hist"] = hist[:24]
        if new_fails:
            merged["final_fail"] = new_fails
        elif old_payload.get("final_fail") and not ready_now:
            merged["final_fail"] = old_payload.get("final_fail")
        elif old_payload.get("final_fail") and ready_now:
            # Recovered to ready: keep last gated fail for attribution.
            merged["final_fail"] = old_payload.get("final_fail")

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


def find_signal(
    trade_date: str,
    code: str,
    signal_type: str,
) -> dict[str, Any] | None:
    """Return the unique same-day signal for code + type, or None."""
    day = str(trade_date or "").strip()[:10]
    c = str(code or "").strip().zfill(6)
    typ = str(signal_type or "").strip().lower()
    if not day or len(c) != 6 or not c.isdigit() or typ not in ("buy", "sell"):
        return None
    with _connect() as conn:
        row = conn.execute(
            _SIGNAL_SELECT
            + """
            WHERE trade_date = ? AND code = ? AND signal_type = ?
            LIMIT 1
            """,
            (day, c, typ),
        ).fetchone()
    rows = _decode_signal_rows([row] if row else [])
    return rows[0] if rows else None


def sync_sell_fill_from_trim(
    *,
    code: str,
    trade_date: str | None,
    fill_price: float | None,
    fill_qty: int,
    note: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    """Mark today's sell signal traded and fill price/qty after a desk trim.

    No-op when there is no same-day sell signal yet. Returns the updated signal
    row summary, or None.
    """
    day = str(trade_date or "").strip()[:10]
    if not day:
        day = datetime.now().strftime("%Y-%m-%d")
    c = str(code or "").strip().zfill(6)
    qty = int(fill_qty or 0)
    if len(c) != 6 or not c.isdigit() or qty <= 0:
        return None
    row = find_signal(day, c, "sell")
    if not row:
        return None
    # Prefer this user's prior fill qty when accumulating partials.
    if user_id is not None:
        meta_map = load_signal_user_meta_map(int(user_id))
        um = meta_map.get(int(row["id"])) or {}
        prev_traded = int(um.get("traded") or 0)
        prev_qty = int(um.get("fill_qty") or 0) if prev_traded else 0
        prev_note = str(um.get("note") or "").strip()
    else:
        prev_traded = int(row.get("traded") or 0)
        prev_qty = int(row.get("fill_qty") or 0) if prev_traded else 0
        prev_note = str(row.get("note") or "").strip()
    px = float(fill_price) if fill_price is not None and float(fill_price) > 0 else None
    if px is None:
        try:
            px = float(row.get("price") or row.get("last") or 0) or None
        except (TypeError, ValueError):
            px = None
    note_to_set = None
    if not prev_note and note:
        note_to_set = note
    elif not prev_traded and note:
        note_to_set = note
    total_qty = prev_qty + qty if prev_qty > 0 else qty
    ok = update_signal_meta(
        int(row["id"]),
        traded=1,
        skipped=0,
        note=note_to_set,
        fill_price=px,
        fill_qty=total_qty,
        user_id=user_id,
    )
    if not ok:
        return None
    return {
        "id": int(row["id"]),
        "code": c,
        "trade_date": day,
        "fill_price": px,
        "fill_qty": total_qty,
    }


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
    user_id: int | None = None,
) -> bool:
    """Update per-user review flags / fill fields for one signal.

    When ``user_id`` is omitted, falls back to legacy columns on ``signals``
    (single-user / migration path only).
    """
    if user_id is not None:
        return upsert_signal_user_meta(
            int(user_id),
            int(signal_id),
            skipped=skipped,
            traded=traded,
            note=note,
            fill_price=fill_price,
            fill_qty=fill_qty,
        )
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, note, skipped, traded, fill_price, fill_qty FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        if not row:
            return False
        new_skipped = int(row["skipped"] or 0) if skipped is None else int(skipped)
        new_traded = int(row["traded"] or 0) if traded is None else int(traded)
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


def upsert_signal_user_meta(
    user_id: int,
    signal_id: int,
    *,
    skipped: int | None = None,
    traded: int | None = None,
    note: str | None = None,
    fill_price: float | None = None,
    fill_qty: int | None = None,
) -> bool:
    """Insert or patch one user's traded / fill annotation for a signal."""
    uid = int(user_id)
    sid = int(signal_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        exists = conn.execute(
            "SELECT id FROM signals WHERE id = ?", (sid,)
        ).fetchone()
        if not exists:
            return False
        row = conn.execute(
            """
            SELECT skipped, traded, note, fill_price, fill_qty
            FROM signal_user_meta
            WHERE user_id = ? AND signal_id = ?
            """,
            (uid, sid),
        ).fetchone()
        if row:
            new_skipped = int(row["skipped"] or 0) if skipped is None else int(skipped)
            new_traded = int(row["traded"] or 0) if traded is None else int(traded)
            new_note = row["note"] if note is None else note
            new_fill_px = row["fill_price"] if fill_price is None else float(fill_price)
            new_fill_qty = row["fill_qty"] if fill_qty is None else int(fill_qty)
        else:
            new_skipped = 0 if skipped is None else int(skipped)
            new_traded = 0 if traded is None else int(traded)
            new_note = note
            new_fill_px = None if fill_price is None else float(fill_price)
            new_fill_qty = None if fill_qty is None else int(fill_qty)
        if traded is not None and int(traded):
            new_skipped = 0
            new_traded = 1
        if skipped is not None and int(skipped):
            new_traded = 0
            new_skipped = 1
        conn.execute(
            """
            INSERT INTO signal_user_meta(
                user_id, signal_id, skipped, traded, note, fill_price, fill_qty, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, signal_id) DO UPDATE SET
                skipped = excluded.skipped,
                traded = excluded.traded,
                note = excluded.note,
                fill_price = excluded.fill_price,
                fill_qty = excluded.fill_qty,
                updated_at = excluded.updated_at
            """,
            (
                uid,
                sid,
                new_skipped,
                new_traded,
                new_note,
                new_fill_px,
                new_fill_qty,
                now,
            ),
        )
        conn.commit()
    return True


def load_signal_user_meta_map(user_id: int) -> dict[int, dict[str, Any]]:
    """Return ``signal_id -> meta`` for one user."""
    uid = int(user_id)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT signal_id, skipped, traded, note, fill_price, fill_qty, updated_at
            FROM signal_user_meta
            WHERE user_id = ?
            """,
            (uid,),
        ).fetchall()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        out[int(row["signal_id"])] = dict(row)
    return out


def apply_signal_user_meta(
    rows: list[dict[str, Any]] | None,
    user_id: int | None,
) -> list[dict[str, Any]]:
    """Overlay per-user traded / fill fields onto shared signal rows.

    Without ``user_id``, clear personal flags so anonymous clients never see
    another user's fills.
    """
    src = list(rows or [])
    if user_id is None:
        cleaned: list[dict[str, Any]] = []
        for raw in src:
            item = dict(raw)
            item["skipped"] = 0
            item["traded"] = 0
            item["note"] = None
            item["fill_price"] = None
            item["fill_qty"] = None
            cleaned.append(item)
        return cleaned
    meta = load_signal_user_meta_map(int(user_id))
    out: list[dict[str, Any]] = []
    for raw in src:
        item = dict(raw)
        # Start from blank personal fields; only show this user's annotations.
        item["skipped"] = 0
        item["traded"] = 0
        item["note"] = None
        item["fill_price"] = None
        item["fill_qty"] = None
        try:
            sid = int(item.get("id") or 0)
        except (TypeError, ValueError):
            sid = 0
        hit = meta.get(sid)
        if hit:
            item["skipped"] = int(hit.get("skipped") or 0)
            item["traded"] = int(hit.get("traded") or 0)
            item["note"] = hit.get("note")
            item["fill_price"] = hit.get("fill_price")
            item["fill_qty"] = hit.get("fill_qty")
        out.append(item)
    return out


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


def load_recent_mainline_switch_stats(days: int = 8) -> dict[str, Any]:
    """Aggregate mainline-switch churn over recent trade dates."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, COUNT(*) AS n
            FROM mainline_switch
            GROUP BY trade_date
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (max(1, int(days)),),
        ).fetchall()
    counts = [int(r["n"] or 0) for r in rows]
    n_days = len(counts)
    total = sum(counts)
    avg = (total / n_days) if n_days else 0.0
    return {
        "days": n_days,
        "total": total,
        "avg_per_day": round(avg, 2),
        "max_day": max(counts) if counts else 0,
    }


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


def export_user_backup_payload(user_id: int) -> dict[str, Any]:
    """Export one user's personal books / fills / private settings."""
    uid = int(user_id)
    with _connect() as conn:
        positions = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM positions WHERE user_id = ? ORDER BY id", (uid,)
            ).fetchall()
        ]
        watchlist = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM watchlist WHERE user_id = ? ORDER BY id", (uid,)
            ).fetchall()
        ]
        favorite_boards = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM favorite_boards WHERE user_id = ? ORDER BY id", (uid,)
            ).fetchall()
        ]
        signal_user_meta = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM signal_user_meta WHERE user_id = ? ORDER BY signal_id",
                (uid,),
            ).fetchall()
        ]
        # Personal knobs live in settings as key user:{id}:runtime
        setting_key = f"user:{uid}:runtime"
        user_settings_row = conn.execute(
            "SELECT key, value, updated_at FROM settings WHERE key = ?",
            (setting_key,),
        ).fetchone()
        user_settings = [dict(user_settings_row)] if user_settings_row else []
    return {
        "version": 2,
        "scope": "user",
        "user_id": uid,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "positions": positions,
        "watchlist": watchlist,
        "favorite_boards": favorite_boards,
        "signal_user_meta": signal_user_meta,
        "user_settings": user_settings,
    }


def import_user_backup_payload(
    user_id: int, payload: dict[str, Any], *, replace: bool = False
) -> dict[str, int]:
    """Import personal data into one user. Never touches other users' rows."""
    if not isinstance(payload, dict):
        raise ValueError("backup payload must be an object")
    uid = int(user_id)
    counts = {
        "positions": 0,
        "watchlist": 0,
        "favorite_boards": 0,
        "signal_user_meta": 0,
        "user_settings": 0,
    }
    with _connect() as conn:
        if replace:
            conn.execute("DELETE FROM positions WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM watchlist WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM favorite_boards WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM signal_user_meta WHERE user_id = ?", (uid,))
            conn.execute(
                "DELETE FROM settings WHERE key = ?", (f"user:{uid}:runtime",)
            )
        for row in payload.get("positions") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO positions(
                    user_id, code, name, buy_price, qty, note, created_at, last_buy_date,
                    closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl,
                    peak_price, entry_board
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
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
                    row.get("peak_price"),
                    row.get("entry_board"),
                ),
            )
            counts["positions"] += 1
        for row in payload.get("watchlist") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO watchlist(
                    user_id, code, name, note, suggest_price, stop_price, chase_price,
                    first_seen_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, code) DO UPDATE SET
                    name = excluded.name,
                    note = excluded.note,
                    stop_price = excluded.stop_price,
                    chase_price = excluded.chase_price
                """,
                (
                    uid,
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
                INSERT INTO favorite_boards(user_id, bk, name, kind, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, bk) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    note = excluded.note
                """,
                (
                    uid,
                    bk,
                    row.get("name") or "",
                    row.get("kind") or "",
                    row.get("note") or "",
                    row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["favorite_boards"] += 1
        for row in payload.get("signal_user_meta") or []:
            if not isinstance(row, dict):
                continue
            try:
                sid = int(row.get("signal_id") or 0)
            except (TypeError, ValueError):
                continue
            if sid <= 0:
                continue
            exists = conn.execute(
                "SELECT id FROM signals WHERE id = ?", (sid,)
            ).fetchone()
            if not exists:
                continue
            conn.execute(
                """
                INSERT INTO signal_user_meta(
                    user_id, signal_id, skipped, traded, note, fill_price, fill_qty, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, signal_id) DO UPDATE SET
                    skipped = excluded.skipped,
                    traded = excluded.traded,
                    note = excluded.note,
                    fill_price = excluded.fill_price,
                    fill_qty = excluded.fill_qty,
                    updated_at = excluded.updated_at
                """,
                (
                    uid,
                    sid,
                    int(row.get("skipped") or 0),
                    int(row.get("traded") or 0),
                    row.get("note"),
                    row.get("fill_price"),
                    row.get("fill_qty"),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["signal_user_meta"] += 1
        for row in payload.get("user_settings") or []:
            if not isinstance(row, dict):
                continue
            # Accept either the namespaced settings key or a bare runtime blob.
            key = str(row.get("key") or "").strip() or f"user:{uid}:runtime"
            if not key.startswith(f"user:{uid}:"):
                key = f"user:{uid}:runtime"
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    row.get("value")
                    if isinstance(row.get("value"), str)
                    else json.dumps(row.get("value"), ensure_ascii=False),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["user_settings"] += 1
        conn.commit()
    return counts


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
