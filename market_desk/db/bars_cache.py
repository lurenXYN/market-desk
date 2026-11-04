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
    try:
        from market_desk.calendar import now_cn

        return now_cn().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _bar_cache_code(code: str) -> str:
    """Normalize stock codes; keep ``IDX######`` bench keys as-is."""
    raw = str(code or "").strip()
    if raw.upper().startswith("IDX") and len(raw) >= 9:
        return raw.upper()
    return normalize_code(code) or raw.zfill(6)


def adj_family(source: str | None) -> str:
    """Map a write ``source`` to an adjustment family for overwrite policy.

    ``qfq`` = forward-adjusted (Tencent qfq / East Money fqt=1 / bench).
    ``raw`` = unadjusted (Sina / explicit none). Unknown sources stay isolated
    so they only fill nulls and never overwrite a known family.
    """
    s = str(source or "").strip().lower()
    if s in {
        "chip_volume",
        "fetch_daily_bars",
        "bench_eod",
        "bench_http",
        "tencent",
        "tencent_qfq",
        "eastmoney",
        "em_qfq",
        "qfq",
        "fetch",
    }:
        return "qfq"
    if s in {"sina", "sina_raw", "raw", "none", "fqt0"}:
        return "raw"
    if s.startswith("bench"):
        return "qfq"
    return f"other:{s or 'unknown'}"


def _skip_incomplete_bar(day: str) -> bool:
    """Return True for future days, or today before the A-share close stamp.

    Mid-session last≠close bars must not poison NP / adapt remaps; after 15:05
    CN the day bar is treated as closed enough to cache.
    """
    try:
        from market_desk.calendar import now_cn, today_cn

        today = today_cn().isoformat()
    except Exception:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if day > today:
            return True
        if day < today:
            return False
        return now.strftime("%H:%M") < "15:05"
    if day > today:
        return True
    if day < today:
        return False
    return now_cn().strftime("%H:%M") < "15:05"


def upsert_daily_bars(
    code: str,
    bars: list[dict[str, Any]],
    *,
    source: str = "fetch",
    skip_today: bool = True,
) -> int:
    """Upsert bar rows for one code. Return number of rows written.

    Null OHLC / pct / volume never wipe an existing non-null cell (COALESCE).
    Cross-family sources (qfq vs raw) only fill nulls — they never replace an
    already-populated OHLC close from another adjustment family.
    By default skips incomplete today (pre-15:05 CN) and future trade dates.
    """
    c = _bar_cache_code(code)
    if not c or not bars:
        return 0
    stamp = _now()
    new_fam = adj_family(source)
    n = 0
    with _connect() as conn:
        ensure_bars_cache_tables(conn)
        for bar in bars:
            day = str(bar.get("date") or bar.get("trade_date") or "")[:10]
            if len(day) != 10:
                continue
            if skip_today and _skip_incomplete_bar(day):
                continue
            vol = bar.get("volume") if bar.get("volume") is not None else bar.get("vol")
            existing = conn.execute(
                "SELECT source, close FROM market_bar_daily WHERE code=? AND trade_date=?",
                (c, day),
            ).fetchone()
            same_family = True
            if existing is not None:
                old_src = existing["source"] if isinstance(existing, sqlite3.Row) else existing[0]
                old_close = existing["close"] if isinstance(existing, sqlite3.Row) else existing[1]
                old_fam = adj_family(old_src)
                # Different family with an existing close → null-fill only.
                if old_fam != new_fam and old_close is not None:
                    same_family = False
                    try:
                        import logging

                        logging.getLogger(__name__).warning(
                            "bars cross-family skip overwrite %s %s old=%s(%s) new=%s(%s)",
                            c,
                            day,
                            old_src,
                            old_fam,
                            source,
                            new_fam,
                        )
                    except Exception:
                        pass
            if same_family:
                sql = """
                INSERT INTO market_bar_daily(
                    code, trade_date, open, high, low, close, volume, pct, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, trade_date) DO UPDATE SET
                    open = COALESCE(excluded.open, market_bar_daily.open),
                    high = COALESCE(excluded.high, market_bar_daily.high),
                    low = COALESCE(excluded.low, market_bar_daily.low),
                    close = COALESCE(excluded.close, market_bar_daily.close),
                    volume = COALESCE(excluded.volume, market_bar_daily.volume),
                    pct = COALESCE(excluded.pct, market_bar_daily.pct),
                    source = COALESCE(excluded.source, market_bar_daily.source),
                    updated_at = excluded.updated_at
                """
            else:
                # Cross-family: keep existing OHLC; only fill holes + keep old source.
                sql = """
                INSERT INTO market_bar_daily(
                    code, trade_date, open, high, low, close, volume, pct, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, trade_date) DO UPDATE SET
                    open = COALESCE(market_bar_daily.open, excluded.open),
                    high = COALESCE(market_bar_daily.high, excluded.high),
                    low = COALESCE(market_bar_daily.low, excluded.low),
                    close = COALESCE(market_bar_daily.close, excluded.close),
                    volume = COALESCE(market_bar_daily.volume, excluded.volume),
                    pct = COALESCE(market_bar_daily.pct, excluded.pct),
                    updated_at = excluded.updated_at
                """
            conn.execute(
                sql,
                (
                    c,
                    day,
                    bar.get("open"),
                    bar.get("high"),
                    bar.get("low"),
                    bar.get("close"),
                    vol,
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
    """Load cached bars oldest→newest (most recent ``limit`` rows when capped)."""
    c = _bar_cache_code(code)
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
        where = " AND ".join(clauses)
        # Newest N first, then re-order ASC for callers that walk chronologically.
        sql = (
            f"SELECT * FROM ("
            f"  SELECT * FROM market_bar_daily WHERE {where} "
            f"  ORDER BY trade_date DESC LIMIT ?"
            f") ORDER BY trade_date ASC"
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
