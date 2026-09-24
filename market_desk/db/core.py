"""Connection factory and small schema helpers shared by every store."""

from __future__ import annotations

import sqlite3


def _connect() -> sqlite3.Connection:
    """Open a short-lived SQLite connection with WAL and a busy timeout."""
    # Read the path off the package each call so patching
    # ``market_desk.db.DB_PATH`` / ``DATA_DIR`` redirects every store.
    import market_desk.db as pkg

    pkg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(pkg.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass
    return conn


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return column names for a SQLite table."""
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
