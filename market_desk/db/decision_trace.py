"""Decision trace: 证据快照 → 动作 → 结果，一条可复核的链路。

The desk already stores signals and outcomes, but not *why* a card was lit: the
evidence (phase, mainline, gates, portfolio stance) lives only in the moment.
This table keeps one row per decision with a compact evidence hash + payload so
counterfactual audits and hindsight attribution can be generated instead of
re-told by hand.

Schema is additive: existing databases gain the table on next ``init_db``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


def ensure_decision_trace_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_trace (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            code TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            side TEXT NOT NULL DEFAULT 'buy',
            action TEXT NOT NULL DEFAULT '',
            phase TEXT,
            mainline TEXT,
            evidence_hash TEXT NOT NULL DEFAULT '',
            evidence TEXT,
            outcome_label TEXT,
            outcome_day1_pct REAL,
            outcome_day3_pct REAL,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_trace_day ON decision_trace(trade_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_trace_code ON decision_trace(code, trade_date)"
    )


def _hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def save_decision(
    *,
    trade_date: str,
    code: str,
    name: str = "",
    side: str = "buy",
    action: str = "",
    evidence: dict[str, Any] | None = None,
    phase: str | None = None,
    mainline: str | None = None,
) -> int:
    """Insert one decision row; returns its id."""
    day = str(trade_date or "")[:10]
    if not day or not code:
        return 0
    ev = evidence if isinstance(evidence, dict) else {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    digest = _hash(ev)
    with _connect() as conn:
        ensure_decision_trace_tables(conn)
        dup = conn.execute(
            "SELECT id FROM decision_trace WHERE trade_date=? AND code=? AND side=? "
            "AND action=? AND evidence_hash=? LIMIT 1",
            (day, str(code), str(side or "buy"), str(action or ""), digest),
        ).fetchone()
        if dup:
            return 0
        cur = conn.execute(
            """
            INSERT INTO decision_trace(
                trade_date, created_at, code, name, side, action, phase, mainline,
                evidence_hash, evidence
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                day,
                now,
                str(code),
                str(name or ""),
                str(side or "buy"),
                str(action or ""),
                phase,
                mainline,
                digest,
                json.dumps(ev, ensure_ascii=False)[:4000],
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def attach_decision_outcome(
    *,
    trade_date: str,
    code: str,
    label: str | None,
    day1_pct: Any = None,
    day3_pct: Any = None,
) -> int:
    """Fill the outcome columns of that day's decision rows; returns rows touched."""
    day = str(trade_date or "")[:10]
    if not day or not code:
        return 0
    with _connect() as conn:
        ensure_decision_trace_tables(conn)
        cur = conn.execute(
            """
            UPDATE decision_trace
               SET outcome_label = ?, outcome_day1_pct = ?, outcome_day3_pct = ?,
                   updated_at = ?
             WHERE trade_date = ? AND code = ? AND outcome_label IS NULL
            """,
            (
                label,
                day1_pct,
                day3_pct,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                day,
                str(code),
            ),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def sync_outcomes_from_signals(*, limit: int = 400) -> int:
    """Backfill decision outcomes from scored signals (day+code join)."""
    from market_desk.db import load_signals

    touched = 0
    try:
        rows = load_signals(limit=limit)
    except Exception:
        return 0
    for row in rows or []:
        day = str(row.get("trade_date") or "")[:10]
        code = str(row.get("code") or "")
        if not day or not code or not row.get("outcome_label"):
            continue
        touched += attach_decision_outcome(
            trade_date=day,
            code=code,
            label=str(row.get("outcome_label") or ""),
            day1_pct=row.get("outcome_day1_pct"),
            day3_pct=row.get("outcome_day3_pct"),
        )
    return touched


def load_decisions(*, trade_date: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Most recent decision rows (optionally one day), newest first."""
    with _connect() as conn:
        ensure_decision_trace_tables(conn)
        if trade_date:
            rows = conn.execute(
                "SELECT * FROM decision_trace WHERE trade_date = ? ORDER BY id DESC LIMIT ?",
                (str(trade_date)[:10], int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM decision_trace ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.get("evidence")
        if raw:
            try:
                item["evidence"] = json.loads(raw)
            except json.JSONDecodeError:
                pass
        out.append(item)
    return out


def trace_summary(*, trade_date: str | None = None) -> dict[str, Any]:
    """Counts by action + outcome coverage for the ops panel."""
    rows = load_decisions(trade_date=trade_date, limit=1000)
    by_action: dict[str, int] = {}
    scored = 0
    for row in rows:
        action = str(row.get("action") or "—")
        by_action[action] = by_action.get(action, 0) + 1
        if row.get("outcome_label"):
            scored += 1
    return {
        "ok": True,
        "trade_date": trade_date,
        "n": len(rows),
        "scored_n": scored,
        "by_action": by_action,
        "note": "证据→动作→结果；outcome 为空表示尚未到结算时点",
    }