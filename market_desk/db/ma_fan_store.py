"""Persistence for the nightly MA-fan scan payloads."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect
from market_desk.db.schema import _ensure_ma_fan_tables


def save_ma_fan_day(trade_date: str, payload: dict[str, Any]) -> None:
    """Upsert one night's MA-fan scan payload."""
    day = str(trade_date or "")[:10]
    if not day:
        return
    body = dict(payload or {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body.setdefault("saved_at", now)
    with _connect() as conn:
        _ensure_ma_fan_tables(conn)
        conn.execute(
            """
            INSERT INTO ma_fan_day(trade_date, payload, formula_version, hit_n, scanned, saved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                payload = excluded.payload,
                formula_version = excluded.formula_version,
                hit_n = excluded.hit_n,
                scanned = excluded.scanned,
                saved_at = excluded.saved_at
            """,
            (
                day,
                json.dumps(body, ensure_ascii=False),
                int(body.get("formula_version") or 1),
                int(body.get("hit_n") or len(body.get("items") or [])),
                int(body.get("scanned") or 0),
                str(body.get("saved_at") or now),
            ),
        )
        conn.commit()


def load_ma_fan_day(trade_date: str) -> dict[str, Any] | None:
    """Return the stored MA-fan payload for one trade date, or None."""
    day = str(trade_date or "")[:10]
    if not day:
        return None
    with _connect() as conn:
        _ensure_ma_fan_tables(conn)
        row = conn.execute(
            "SELECT payload FROM ma_fan_day WHERE trade_date = ?",
            (day,),
        ).fetchone()
    if not row or not row["payload"]:
        return None
    try:
        data = json.loads(row["payload"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        data.setdefault("trade_date", day)
        return data
    return None


def list_ma_fan_dates(limit: int = 40) -> list[str]:
    """Return recent MA-fan scan dates, newest first."""
    lim = max(1, min(int(limit or 40), 120))
    with _connect() as conn:
        _ensure_ma_fan_tables(conn)
        rows = conn.execute(
            """
            SELECT trade_date FROM ma_fan_day
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    return [str(r["trade_date"])[:10] for r in rows if r["trade_date"]]
