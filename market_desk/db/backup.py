"""Full / per-user backup export and import."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


def export_backup_payload() -> dict[str, Any]:
    """Export core local tables as a JSON-serializable backup dict."""
    with _connect() as conn:
        signals = [dict(r) for r in conn.execute("SELECT * FROM signals ORDER BY id").fetchall()]
        positions = [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY id").fetchall()]
        watchlist = [dict(r) for r in conn.execute("SELECT * FROM watchlist ORDER BY id").fetchall()]
        favorite_boards = [
            dict(r) for r in conn.execute("SELECT * FROM favorite_boards ORDER BY id").fetchall()
        ]
        stock_blacklist = [
            dict(r) for r in conn.execute("SELECT * FROM stock_blacklist ORDER BY code").fetchall()
        ]
        digests = [dict(r) for r in conn.execute("SELECT * FROM review_digest ORDER BY trade_date").fetchall()]
        settings = [dict(r) for r in conn.execute("SELECT * FROM settings").fetchall()]
        overrides = [dict(r) for r in conn.execute("SELECT * FROM trend_override").fetchall()]
    return {
        "version": 1,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "signals": signals,
        "positions": positions,
        "watchlist": watchlist,
        "favorite_boards": favorite_boards,
        "stock_blacklist": stock_blacklist,
        "review_digest": digests,
        "settings": settings,
        "trend_override": overrides,
    }


def export_user_backup_payload(user_id: int) -> dict[str, Any]:
    """Export one user's personal books / fills / private settings."""
    uid = int(user_id)
    with _connect() as conn:
        positions = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM positions WHERE user_id = ? ORDER BY id", (uid,)
            ).fetchall()
        ]
        watchlist = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM watchlist WHERE user_id = ? ORDER BY id", (uid,)
            ).fetchall()
        ]
        favorite_boards = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM favorite_boards WHERE user_id = ? ORDER BY id", (uid,)
            ).fetchall()
        ]
        signal_user_meta = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM signal_user_meta WHERE user_id = ? ORDER BY signal_id",
                (uid,),
            ).fetchall()
        ]
        # Personal knobs live in settings as key user:{id}:runtime
        setting_key = f"user:{uid}:runtime"
        user_settings_row = conn.execute(
            "SELECT key, value, updated_at FROM settings WHERE key = ?",
            (setting_key,),
        ).fetchone()
        user_settings = [dict(user_settings_row)] if user_settings_row else []
    return {
        "version": 2,
        "scope": "user",
        "user_id": uid,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "positions": positions,
        "watchlist": watchlist,
        "favorite_boards": favorite_boards,
        "signal_user_meta": signal_user_meta,
        "user_settings": user_settings,
    }


