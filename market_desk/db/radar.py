"""Stage-3 radar storage: board crowding history, ETF pulse log, narrative shadow."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


def save_board_crowding(
    trade_date: str,
    rows: list[dict[str, Any]],
    market_turnover: float,
) -> int:
    """Upsert today's board turnover shares (latest value wins).

    Args:
        trade_date: ``YYYY-MM-DD`` session date.
        rows: Dicts with ``name`` / ``kind`` / ``share`` (%) / ``turnover`` (元).
        market_turnover: Whole-market turnover in 元 used as the denominator.

    Returns:
        Number of rows written.
    """
    day = str(trade_date or "")[:10]
    data = [
        (
            day,
            str(r.get("name") or ""),
            str(r.get("kind") or ""),
            r.get("share"),
            r.get("turnover"),
            float(market_turnover or 0.0),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        for r in rows or []
        if r.get("name") and r.get("share") is not None
    ]
    if not day or not data:
        return 0
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO board_crowding(
                trade_date, name, kind, share, turnover, market_turnover, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, name) DO UPDATE SET
                kind = excluded.kind,
                share = excluded.share,
                turnover = excluded.turnover,
                market_turnover = excluded.market_turnover,
                updated_at = excluded.updated_at
            """,
            data,
        )
        conn.commit()
    return len(data)


def load_board_crowding_history(before: str, days: int = 60) -> dict[str, list[float]]:
    """Return per-board daily shares for the ``days`` sessions before ``before``.

    Args:
        before: ``YYYY-MM-DD``; that day itself is excluded.
        days: Number of distinct prior session dates to include.

    Returns:
        ``{name: [share, ...]}`` ordered oldest → newest.
    """
    day = str(before or "")[:10]
    with _connect() as conn:
        dates = [
            r["trade_date"]
            for r in conn.execute(
                "SELECT DISTINCT trade_date FROM board_crowding WHERE trade_date < ? "
                "ORDER BY trade_date DESC LIMIT ?",
                (day, int(days)),
            ).fetchall()
        ]
        if not dates:
            return {}
        marks = ",".join("?" for _ in dates)
        rows = conn.execute(
            f"SELECT trade_date, name, share FROM board_crowding WHERE trade_date IN ({marks}) "
            "ORDER BY trade_date ASC",
            dates,
        ).fetchall()
    out: dict[str, list[float]] = {}
    for r in rows:
        if r["share"] is None:
            continue
        out.setdefault(str(r["name"]), []).append(float(r["share"]))
    return out


def save_etf_pulse(trade_date: str, rows: list[dict[str, Any]]) -> int:
    """Insert ETF pulse hits (idempotent per date / minute / code)."""
    day = str(trade_date or "")[:10]
    data = [
        (
            day,
            str(r.get("at") or ""),
            str(r.get("code") or ""),
            str(r.get("kind") or ""),
            r.get("ratio"),
            r.get("px_move"),
            json.dumps(r, ensure_ascii=False),
        )
        for r in rows or []
        if r.get("at") and r.get("code")
    ]
    if not day or not data:
        return 0
    with _connect() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO etf_pulse_log(
                trade_date, at, code, kind, ratio, px_move, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            data,
        )
        conn.commit()
    return len(data)


