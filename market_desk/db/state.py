"""Global settings, session segments, mainline switches and trend overrides."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


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
