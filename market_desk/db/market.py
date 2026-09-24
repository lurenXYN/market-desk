"""Daily snapshots, breadth sanitizing, auction locks, boards and fund flow."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


def save_daily(trade_date: str, payload: dict[str, Any], *, merge: bool = True) -> None:
    """Upsert today's compact daily row used by the history table.

    When ``merge`` is True (default), patch onto any existing payload for the
    day so later writes (e.g. mainline) do not wipe metrics already saved.
    Quote-derived breadth fields are not overwritten by all-zero stubs when the
    live quote list failed (e.g. East Money clist 502).
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    day = str(trade_date or "")[:10]
    data = dict(payload or {})
    with _connect() as conn:
        if merge and day:
            row = conn.execute(
                "SELECT payload FROM daily_snapshot WHERE trade_date = ?",
                (day,),
            ).fetchone()
            if row and row["payload"]:
                try:
                    old = json.loads(row["payload"])
                    if isinstance(old, dict):
                        merged = dict(old)
                        degraded = _breadth_fields_degraded(data)
                        old_span = _breadth_span(old)
                        new_span = _breadth_span(data)
                        # Prefer a full-market prior over a thinner emergency sample.
                        thin_worse = (
                            0 < new_span < 800
                            and old_span >= 800
                        ) or (
                            0 < new_span < 800
                            and old_span > new_span
                        )
                        for k, v in data.items():
                            if v is None:
                                continue
                            if (
                                k in _BREADTH_FIELDS
                                and (
                                    (
                                        degraded
                                        and _has_good_breadth(old)
                                    )
                                    or thin_worse
                                )
                            ):
                                # Keep prior real ups/downs/amount when quotes blanked/thin.
                                continue
                            merged[k] = v
                        # Preserve prior mainline when new payload omits it.
                        if not str(merged.get("mainline") or "").strip():
                            prev_ml = str(old.get("mainline") or "").strip()
                            if prev_ml:
                                merged["mainline"] = prev_ml
                        data = merged
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        # Never persist zero stubs or thin (~200-row) emergency breadth as truth.
        if _breadth_fields_degraded(data):
            span = _breadth_span(data)
            for k in _BREADTH_FIELDS:
                if k not in data:
                    continue
                val = data.get(k)
                if val == 0 or val == 0.0 or (0 < span < 800):
                    data[k] = None
            data["breadth_degraded"] = True
        conn.execute(
            """
            INSERT INTO daily_snapshot(trade_date, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (day, json.dumps(data, ensure_ascii=False), now),
        )
        conn.commit()


_BREADTH_FIELDS = ("ups", "downs", "amount_yi", "amount_pctile", "big_drop")


def _breadth_span(payload: dict[str, Any] | None) -> int:
    """Return ups+downs count for a daily payload (0 when missing)."""
    data = payload or {}
    try:
        return int(data.get("ups") or 0) + int(data.get("downs") or 0)
    except (TypeError, ValueError):
        return 0


def _breadth_fields_degraded(payload: dict[str, Any] | None) -> bool:
    """True when ups/downs/amount look like a failed or partial quote list.

    Full main-board breadth is thousands of names; a ~200-row emergency sample
    (often ups≈200, downs≈0) must not be treated as a real flat/strong day.
    """
    data = payload or {}
    ups = int(data.get("ups") or 0)
    downs = int(data.get("downs") or 0)
    amt = float(data.get("amount_yi") or 0)
    zt = int(data.get("zt") or 0)
    span = ups + downs
    if ups == 0 and downs == 0 and amt <= 0 and zt >= 5:
        return True
    # Thin partial sample (emergency clist pages).
    if 0 < span < 800:
        return True
    return False


def _has_nonzero_breadth(payload: dict[str, Any] | None) -> bool:
    """True when a prior daily row already stored any non-zero breadth fields."""
    data = payload or {}
    try:
        if int(data.get("ups") or 0) > 0 or int(data.get("downs") or 0) > 0:
            return True
        if float(data.get("amount_yi") or 0) > 0:
            return True
    except (TypeError, ValueError):
        return False
    return False


def _has_good_breadth(payload: dict[str, Any] | None) -> bool:
    """True when prior ups+downs look like a full main-board sample (>=800)."""
    return _breadth_span(payload) >= 800


def sanitize_daily_row(row: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a loaded daily row so failed/thin quote breadth render as blanks."""
    item = dict(row or {})
    if not _breadth_fields_degraded(item):
        return item
    span = _breadth_span(item)
    for k in _BREADTH_FIELDS:
        val = item.get(k)
        if val == 0 or val == 0.0 or (0 < span < 800):
            item[k] = None
    item["breadth_degraded"] = True
    ev = str(item.get("event") or "")
    ml = str(item.get("mainline") or "").strip()
    if ml:
        import re

        ev2 = re.sub(r"热点\s*[—\-–−]+", f"热点{ml}", ev)
        if ev2 != ev:
            item["event"] = ev2
    return item


def load_daily(limit: int = 14) -> list[dict[str, Any]]:
    """Return recent daily snapshots, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT trade_date, payload FROM daily_snapshot ORDER BY trade_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    dirty: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        item = json.loads(row["payload"])
        item["trade_date"] = row["trade_date"]
        cleaned = sanitize_daily_row(item)
        if cleaned.get("breadth_degraded") and not (item.get("breadth_degraded")):
            dirty.append((str(row["trade_date"]), cleaned))
        out.append(cleaned)
    # One-shot heal of already-written zero stubs (e.g. 2026-09-21 after clist 502 day).
    if dirty:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with _connect() as conn:
                for day, payload in dirty:
                    conn.execute(
                        """
                        UPDATE daily_snapshot
                        SET payload = ?, updated_at = ?
                        WHERE trade_date = ?
                        """,
                        (json.dumps(payload, ensure_ascii=False), now, day),
                    )
                conn.commit()
        except Exception:
            pass
    return out


def save_auction(trade_date: str, payload: dict[str, Any]) -> None:
    """Lock the 09:25 auction summary for a trading day."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO auction_lock(
                trade_date, median_open, high_open_share, tone, payload, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                median_open = excluded.median_open,
                high_open_share = excluded.high_open_share,
                tone = excluded.tone,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                trade_date,
                payload.get("median_open"),
                payload.get("high_open_share"),
                payload.get("tone"),
                json.dumps(payload, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()


def load_auction(trade_date: str) -> dict[str, Any] | None:
    """Return the locked auction summary for the given date."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload FROM auction_lock WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["payload"])


def save_board_daily(trade_date: str, rows: list[dict[str, Any]]) -> None:
    """Upsert today's compact sector rows used for concentration history."""
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO board_daily(
                trade_date, bk, name, zt_n, dt_n, pct, leader_boards, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, bk) DO UPDATE SET
                name = excluded.name,
                zt_n = excluded.zt_n,
                dt_n = excluded.dt_n,
                pct = excluded.pct,
                leader_boards = excluded.leader_boards,
                status = excluded.status
            """,
            [
                (
                    trade_date,
                    r.get("bk") or "",
                    r.get("name") or "",
                    int(r.get("zt_n") or 0),
                    int(r.get("dt_n") or 0),
                    r.get("pct"),
                    int(r.get("leader_boards") or 0),
                    r.get("status") or "",
                )
                for r in rows
                if r.get("bk")
            ],
        )
        conn.commit()


def save_fund_flow_daily(trade_date: str, rows: list[dict[str, Any]]) -> int:
    """Upsert one trade day's board money-flow prints for local week/month sums."""
    day = str(trade_date or "")[:10]
    if not day or not rows:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = []
    for r in rows:
        bk = str(r.get("bk") or "").strip()
        kind = str(r.get("kind") or "").strip() or "industry"
        if not bk:
            continue
        payload.append(
            (
                day,
                bk,
                kind,
                str(r.get("name") or ""),
                r.get("pct"),
                r.get("main_net"),
                r.get("main_pct"),
                r.get("super_net"),
                r.get("large_net"),
                str(r.get("leader_name") or ""),
                str(r.get("leader_code") or ""),
                now,
            )
        )
    if not payload:
        return 0
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO fund_flow_daily(
                trade_date, bk, kind, name, pct, main_net, main_pct,
                super_net, large_net, leader_name, leader_code, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, bk, kind) DO UPDATE SET
                name = excluded.name,
                pct = excluded.pct,
                main_net = excluded.main_net,
                main_pct = excluded.main_pct,
                super_net = excluded.super_net,
                large_net = excluded.large_net,
                leader_name = excluded.leader_name,
                leader_code = excluded.leader_code,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        conn.commit()
    return len(payload)


def list_fund_flow_dates(through: str, limit: int = 40) -> list[str]:
    """Return recent trade dates that have fund-flow rows (newest first)."""
    day = str(through or "")[:10]
    lim = max(1, min(int(limit or 40), 120))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM fund_flow_daily
            WHERE trade_date <= ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (day, lim),
        ).fetchall()
    return [str(r["trade_date"]) for r in rows]


def load_fund_flow_for_dates(dates: list[str]) -> list[dict[str, Any]]:
    """Load raw daily fund-flow rows for the given trade dates."""
    days = [str(d)[:10] for d in dates if str(d or "").strip()]
    if not days:
        return []
    placeholders = ",".join("?" for _ in days)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT trade_date, bk, kind, name, pct, main_net, main_pct,
                   super_net, large_net, leader_name, leader_code
            FROM fund_flow_daily
            WHERE trade_date IN ({placeholders})
            """,
            days,
        ).fetchall()
    return [
        {
            "trade_date": str(r["trade_date"]),
            "bk": str(r["bk"] or ""),
            "kind": str(r["kind"] or ""),
            "name": str(r["name"] or ""),
            "pct": r["pct"],
            "main_net": r["main_net"],
            "main_pct": r["main_pct"],
            "super_net": r["super_net"],
            "large_net": r["large_net"],
            "leader_name": str(r["leader_name"] or ""),
            "leader_code": str(r["leader_code"] or ""),
        }
        for r in rows
    ]


def load_board_hist_map(before_date: str, days: int = 8) -> dict[str, list[dict[str, Any]]]:
    """Return prior-day sector stats keyed by board code, oldest first.

    Only scans a bounded recent window (``days + 4`` calendar cushion) so the
    ``board_daily`` table does not grow into a full-history scan each refresh.
    """
    window = max(int(days) + 4, 8)
    with _connect() as conn:
        date_rows = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM board_daily
            WHERE trade_date < ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (before_date, window),
        ).fetchall()
        dates = [str(r["trade_date"]) for r in date_rows]
        if not dates:
            return {}
        placeholders = ",".join("?" for _ in dates)
        rows = conn.execute(
            f"""
            SELECT trade_date, bk, name, zt_n, dt_n, pct, leader_boards, status
            FROM board_daily
            WHERE trade_date IN ({placeholders})
            ORDER BY trade_date ASC
            """,
            dates,
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        out.setdefault(item["bk"], []).append(item)
    for bk, series in list(out.items()):
        out[bk] = series[-days:]
    return out


def load_board_rows_for_date(trade_date: str) -> list[dict[str, Any]]:
    """Return all board_daily rows for one trade date."""
    day = str(trade_date or "")[:10]
    if not day:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, bk, name, zt_n, dt_n, pct, leader_boards, status
            FROM board_daily
            WHERE trade_date = ?
            """,
            (day,),
        ).fetchall()
    return [dict(r) for r in rows]
