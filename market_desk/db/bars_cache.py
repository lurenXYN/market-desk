"""Local daily OHLCV cache: record fetched bars and detect calendar gaps."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect, _table_cols
from market_desk.filters import normalize_code


def ensure_bars_cache_tables(conn: sqlite3.Connection) -> None:
    """Create ``market_bar_daily`` if missing (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_bar_daily (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            pct REAL,
            source TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (code, trade_date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_bar_daily_date "
        "ON market_bar_daily(trade_date, code)"
    )
    cols = _table_cols(conn, "market_bar_daily")
    if "pct" not in cols:
        conn.execute("ALTER TABLE market_bar_daily ADD COLUMN pct REAL")
    if "source" not in cols:
        conn.execute("ALTER TABLE market_bar_daily ADD COLUMN source TEXT")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def upsert_daily_bars(
    code: str,
    bars: list[dict[str, Any]],
    *,
    source: str = "fetch",
) -> int:
    """Upsert bar rows for one code. Return number of rows written."""
    c = normalize_code(code) or str(code or "").zfill(6)
    if not c or not bars:
        return 0
    stamp = _now()
    n = 0
    with _connect() as conn:
        ensure_bars_cache_tables(conn)
        for bar in bars:
            day = str(bar.get("date") or bar.get("trade_date") or "")[:10]
            if len(day) != 10:
                continue
            conn.execute(
                """
                INSERT INTO market_bar_daily(
                    code, trade_date, open, high, low, close, volume, pct, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, trade_date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    pct = excluded.pct,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (
                    c,
                    day,
                    bar.get("open"),
                    bar.get("high"),
                    bar.get("low"),
                    bar.get("close"),
                    bar.get("volume") if bar.get("volume") is not None else bar.get("vol"),
                    bar.get("pct"),
                    source,
                    stamp,
                ),
            )
            n += 1
        conn.commit()
    return n


def load_daily_bars(
    code: str,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int = 120,
) -> list[dict[str, Any]]:
    """Load cached bars oldest→newest."""
    c = normalize_code(code) or str(code or "").zfill(6)
    lim = max(1, min(int(limit or 120), 2000))
    with _connect() as conn:
        ensure_bars_cache_tables(conn)
        clauses = ["code = ?"]
        args: list[Any] = [c]
        if start:
            clauses.append("trade_date >= ?")
            args.append(str(start)[:10])
        if end:
            clauses.append("trade_date <= ?")
            args.append(str(end)[:10])
        sql = (
            f"SELECT * FROM market_bar_daily WHERE {' AND '.join(clauses)} "
            f"ORDER BY trade_date ASC LIMIT ?"
        )
        args.append(lim)
        rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "code": r["code"],
                "date": r["trade_date"],
                "trade_date": r["trade_date"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
                "pct": r["pct"],
                "source": r["source"],
            }
        )
    return out


def find_bar_gaps(
    code: str,
    trading_days: list[str],
) -> dict[str, Any]:
    """Return missing trade dates for ``code`` within ``trading_days``."""
    c = normalize_code(code) or str(code or "").zfill(6)
    want = [str(d)[:10] for d in trading_days if str(d)[:10]]
    if not c or not want:
        return {"code": c, "missing": [], "have_n": 0, "want_n": 0}
    with _connect() as conn:
        ensure_bars_cache_tables(conn)
        rows = conn.execute(
            f"""
            SELECT trade_date FROM market_bar_daily
            WHERE code = ? AND trade_date IN ({",".join("?" for _ in want)})
            """,
            (c, *want),
        ).fetchall()
    have = {str(r["trade_date"])[:10] for r in rows}
    missing = [d for d in want if d not in have]
    return {
        "code": c,
        "want_n": len(want),
        "have_n": len(have),
        "missing_n": len(missing),
        "missing": missing[:60],
    }


def bars_cache_stats() -> dict[str, Any]:
    """Ops summary of cached bar coverage."""
    with _connect() as conn:
        ensure_bars_cache_tables(conn)
        row = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   COUNT(DISTINCT code) AS codes,
                   MIN(trade_date) AS min_d,
                   MAX(trade_date) AS max_d
            FROM market_bar_daily
            """
        ).fetchone()
    return {
        "rows": int(row["n"] or 0) if row else 0,
        "codes": int(row["codes"] or 0) if row else 0,
        "min_date": (row["min_d"] if row else None),
        "max_date": (row["max_d"] if row else None),
    }