def load_etf_pulse(trade_date: str) -> list[dict[str, Any]]:
    """Return one day's logged ETF pulse hits ordered by time."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT payload FROM etf_pulse_log WHERE trade_date = ? ORDER BY at ASC",
            (str(trade_date or "")[:10],),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            out.append(json.loads(r["payload"]))
        except (TypeError, ValueError):
            continue
    return out


def upsert_narrative_shadow(
    trade_date: str,
    clusters: list[dict[str, Any]],
    *,
    at: str,
    mainline: str,
) -> int:
    """Record narrative-cluster sightings: keep first sighting, refresh peak / last.

    ``became_mainline_at`` is stamped the first time the live mainline name
    falls inside the cluster's concept set, which yields the lead time.

    Args:
        trade_date: ``YYYY-MM-DD`` session date.
        clusters: Candidate clusters with ``label`` / ``zt_n`` / ``industries`` / ``concepts``.
        at: ``HH:MM:SS`` sighting clock.
        mainline: Current live mainline name.

    Returns:
        Number of clusters written.
    """
    day = str(trade_date or "")[:10]
    if not day or not clusters:
        return 0
    n = 0
    with _connect() as conn:
        for c in clusters:
            label = str(c.get("label") or "")
            if not label:
                continue
            concepts = [str(x) for x in (c.get("concepts") or [])]
            is_main = bool(mainline) and (mainline == label or mainline in concepts)
            row = conn.execute(
                "SELECT peak_zt, became_mainline_at FROM narrative_shadow "
                "WHERE trade_date = ? AND label = ?",
                (day, label),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO narrative_shadow(
                        trade_date, label, first_seen, last_seen, peak_zt, industries,
                        concepts, mainline_at_first, became_mainline_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        day,
                        label,
                        at,
                        at,
                        int(c.get("zt_n") or 0),
                        int(c.get("industries") or 0),
                        json.dumps(concepts, ensure_ascii=False),
                        mainline or None,
                        at if is_main else None,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE narrative_shadow SET
                        last_seen = ?,
                        peak_zt = MAX(COALESCE(peak_zt, 0), ?),
                        industries = MAX(COALESCE(industries, 0), ?),
                        concepts = ?,
                        became_mainline_at = COALESCE(became_mainline_at, ?)
                    WHERE trade_date = ? AND label = ?
                    """,
                    (
                        at,
                        int(c.get("zt_n") or 0),
                        int(c.get("industries") or 0),
                        json.dumps(concepts, ensure_ascii=False),
                        at if is_main else None,
                        day,
                        label,
                    ),
                )
            n += 1
        conn.commit()
    return n


def save_crowd_shadow(trade_date: str, hits: list[dict[str, Any]], *, at: str) -> int:
    """Record the first extreme-crowding hit per code per day (hard or soft).

    The ledger answers "what would a hard ban have blocked": compare ``price``
    with later closes before making the ban unconditional.

    Args:
        trade_date: ``YYYY-MM-DD`` session date.
        hits: ``crowding.hits`` rows from ``apply_crowding_gate``.
        at: ``HH:MM:SS`` clock of the hit.

    Returns:
        Number of rows offered (duplicates are ignored).
    """
    day = str(trade_date or "")[:10]
    data = [
        (
            day,
            str(h.get("code") or ""),
            str(h.get("name") or ""),
            str(h.get("board") or ""),
            str(h.get("box") or ""),
            str(h.get("mode") or ""),
            1 if h.get("was_ready") else 0,
            h.get("price"),
            h.get("share"),
            at,
        )
        for h in hits or []
        if h.get("code")
    ]
    if not day or not data:
        return 0
    with _connect() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO crowd_shadow(
                trade_date, code, name, board, box, mode, was_ready, price, share, at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            data,
        )
        conn.commit()
    return len(data)


def load_crowd_shadow(since: str, until: str | None = None) -> list[dict[str, Any]]:
    """Return crowding shadow hits between two dates (inclusive), oldest first."""
    lo = str(since or "")[:10]
    hi = str(until or "9999-12-31")[:10]
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM crowd_shadow WHERE trade_date >= ? AND trade_date <= ? "
            "ORDER BY trade_date ASC, at ASC",
            (lo, hi),
        ).fetchall()
    return [dict(r) for r in rows]


def load_narrative_shadow(since: str, until: str | None = None) -> list[dict[str, Any]]:
    """Return shadow sightings between two dates (inclusive), newest first."""
    lo = str(since or "")[:10]
    hi = str(until or "9999-12-31")[:10]
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM narrative_shadow WHERE trade_date >= ? AND trade_date <= ? "
            "ORDER BY trade_date DESC, first_seen ASC",
            (lo, hi),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        try:
            item["concepts"] = json.loads(item.get("concepts") or "[]")
        except (TypeError, ValueError):
            item["concepts"] = []
        out.append(item)
    return out
