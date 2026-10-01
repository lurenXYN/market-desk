"""East Money availability history: hourly request counters and block transitions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from market_desk.db.core import _connect


def save_em_avail(rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> int:
    """Add hourly counter deltas and append block / recover transitions.

    Args:
        rows: ``{"day", "hour", "family", "ok", "fail", "empty", "last_err"}`` deltas.
        events: ``{"at", "family", "state", "err"}`` transitions.

    Returns:
        Number of hourly rows written.
    """
    if not rows and not events:
        return 0
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO em_avail_hourly(day, hour, family, ok, fail, empty, last_err)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day, hour, family) DO UPDATE SET
                ok = ok + excluded.ok,
                fail = fail + excluded.fail,
                empty = empty + excluded.empty,
                last_err = CASE WHEN excluded.last_err != '' THEN excluded.last_err ELSE last_err END
            """,
            [
                (
                    str(r["day"]), int(r["hour"]), str(r["family"]), int(r.get("ok") or 0),
                    int(r.get("fail") or 0), int(r.get("empty") or 0), str(r.get("last_err") or ""),
                )
                for r in rows
            ],
        )
        conn.executemany(
            "INSERT INTO em_avail_event(at, family, state, err) VALUES (?, ?, ?, ?)",
            [
                (str(e["at"]), str(e["family"]), str(e["state"]), str(e.get("err") or ""))
                for e in events
            ],
        )
        conn.commit()
    return len(rows)


def load_em_avail(days: int = 7) -> dict[str, list[dict[str, Any]]]:
    """Return hourly rows and transitions of the last ``days`` calendar days."""
    since = (datetime.now() - timedelta(days=max(1, int(days)) - 1)).strftime("%Y-%m-%d")
    with _connect() as conn:
        hourly = conn.execute(
            """
            SELECT day, hour, family, ok, fail, empty, last_err FROM em_avail_hourly
            WHERE day >= ? ORDER BY day, hour, family
            """,
            (since,),
        ).fetchall()
        events = conn.execute(
            "SELECT at, family, state, err FROM em_avail_event WHERE at >= ? ORDER BY at",
            (since,),
        ).fetchall()
    hk = ("day", "hour", "family", "ok", "fail", "empty", "last_err")
    ek = ("at", "family", "state", "err")
    return {
        "hourly": [dict(zip(hk, row)) for row in hourly],
        "events": [dict(zip(ek, row)) for row in events],
    }


def prune_em_avail(keep_days: int = 30) -> int:
    """Delete availability history older than ``keep_days``; return rows removed."""
    cutoff = (datetime.now() - timedelta(days=max(1, int(keep_days)))).strftime("%Y-%m-%d")
    with _connect() as conn:
        n = conn.execute("DELETE FROM em_avail_hourly WHERE day < ?", (cutoff,)).rowcount
        n += conn.execute("DELETE FROM em_avail_event WHERE at < ?", (cutoff,)).rowcount
        conn.commit()
    return int(n or 0)


def summarize_em_avail(hourly: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold hourly rows into hour-of-day buckets per family across all days.

    Answers "does a family fail at particular clock hours?" without the day axis.
    """
    acc: dict[tuple[str, int], dict[str, Any]] = {}
    for r in hourly:
        key = (str(r["family"]), int(r["hour"]))
        cell = acc.setdefault(key, {"family": key[0], "hour": key[1], "ok": 0, "fail": 0, "empty": 0, "days": set()})
        cell["ok"] += int(r.get("ok") or 0)
        cell["fail"] += int(r.get("fail") or 0)
        cell["empty"] += int(r.get("empty") or 0)
        cell["days"].add(str(r["day"]))
    out: list[dict[str, Any]] = []
    for key in sorted(acc):
        cell = acc[key]
        total = cell["ok"] + cell["fail"]
        out.append(
            {
                "family": cell["family"],
                "hour": cell["hour"],
                "ok": cell["ok"],
                "fail": cell["fail"],
                "empty": cell["empty"],
                "days": len(cell["days"]),
                "fail_rate": round(cell["fail"] / total, 3) if total else None,
            }
        )
    return out