def import_user_backup_payload(
    user_id: int, payload: dict[str, Any], *, replace: bool = False
) -> dict[str, int]:
    """Import personal data into one user. Never touches other users' rows."""
    if not isinstance(payload, dict):
        raise ValueError("backup payload must be an object")
    uid = int(user_id)
    counts = {
        "positions": 0,
        "watchlist": 0,
        "favorite_boards": 0,
        "signal_user_meta": 0,
        "user_settings": 0,
    }
    with _connect() as conn:
        if replace:
            conn.execute("DELETE FROM positions WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM watchlist WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM favorite_boards WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM signal_user_meta WHERE user_id = ?", (uid,))
            conn.execute(
                "DELETE FROM settings WHERE key = ?", (f"user:{uid}:runtime",)
            )
        for row in payload.get("positions") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO positions(
                    user_id, code, name, buy_price, qty, note, created_at, last_buy_date,
                    closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl,
                    peak_price, entry_board
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    str(row.get("code") or "").zfill(6),
                    row.get("name") or "",
                    float(row.get("buy_price") or 0),
                    int(row.get("qty") or 0),
                    row.get("note") or "",
                    row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("last_buy_date") or str(row.get("created_at") or "")[:10] or None,
                    row.get("closed_date"),
                    row.get("last_sell_date"),
                    row.get("last_sell_price"),
                    int(row.get("day_sold_qty") or 0),
                    float(row.get("day_realized_pnl") or 0),
                    row.get("peak_price"),
                    row.get("entry_board"),
                ),
            )
            counts["positions"] += 1
        for row in payload.get("watchlist") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO watchlist(
                    user_id, code, name, note, suggest_price, stop_price, chase_price,
                    first_seen_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, code) DO UPDATE SET
                    name = excluded.name,
                    note = excluded.note,
                    stop_price = excluded.stop_price,
                    chase_price = excluded.chase_price
                """,
                (
                    uid,
                    str(row.get("code") or "").zfill(6),
                    row.get("name") or "",
                    row.get("note") or "",
                    row.get("suggest_price"),
                    row.get("stop_price"),
                    row.get("chase_price"),
                    row.get("first_seen_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["watchlist"] += 1
        for row in payload.get("favorite_boards") or []:
            if not isinstance(row, dict):
                continue
            bk = str(row.get("bk") or "").strip().upper()
            if not bk.startswith("BK"):
                continue
            conn.execute(
                """
                INSERT INTO favorite_boards(user_id, bk, name, kind, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, bk) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    note = excluded.note
                """,
                (
                    uid,
                    bk,
                    row.get("name") or "",
                    row.get("kind") or "",
                    row.get("note") or "",
                    row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["favorite_boards"] += 1
        for row in payload.get("signal_user_meta") or []:
            if not isinstance(row, dict):
                continue
            try:
                sid = int(row.get("signal_id") or 0)
            except (TypeError, ValueError):
                continue
            if sid <= 0:
                continue
            exists = conn.execute(
                "SELECT id FROM signals WHERE id = ?", (sid,)
            ).fetchone()
            if not exists:
                continue
            conn.execute(
                """
                INSERT INTO signal_user_meta(
                    user_id, signal_id, skipped, traded, note, fill_price, fill_qty, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, signal_id) DO UPDATE SET
                    skipped = excluded.skipped,
                    traded = excluded.traded,
                    note = excluded.note,
                    fill_price = excluded.fill_price,
                    fill_qty = excluded.fill_qty,
                    updated_at = excluded.updated_at
                """,
                (
                    uid,
                    sid,
                    int(row.get("skipped") or 0),
                    int(row.get("traded") or 0),
                    row.get("note"),
                    row.get("fill_price"),
                    row.get("fill_qty"),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["signal_user_meta"] += 1
        for row in payload.get("user_settings") or []:
            if not isinstance(row, dict):
                continue
            # Accept either the namespaced settings key or a bare runtime blob.
            key = str(row.get("key") or "").strip() or f"user:{uid}:runtime"
            if not key.startswith(f"user:{uid}:"):
                key = f"user:{uid}:runtime"
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    row.get("value")
                    if isinstance(row.get("value"), str)
                    else json.dumps(row.get("value"), ensure_ascii=False),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["user_settings"] += 1
        conn.commit()
    return counts


def import_backup_payload(payload: dict[str, Any], *, replace: bool = False) -> dict[str, int]:
    """Import a backup payload. When replace=True, clear target tables first."""
    if not isinstance(payload, dict):
        raise ValueError("backup payload must be an object")
    counts = {
        "signals": 0,
        "positions": 0,
        "watchlist": 0,
        "favorite_boards": 0,
        "stock_blacklist": 0,
        "review_digest": 0,
        "settings": 0,
        "trend_override": 0,
    }
    with _connect() as conn:
        if replace:
            for table in (
                "signals",
                "positions",
                "watchlist",
                "favorite_boards",
                "stock_blacklist",
                "review_digest",
                "settings",
                "trend_override",
            ):
                conn.execute(f"DELETE FROM {table}")
        for row in payload.get("positions") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO positions(
                    code, name, buy_price, qty, note, created_at, last_buy_date,
                    closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("code") or "").zfill(6),
                    row.get("name") or "",
                    float(row.get("buy_price") or 0),
                    int(row.get("qty") or 0),
                    row.get("note") or "",
                    row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("last_buy_date") or str(row.get("created_at") or "")[:10] or None,
                    row.get("closed_date"),
                    row.get("last_sell_date"),
                    row.get("last_sell_price"),
                    int(row.get("day_sold_qty") or 0),
                    float(row.get("day_realized_pnl") or 0),
                ),
            )
            counts["positions"] += 1
        for row in payload.get("watchlist") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO watchlist(
                    code, name, note, suggest_price, stop_price, chase_price, first_seen_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    note = excluded.note,
                    stop_price = excluded.stop_price,
                    chase_price = excluded.chase_price
                """,
                (
                    str(row.get("code") or "").zfill(6),
                    row.get("name") or "",
                    row.get("note") or "",
                    row.get("suggest_price"),
                    row.get("stop_price"),
                    row.get("chase_price"),
                    row.get("first_seen_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["watchlist"] += 1
        for row in payload.get("favorite_boards") or []:
            if not isinstance(row, dict):
                continue
            bk = str(row.get("bk") or "").strip().upper()
            if not bk.startswith("BK"):
                continue
            conn.execute(
                """
                INSERT INTO favorite_boards(bk, name, kind, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(bk) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    note = excluded.note
                """,
                (
                    bk,
                    row.get("name") or "",
                    row.get("kind") or "",
                    row.get("note") or "",
                    row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["favorite_boards"] += 1
        for row in payload.get("stock_blacklist") or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").zfill(6)
            if len(code) != 6 or not code.isdigit():
                continue
            conn.execute(
                """
                INSERT INTO stock_blacklist(
                    code, name, reason, source, strike_n, clean_streak,
                    blocked_at, updated_at, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    reason = excluded.reason,
                    source = excluded.source,
                    strike_n = excluded.strike_n,
                    clean_streak = excluded.clean_streak,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    code,
                    row.get("name") or "",
                    row.get("reason") or "",
                    row.get("source") or "manual",
                    int(row.get("strike_n") or 0),
                    int(row.get("clean_streak") or 0),
                    row.get("blocked_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("note") or "",
                ),
            )
            counts["stock_blacklist"] += 1
        for row in payload.get("signals") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO signals(
                    trade_date, signaled_at, signal_type, action, phase, mainline,
                    code, name, kind, price, last, ready, payload,
                    outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
                    outcome_label, outcome_checked_at, note, skipped, traded, fill_price, fill_qty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, code, signal_type) DO UPDATE SET
                    action = excluded.action,
                    phase = excluded.phase,
                    mainline = excluded.mainline,
                    name = excluded.name,
                    kind = excluded.kind,
                    last = excluded.last,
                    ready = excluded.ready,
                    payload = excluded.payload,
                    note = excluded.note,
                    skipped = excluded.skipped,
                    traded = excluded.traded,
                    fill_price = excluded.fill_price,
                    fill_qty = excluded.fill_qty,
                    outcome_day1_pct = excluded.outcome_day1_pct,
                    outcome_day3_pct = excluded.outcome_day3_pct,
                    outcome_mfe_pct = excluded.outcome_mfe_pct,
                    outcome_mae_pct = excluded.outcome_mae_pct,
                    outcome_label = excluded.outcome_label,
                    outcome_checked_at = excluded.outcome_checked_at
                """,
                (
                    row.get("trade_date"),
                    row.get("signaled_at"),
                    row.get("signal_type"),
                    row.get("action"),
                    row.get("phase"),
                    row.get("mainline"),
                    str(row.get("code") or "").zfill(6),
                    row.get("name"),
                    row.get("kind"),
                    row.get("price"),
                    row.get("last"),
                    int(row.get("ready") or 0),
                    row.get("payload")
                    if isinstance(row.get("payload"), str)
                    else json.dumps(row.get("payload") or {}, ensure_ascii=False),
                    row.get("outcome_day1_pct"),
                    row.get("outcome_day3_pct"),
                    row.get("outcome_mfe_pct"),
                    row.get("outcome_mae_pct"),
                    row.get("outcome_label"),
                    row.get("outcome_checked_at"),
                    row.get("note"),
                    int(row.get("skipped") or 0),
                    int(row.get("traded") or 0),
                    row.get("fill_price"),
                    row.get("fill_qty"),
                ),
            )
            counts["signals"] += 1
        for row in payload.get("review_digest") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO review_digest(trade_date, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    row.get("trade_date"),
                    row.get("payload")
                    if isinstance(row.get("payload"), str)
                    else json.dumps(row.get("payload") or {}, ensure_ascii=False),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["review_digest"] += 1
        for row in payload.get("settings") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (
                    row.get("key"),
                    row.get("value")
                    if isinstance(row.get("value"), str)
                    else json.dumps(row.get("value"), ensure_ascii=False),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["settings"] += 1
        for row in payload.get("trend_override") or []:
            if not isinstance(row, dict):
                continue
            conn.execute(
                """
                INSERT INTO trend_override(trade_date, code, verdict, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_date, code) DO UPDATE SET
                    verdict = excluded.verdict,
                    updated_at = excluded.updated_at
                """,
                (
                    row.get("trade_date"),
                    str(row.get("code") or "").zfill(6),
                    row.get("verdict"),
                    row.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            counts["trend_override"] += 1
        conn.commit()
    return counts
