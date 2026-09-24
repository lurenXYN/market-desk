"""Schema creation and in-place migrations (``init_db``)."""

from __future__ import annotations

import sqlite3

from market_desk.db.core import _connect, _table_cols
from market_desk.db.positions import _backfill_position_lots


def _migrate_signals_owner_user(conn: sqlite3.Connection) -> None:
    """Add ``owner_user_id`` and rebuild UNIQUE so sells can be per-account.

    Buys stay at ``owner_user_id=0`` (shared paper). Sells use the real user id.
    Preserves row ids so ``signal_user_meta`` FKs stay valid.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)").fetchall()}
    if "owner_user_id" not in cols:
        conn.execute(
            "ALTER TABLE signals ADD COLUMN owner_user_id INTEGER NOT NULL DEFAULT 0"
        )
    # Already on the 4-key unique? Check table DDL / indexes.
    create_sql = ""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='signals'"
    ).fetchone()
    if row and row[0]:
        create_sql = str(row[0])
    if "owner_user_id" in create_sql and "UNIQUE(trade_date, code, signal_type, owner_user_id)" in create_sql.replace(" ", ""):
        return
    # Also accept spaced form.
    compact = create_sql.replace(" ", "").replace("\n", "")
    if "UNIQUE(trade_date,code,signal_type,owner_user_id)" in compact:
        return
    # Rebuild table to replace 3-col UNIQUE with 4-col UNIQUE.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals_owner_mig (
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
            owner_user_id INTEGER NOT NULL DEFAULT 0,
            UNIQUE(trade_date, code, signal_type, owner_user_id)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO signals_owner_mig(
            id, trade_date, signaled_at, signal_type, action, phase, mainline,
            code, name, kind, price, last, ready, payload,
            outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
            outcome_label, outcome_checked_at, note, skipped, traded,
            fill_price, fill_qty, owner_user_id
        )
        SELECT
            id, trade_date, signaled_at, signal_type, action, phase, mainline,
            code, name, kind, price, last, ready, payload,
            outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
            outcome_label, outcome_checked_at, note, skipped, traded,
            fill_price, fill_qty, COALESCE(owner_user_id, 0)
        FROM signals
        """
    )
    conn.execute("DROP TABLE signals")
    conn.execute("ALTER TABLE signals_owner_mig RENAME TO signals")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_signals_trade_date ON signals(trade_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_signals_owner ON signals(owner_user_id, trade_date)"
    )
    try:
        mx = conn.execute("SELECT MAX(id) FROM signals").fetchone()[0]
        if mx:
            conn.execute(
                "DELETE FROM sqlite_sequence WHERE name = ?",
                ("signals",),
            )
            conn.execute(
                "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                ("signals", int(mx)),
            )
    except sqlite3.Error:
        pass


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
            ("day_sell_notional", "REAL DEFAULT 0"),
            ("peak_price", "REAL"),
            ("entry_board", "TEXT"),
        ):
            if col not in pos_cols:
                conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {decl}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                position_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                buy_price REAL NOT NULL,
                qty INTEGER NOT NULL,
                buy_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                note TEXT,
                FOREIGN KEY(position_id) REFERENCES positions(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_position_lots_pos "
            "ON position_lots(position_id, id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exec_diary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                trade_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                side TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                qty INTEGER,
                price REAL,
                advice_json TEXT,
                note TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exec_diary_user_day "
            "ON exec_diary(user_id, trade_date, id DESC)"
        )
        # Backfill one synthetic lot for open positions that have none yet.
        _backfill_position_lots(conn)
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
                owner_user_id INTEGER NOT NULL DEFAULT 0,
                UNIQUE(trade_date, code, signal_type, owner_user_id)
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
        _migrate_signals_owner_user(conn)
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
        _ensure_backtest_tables(conn)
        _ensure_ma_fan_tables(conn)
        conn.commit()


def _ensure_ma_fan_tables(conn: sqlite3.Connection) -> None:
    """Create nightly MA-fan scan day table (payload JSON, one row per trade date)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ma_fan_day (
            trade_date TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            formula_version INTEGER NOT NULL DEFAULT 1,
            hit_n INTEGER NOT NULL DEFAULT 0,
            scanned INTEGER NOT NULL DEFAULT 0,
            saved_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ma_fan_day_saved ON ma_fan_day(saved_at DESC)"
    )


def _ensure_backtest_tables(conn: sqlite3.Connection) -> None:
    """Create dedicated backtest run/fill tables (never stack onto signals.payload)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_backtest_run (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            created_by INTEGER NOT NULL DEFAULT 0,
            label TEXT NOT NULL DEFAULT '',
            date_from TEXT NOT NULL,
            date_to TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'plan',
            ready_only INTEGER NOT NULL DEFAULT 0,
            include_sells INTEGER NOT NULL DEFAULT 1,
            limit_n INTEGER NOT NULL DEFAULT 120,
            vol_min_ratio REAL,
            slip_pct REAL,
            gap_pct REAL,
            params_json TEXT NOT NULL DEFAULT '{}',
            summary_json TEXT NOT NULL DEFAULT '{}',
            note TEXT,
            item_n INTEGER NOT NULL DEFAULT 0,
            buy_hit_rate REAL,
            sell_hit_rate REAL,
            buy_fill_rate REAL,
            sim_exec_score REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_backtest_fill (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            signal_id INTEGER,
            side TEXT NOT NULL,
            code TEXT,
            name TEXT,
            kind TEXT,
            trade_date TEXT,
            signal_type TEXT,
            desk_source TEXT,
            plan_price REAL,
            wait_price REAL,
            chase_price REAL,
            stop_price REAL,
            sim_filled INTEGER NOT NULL DEFAULT 0,
            sim_fill_price REAL,
            sim_fill_date TEXT,
            sim_mode TEXT,
            sim_exit_mode TEXT,
            sim_exec TEXT,
            note TEXT,
            liq_skips INTEGER NOT NULL DEFAULT 0,
            gap_filled INTEGER NOT NULL DEFAULT 0,
            outcome_label TEXT,
            outcome_day1_pct REAL,
            outcome_day3_pct REAL,
            payload_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bt_fill_run ON signal_backtest_fill(run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bt_run_created ON signal_backtest_run(created_at DESC)"
    )


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
    user_cols = _table_cols(conn, "users")
    added_allowed = "serverchan_allowed" not in user_cols
    for col, decl in (
        ("serverchan_sendkey", "TEXT"),
        ("serverchan_on", "INTEGER NOT NULL DEFAULT 0"),
        ("serverchan_allowed", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if col not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
    # One-time grant when the column is introduced; init_db runs often, and a
    # recurring grant would undo an admin revoking their own push permission.
    if added_allowed:
        conn.execute(
            """
            UPDATE users
            SET serverchan_allowed = 1
            WHERE role = 'admin'
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
