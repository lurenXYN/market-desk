"""Connection factory and small schema helpers shared by every store."""

from __future__ import annotations

import sqlite3


class _ClosingConnection(sqlite3.Connection):
    """Connection that also closes on ``with`` exit (sqlite3 only commits)."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def _connect() -> sqlite3.Connection:
    """Open a short-lived SQLite connection with WAL and a busy timeout.

    The connection is closed when the ``with`` block exits: sqlite3's own
    context manager only commits, so relying on refcounting leaked handles and
    left WAL read transactions open in the long-running server.
    """
    # Read the path off the package each call so patching
    # ``market_desk.db.DB_PATH`` / ``DATA_DIR`` redirects every store.
    import market_desk.db as pkg

    pkg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(pkg.DB_PATH, timeout=30.0, factory=_ClosingConnection)
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
