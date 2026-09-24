"""Positions, FIFO lots, T+1 checks and the execution diary."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


_POS_SELECT = """
    id, user_id, code, name, buy_price, qty, note, created_at, last_buy_date,
    closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl,
    day_sell_notional, peak_price, entry_board
"""


def _backfill_position_lots(conn: sqlite3.Connection) -> None:
    """Seed one lot row for open positions missing lot history (compat)."""
    try:
        rows = conn.execute(
            """
            SELECT p.id, p.user_id, p.code, p.buy_price, p.qty, p.created_at,
                   p.last_buy_date, p.note
            FROM positions p
            WHERE COALESCE(p.qty, 0) > 0
              AND NOT EXISTS (
                SELECT 1 FROM position_lots l WHERE l.position_id = p.id AND l.qty > 0
              )
            """
        ).fetchall()
    except sqlite3.Error:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        buy_day = str(row["last_buy_date"] or row["created_at"] or now)[:10]
        conn.execute(
            """
            INSERT INTO position_lots(
                user_id, position_id, code, buy_price, qty, buy_date, created_at, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["user_id"] or 0),
                int(row["id"]),
                str(row["code"] or "").zfill(6),
                float(row["buy_price"] or 0),
                int(row["qty"] or 0),
                buy_day,
                str(row["created_at"] or now),
                str(row["note"] or "") or "历史回填",
            ),
        )


def _insert_position_lot(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    position_id: int,
    code: str,
    buy_price: float,
    qty: int,
    buy_date: str,
    created_at: str,
    note: str = "",
) -> int:
    """Insert one buy lot under a parent position. Returns lot id."""
    cur = conn.execute(
        """
        INSERT INTO position_lots(
            user_id, position_id, code, buy_price, qty, buy_date, created_at, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            int(position_id),
            str(code or "").zfill(6),
            float(buy_price),
            int(qty),
            str(buy_date)[:10],
            str(created_at),
            str(note or ""),
        ),
    )
    return int(cur.lastrowid)


def _fifo_trim_lots(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    qty: int,
) -> list[dict[str, Any]]:
    """Reduce oldest lots first; delete empty lots. Returns consumed lot chunks."""
    sell = int(qty)
    if sell <= 0:
        return []
    rows = conn.execute(
        """
        SELECT id, buy_price, qty, buy_date
        FROM position_lots
        WHERE position_id = ? AND qty > 0
        ORDER BY id ASC
        """,
        (int(position_id),),
    ).fetchall()
    consumed: list[dict[str, Any]] = []
    left = sell
    for row in rows:
        if left <= 0:
            break
        lot_qty = int(row["qty"] or 0)
        take = min(lot_qty, left)
        if take <= 0:
            continue
        remain = lot_qty - take
        if remain <= 0:
            conn.execute("DELETE FROM position_lots WHERE id = ?", (int(row["id"]),))
        else:
            conn.execute(
                "UPDATE position_lots SET qty = ? WHERE id = ?",
                (remain, int(row["id"])),
            )
        consumed.append(
            {
                "lot_id": int(row["id"]),
                "buy_price": float(row["buy_price"] or 0),
                "qty": take,
                "buy_date": str(row["buy_date"] or "")[:10],
            }
        )
        left -= take
    return consumed


def load_lots_for_positions(
    position_ids: list[int] | None,
    *,
    user_id: int | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """Return position_id → open lots (oldest first) for decorate / UI."""
    ids = [int(x) for x in (position_ids or []) if x]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    params: list[Any] = list(ids)
    sql = f"""
        SELECT id, user_id, position_id, code, buy_price, qty, buy_date, created_at, note
        FROM position_lots
        WHERE position_id IN ({placeholders}) AND qty > 0
    """
    if user_id is not None:
        sql += " AND user_id = ?"
        params.append(int(user_id))
    sql += " ORDER BY position_id ASC, id ASC"
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    with _connect() as conn:
        for row in conn.execute(sql, params).fetchall():
            pid = int(row["position_id"])
            out.setdefault(pid, []).append(
                {
                    "id": int(row["id"]),
                    "buy_price": round(float(row["buy_price"] or 0), 4),
                    "qty": int(row["qty"] or 0),
                    "buy_date": str(row["buy_date"] or "")[:10],
                    "note": str(row["note"] or "") or None,
                    "cost": round(
                        float(row["buy_price"] or 0) * int(row["qty"] or 0), 2
                    ),
                }
            )
    return out


def add_exec_diary(
    *,
    user_id: int,
    side: str,
    code: str,
    name: str = "",
    qty: int | None = None,
    price: float | None = None,
    advice: dict[str, Any] | None = None,
    note: str = "",
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Append one execution diary row capturing the live advice snapshot."""
    import json

    uid = int(user_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    day = str(trade_date or now)[:10]
    side_s = str(side or "").strip().lower() or "buy"
    code_s = str(code or "").zfill(6)
    payload = json.dumps(advice or {}, ensure_ascii=False, default=str)
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO exec_diary(
                user_id, trade_date, created_at, side, code, name, qty, price,
                advice_json, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid,
                day,
                now,
                side_s,
                code_s,
                str(name or ""),
                int(qty) if qty is not None else None,
                float(price) if price is not None else None,
                payload,
                str(note or ""),
            ),
        )
        conn.commit()
        did = int(cur.lastrowid)
    return {
        "id": did,
        "user_id": uid,
        "trade_date": day,
        "created_at": now,
        "side": side_s,
        "code": code_s,
        "name": name,
        "qty": qty,
        "price": price,
        "note": note,
    }


def load_exec_diary(
    *,
    user_id: int,
    trade_date: str | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Return recent exec diary rows for one user (newest first)."""
    import json

    uid = int(user_id)
    lim = max(1, min(int(limit or 40), 500))
    with _connect() as conn:
        if trade_date:
            rows = conn.execute(
                """
                SELECT id, user_id, trade_date, created_at, side, code, name,
                       qty, price, advice_json, note
                FROM exec_diary
                WHERE user_id = ? AND trade_date = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (uid, str(trade_date)[:10], lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, user_id, trade_date, created_at, side, code, name,
                       qty, price, advice_json, note
                FROM exec_diary
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (uid, lim),
            ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.pop("advice_json", None)
        try:
            item["advice"] = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            item["advice"] = {}
        out.append(item)
    return out


def load_recent_buy_diary(*, limit: int = 40) -> list[dict[str, Any]]:
    """Return recent buy-side diary rows across users (for global exec-score adapt)."""
    import json

    lim = max(1, min(int(limit or 40), 120))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, trade_date, created_at, side, code, name,
                   qty, price, advice_json, note
            FROM exec_diary
            WHERE side = 'buy'
            ORDER BY id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.pop("advice_json", None)
        try:
            item["advice"] = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            item["advice"] = {}
        out.append(item)
    return out


def _position_item(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Normalize a positions table row for API / decorate use."""
    item = dict(row)
    if not item.get("last_buy_date"):
        item["last_buy_date"] = str(item.get("created_at") or "")[:10] or None
    item["day_sold_qty"] = int(item.get("day_sold_qty") or 0)
    item["day_realized_pnl"] = float(item.get("day_realized_pnl") or 0)
    try:
        item["day_sell_notional"] = float(item.get("day_sell_notional") or 0)
    except (TypeError, ValueError):
        item["day_sell_notional"] = 0.0
    qty = int(item.get("qty") or 0)
    item["closed"] = qty <= 0 and bool(str(item.get("closed_date") or "").strip())
    peak = item.get("peak_price")
    item["peak_price"] = float(peak) if peak not in (None, "") else None
    item["entry_board"] = str(item.get("entry_board") or "").strip() or None
    try:
        item["user_id"] = int(item["user_id"]) if item.get("user_id") is not None else None
    except (TypeError, ValueError):
        item["user_id"] = None
    return item


def touch_position_peaks(peaks: dict[int, float]) -> int:
    """Persist updated hold-peak prices keyed by position id. Returns rows touched."""
    if not peaks:
        return 0
    n = 0
    with _connect() as conn:
        for pid, peak in peaks.items():
            try:
                pid_i = int(pid)
                peak_f = float(peak)
            except (TypeError, ValueError):
                continue
            if peak_f <= 0:
                continue
            cur = conn.execute(
                """
                UPDATE positions
                SET peak_price = ?
                WHERE id = ? AND qty > 0
                  AND (peak_price IS NULL OR peak_price < ?)
                """,
                (peak_f, pid_i, peak_f),
            )
            n += int(cur.rowcount or 0)
        conn.commit()
    return n


def add_position(
    code: str,
    name: str,
    buy_price: float,
    qty: int,
    note: str = "",
    *,
    entry_board: str = "",
    user_id: int,
) -> dict[str, Any]:
    """Insert a position, or average into an existing open same-code row.

    Always records a ``position_lots`` leg so multi-fill cost stays auditable while
    the parent row keeps a weighted-average ``buy_price`` for display.
    """
    uid = int(user_id)
    code = str(code or "").zfill(6)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    buy_day = now[:10]
    board = str(entry_board or "").strip() or None
    add_qty = int(qty)
    add_px = float(buy_price)
    with _connect() as conn:
        existing = conn.execute(
            f"""
            SELECT {_POS_SELECT}
            FROM positions
            WHERE code = ? AND user_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (code, uid),
        ).fetchone()
        if existing:
            old_qty = int(existing["qty"] or 0)
            old_px = float(existing["buy_price"] or 0)
            pid = int(existing["id"])
            if old_qty <= 0:
                conn.execute(
                    """
                    UPDATE positions
                    SET name = ?, buy_price = ?, qty = ?, note = ?, last_buy_date = ?,
                        closed_date = NULL, created_at = ?, peak_price = ?, entry_board = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        name or existing["name"] or code,
                        add_px,
                        add_qty,
                        note or existing["note"] or "",
                        buy_day,
                        now,
                        add_px,
                        board or (str(existing["entry_board"] or "").strip() or None),
                        pid,
                        uid,
                    ),
                )
                _insert_position_lot(
                    conn,
                    user_id=uid,
                    position_id=pid,
                    code=code,
                    buy_price=add_px,
                    qty=add_qty,
                    buy_date=buy_day,
                    created_at=now,
                    note=note or "重开仓",
                )
                conn.commit()
                return {
                    "id": pid,
                    "user_id": uid,
                    "code": code,
                    "name": name or existing["name"] or code,
                    "buy_price": add_px,
                    "qty": add_qty,
                    "note": note or existing["note"] or "",
                    "created_at": now,
                    "last_buy_date": buy_day,
                    "entry_board": board or (str(existing["entry_board"] or "").strip() or None),
                    "reopened": True,
                    "lot_added": True,
                }
            new_qty = old_qty + add_qty
            if new_qty <= 0:
                conn.execute(
                    "DELETE FROM positions WHERE id = ? AND user_id = ?",
                    (pid, uid),
                )
                conn.execute(
                    "DELETE FROM position_lots WHERE position_id = ?",
                    (pid,),
                )
                conn.commit()
                return {"id": pid, "deleted": True, "code": code}
            avg = (old_px * old_qty + add_px * add_qty) / float(new_qty)
            merged_note = (existing["note"] or "") or note
            if note and existing["note"] and note not in str(existing["note"]):
                merged_note = f"{existing['note']}；{note}"
            conn.execute(
                """
                UPDATE positions
                SET name = ?, buy_price = ?, qty = ?, note = ?, last_buy_date = ?,
                    closed_date = NULL, entry_board = COALESCE(?, entry_board)
                WHERE id = ? AND user_id = ?
                """,
                (
                    name or existing["name"] or code,
                    round(avg, 4),
                    new_qty,
                    merged_note,
                    buy_day,
                    board,
                    pid,
                    uid,
                ),
            )
            _insert_position_lot(
                conn,
                user_id=uid,
                position_id=pid,
                code=code,
                buy_price=add_px,
                qty=add_qty,
                buy_date=buy_day,
                created_at=now,
                note=note or "加仓",
            )
            conn.commit()
            return {
                "id": pid,
                "user_id": uid,
                "code": code,
                "name": name or existing["name"] or code,
                "buy_price": round(avg, 4),
                "qty": new_qty,
                "note": merged_note,
                "created_at": existing["created_at"] or now,
                "last_buy_date": buy_day,
                "entry_board": board or (str(existing["entry_board"] or "").strip() or None),
                "averaged": True,
                "lot_added": True,
                "lot_price": add_px,
                "lot_qty": add_qty,
            }
        cur = conn.execute(
            """
            INSERT INTO positions(
                user_id, code, name, buy_price, qty, note, created_at, last_buy_date,
                closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl,
                peak_price, entry_board
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 0, ?, ?)
            """,
            (uid, code, name, add_px, add_qty, note, now, buy_day, add_px, board),
        )
        pid = int(cur.lastrowid)
        _insert_position_lot(
            conn,
            user_id=uid,
            position_id=pid,
            code=code,
            buy_price=add_px,
            qty=add_qty,
            buy_date=buy_day,
            created_at=now,
            note=note or "开仓",
        )
        conn.commit()
    return {
        "id": pid,
        "user_id": uid,
        "code": code,
        "name": name,
        "buy_price": add_px,
        "qty": add_qty,
        "note": note,
        "created_at": now,
        "last_buy_date": buy_day,
        "entry_board": board,
        "lot_added": True,
    }


def trim_position(
    pid: int,
    qty: int,
    *,
    sell_price: float | None = None,
    trade_date: str | None = None,
    user_id: int | None = None,
    day_anchor: float | None = None,
    trade_fee: float | None = None,
) -> dict[str, Any] | None:
    """Reduce shares; keep qty=0 rows as closed-today until the next trade day.

    ``day_anchor`` should be 昨收 for overnight lots (buy price is used automatically
    when the lot was bought today). Session realized stores vs that anchor minus a
    flat sell fee on the first sell of the day — display still recomputes in decorate
    (buy fee is applied on the decorate path when bought today). Lot legs are trimmed
    FIFO so multi-fill cost history stays consistent.
    """
    from market_desk.config import TRADE_FEE_CNY

    sell = int(qty)
    if sell <= 0:
        return None
    day = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    fee = float(TRADE_FEE_CNY if trade_fee is None else trade_fee)
    if fee < 0:
        fee = 0.0
    with _connect() as conn:
        if user_id is not None:
            row = conn.execute(
                f"SELECT {_POS_SELECT} FROM positions WHERE id = ? AND user_id = ?",
                (pid, int(user_id)),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT {_POS_SELECT} FROM positions WHERE id = ?",
                (pid,),
            ).fetchone()
        if not row:
            return None
        hold = int(row["qty"] or 0)
        if hold <= 0:
            return _position_item(row)
        sell = min(sell, hold)
        left = hold - sell
        buy = float(row["buy_price"] or 0)
        px = float(sell_price) if sell_price is not None and float(sell_price) > 0 else buy
        buy_day = str(row["last_buy_date"] or row["created_at"] or "")[:10]
        if buy_day == day and buy > 0:
            anchor = buy
        elif day_anchor is not None and float(day_anchor) > 0:
            anchor = float(day_anchor)
        else:
            anchor = buy
        chunk_pnl = round((px - anchor) * sell, 2)
        chunk_notional = round(px * sell, 4)
        prev_day = str(row["last_sell_date"] or "")[:10]
        if prev_day == day:
            day_sold = int(row["day_sold_qty"] or 0) + sell
            day_pnl = round(float(row["day_realized_pnl"] or 0) + chunk_pnl, 2)
            day_notional = round(float(row["day_sell_notional"] or 0) + chunk_notional, 4)
        else:
            day_sold = sell
            day_pnl = round(chunk_pnl - fee, 2)
            day_notional = chunk_notional
        closed_date = day if left <= 0 else None
        lot_chunks = _fifo_trim_lots(conn, position_id=pid, qty=sell)
        new_buy = buy
        if left > 0:
            rem = conn.execute(
                """
                SELECT buy_price, qty FROM position_lots
                WHERE position_id = ? AND qty > 0
                """,
                (pid,),
            ).fetchall()
            if rem:
                cost = sum(float(r["buy_price"] or 0) * int(r["qty"] or 0) for r in rem)
                qsum = sum(int(r["qty"] or 0) for r in rem)
                if qsum > 0:
                    new_buy = round(cost / qsum, 4)
        conn.execute(
            """
            UPDATE positions
            SET qty = ?, buy_price = ?, closed_date = ?, last_sell_date = ?,
                last_sell_price = ?, day_sold_qty = ?, day_realized_pnl = ?,
                day_sell_notional = ?
            WHERE id = ?
            """,
            (
                left,
                new_buy,
                closed_date,
                day,
                round(px, 4),
                day_sold,
                day_pnl,
                day_notional,
                pid,
            ),
        )
        conn.commit()
        item = _position_item(
            {
                **dict(row),
                "qty": left,
                "buy_price": new_buy,
                "closed_date": closed_date,
                "last_sell_date": day,
                "last_sell_price": round(px, 4),
                "day_sold_qty": day_sold,
                "day_realized_pnl": day_pnl,
                "day_sell_notional": day_notional,
            }
        )
        item["trimmed"] = sell
        item["sell_price"] = round(px, 4)
        item["realized_chunk"] = chunk_pnl
        item["day_anchor"] = round(anchor, 4)
        item["trade_fee"] = fee if prev_day != day else 0.0
        item["lot_chunks"] = lot_chunks
        return item


def sync_position_buy_fill(
    code: str,
    name: str,
    *,
    old_price: float | None,
    old_qty: int | None,
    new_price: float,
    new_qty: int,
    user_id: int,
    note: str = "",
    entry_board: str = "",
) -> dict[str, Any]:
    """Correct an open book lot after a review buy fill is edited.

    Reverses the previous fill contribution (when present), then applies the
    new price/qty. If no open row exists, inserts the new lot.
    """
    uid = int(user_id)
    code = str(code or "").zfill(6)
    new_q = int(new_qty)
    new_p = float(new_price)
    if new_q <= 0 or new_p <= 0:
        raise ValueError("new buy fill must have positive price and qty")
    old_q = int(old_qty or 0)
    old_p = float(old_price or 0)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    buy_day = now[:10]
    board = str(entry_board or "").strip() or None
    with _connect() as conn:
        existing = conn.execute(
            f"""
            SELECT {_POS_SELECT}
            FROM positions
            WHERE code = ? AND user_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (code, uid),
        ).fetchone()
        if not existing or int(existing["qty"] or 0) <= 0:
            if existing and int(existing["qty"] or 0) <= 0:
                conn.execute(
                    """
                    UPDATE positions
                    SET name = ?, buy_price = ?, qty = ?, note = ?, last_buy_date = ?,
                        closed_date = NULL, created_at = ?, peak_price = ?, entry_board = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        name or existing["name"] or code,
                        new_p,
                        new_q,
                        note or existing["note"] or "",
                        buy_day,
                        now,
                        new_p,
                        board or (str(existing["entry_board"] or "").strip() or None),
                        int(existing["id"]),
                        uid,
                    ),
                )
                conn.commit()
                return {
                    "id": int(existing["id"]),
                    "code": code,
                    "name": name or existing["name"] or code,
                    "buy_price": new_p,
                    "qty": new_q,
                    "reopened": True,
                    "corrected": True,
                }
            cur = conn.execute(
                """
                INSERT INTO positions(
                    user_id, code, name, buy_price, qty, note, created_at, last_buy_date,
                    closed_date, last_sell_date, last_sell_price, day_sold_qty, day_realized_pnl,
                    peak_price, entry_board
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 0, ?, ?)
                """,
                (uid, code, name or code, new_p, new_q, note, now, buy_day, new_p, board),
            )
            conn.commit()
            return {
                "id": int(cur.lastrowid),
                "code": code,
                "name": name or code,
                "buy_price": new_p,
                "qty": new_q,
                "inserted": True,
                "corrected": True,
            }

        hold = int(existing["qty"] or 0)
        avg = float(existing["buy_price"] or 0)
        # Undo prior fill contribution when we still have those shares.
        if old_q > 0 and old_p > 0:
            undo = min(old_q, hold)
            if undo >= hold:
                hold = 0
                avg = 0.0
            else:
                cost = avg * hold - old_p * undo
                hold -= undo
                avg = (cost / hold) if hold > 0 else 0.0
                if avg < 0:
                    avg = 0.0
        if hold <= 0:
            hold = new_q
            avg = new_p
        else:
            cost = avg * hold + new_p * new_q
            hold = hold + new_q
            avg = cost / float(hold)
        merged_note = (existing["note"] or "") or note
        if note and existing["note"] and note not in str(existing["note"]):
            merged_note = f"{existing['note']}；{note}"
        conn.execute(
            """
            UPDATE positions
            SET name = ?, buy_price = ?, qty = ?, note = ?, last_buy_date = ?,
                closed_date = NULL, entry_board = COALESCE(?, entry_board)
            WHERE id = ? AND user_id = ?
            """,
            (
                name or existing["name"] or code,
                round(avg, 4),
                hold,
                merged_note,
                buy_day,
                board,
                int(existing["id"]),
                uid,
            ),
        )
        conn.commit()
        return {
            "id": int(existing["id"]),
            "code": code,
            "name": name or existing["name"] or code,
            "buy_price": round(avg, 4),
            "qty": hold,
            "corrected": True,
        }


def sync_position_sell_fill(
    code: str,
    *,
    old_price: float | None,
    old_qty: int | None,
    new_price: float,
    new_qty: int,
    user_id: int,
    trade_date: str | None = None,
    day_anchor: float | None = None,
    trade_fee: float | None = None,
) -> dict[str, Any]:
    """Correct a local book after a review sell fill is edited.

    Restores the previous sell qty, then re-trims at the new price/qty.
    Session realized uses ``day_anchor`` (昨收) for overnight lots.
    """
    from market_desk.config import TRADE_FEE_CNY

    uid = int(user_id)
    code = str(code or "").zfill(6)
    new_q = int(new_qty)
    new_p = float(new_price)
    if new_q <= 0 or new_p <= 0:
        raise ValueError("new sell fill must have positive price and qty")
    old_q = int(old_qty or 0)
    old_p = float(old_price or 0)
    day = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    fee = float(TRADE_FEE_CNY if trade_fee is None else trade_fee)
    if fee < 0:
        fee = 0.0
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {_POS_SELECT}
            FROM positions
            WHERE code = ? AND user_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (code, uid),
        ).fetchone()
        if not row:
            raise ValueError("no local position to sync this sell fill")
        hold = int(row["qty"] or 0)
        buy = float(row["buy_price"] or 0)
        buy_day = str(row["last_buy_date"] or row["created_at"] or "")[:10]
        if buy_day == day and buy > 0:
            anchor = buy
        elif day_anchor is not None and float(day_anchor) > 0:
            anchor = float(day_anchor)
        else:
            anchor = buy
        day_sold = int(row["day_sold_qty"] or 0)
        day_pnl = float(row["day_realized_pnl"] or 0)
        last_sell = str(row["last_sell_date"] or "")[:10]
        last_px = row["last_sell_price"]
        # Undo prior sell contribution on the same book day when possible.
        if old_q > 0:
            hold += old_q
            if last_sell == day:
                day_sold = max(0, day_sold - old_q)
                undo_px = old_p if old_p > 0 else float(last_px or buy or 0)
                day_pnl = round(day_pnl - (undo_px - anchor) * old_q, 2)
                if day_sold <= 0:
                    day_sold = 0
                    day_pnl = 0.0
                    last_sell = ""
                    last_px = None
        sell = min(new_q, hold)
        if sell <= 0:
            raise ValueError("position qty is zero after reversing prior sell")
        left = hold - sell
        chunk = round((new_p - anchor) * sell, 2)
        if last_sell == day or (not last_sell and day_sold > 0):
            day_sold = day_sold + sell
            day_pnl = round(day_pnl + chunk, 2)
        else:
            day_sold = sell
            day_pnl = round(chunk - fee, 2)
        closed_date = day if left <= 0 else None
        conn.execute(
            """
            UPDATE positions
            SET qty = ?, closed_date = ?, last_sell_date = ?, last_sell_price = ?,
                day_sold_qty = ?, day_realized_pnl = ?
            WHERE id = ? AND user_id = ?
            """,
            (left, closed_date, day, round(new_p, 4), day_sold, day_pnl, int(row["id"]), uid),
        )
        conn.commit()
        item = _position_item(
            {
                **dict(row),
                "qty": left,
                "closed_date": closed_date,
                "last_sell_date": day,
                "last_sell_price": round(new_p, 4),
                "day_sold_qty": day_sold,
                "day_realized_pnl": day_pnl,
            }
        )
        item["trimmed"] = sell
        item["sell_price"] = round(new_p, 4)
        item["realized_chunk"] = chunk
        item["corrected"] = True
        item["day_anchor"] = round(anchor, 4)
        return item


def delete_position(pid: int, *, user_id: int | None = None) -> bool:
    """Delete a position by id. Return True if a row was removed."""
    with _connect() as conn:
        if user_id is not None:
            cur = conn.execute(
                "DELETE FROM positions WHERE id = ? AND user_id = ?",
                (pid, int(user_id)),
            )
        else:
            cur = conn.execute("DELETE FROM positions WHERE id = ?", (pid,))
        conn.commit()
        return cur.rowcount > 0


def purge_stale_closed_positions(trade_date: str | None = None) -> dict[str, int]:
    """Drop fully closed rows from prior trade days; reset stale day-realized fields."""
    day = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    with _connect() as conn:
        cur_del = conn.execute(
            """
            DELETE FROM positions
            WHERE qty <= 0
              AND closed_date IS NOT NULL
              AND closed_date <> ''
              AND closed_date < ?
            """,
            (day,),
        )
        cur_reset = conn.execute(
            """
            UPDATE positions
            SET day_sold_qty = 0, day_realized_pnl = 0, day_sell_notional = 0
            WHERE qty > 0
              AND last_sell_date IS NOT NULL
              AND last_sell_date <> ''
              AND last_sell_date < ?
            """,
            (day,),
        )
        conn.commit()
        return {
            "deleted_closed": int(cur_del.rowcount or 0),
            "reset_day_pnl": int(cur_reset.rowcount or 0),
        }


def load_positions(*, user_id: int | None = None) -> list[dict[str, Any]]:
    """Return positions for one user (empty when user_id is None)."""
    if user_id is None:
        return []
    uid = int(user_id)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {_POS_SELECT}
            FROM positions
            WHERE user_id = ?
            ORDER BY
              CASE WHEN qty > 0 THEN 0 ELSE 1 END,
              id DESC
            """,
            (uid,),
        ).fetchall()
    return [_position_item(row) for row in rows]


def load_all_book_codes() -> list[str]:
    """Return distinct codes from every user's open/closed positions and watchlist.

    Used by the shared engine refresh to fetch marks without loading any
    personal rows into the public snapshot.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT code FROM (
              SELECT code FROM positions
              WHERE IFNULL(qty, 0) > 0 OR closed_date IS NOT NULL
              UNION
              SELECT code FROM watchlist
            )
            WHERE code IS NOT NULL AND TRIM(code) != ''
            """
        ).fetchall()
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        code = str(row["code"] or "").strip().zfill(6)
        if len(code) != 6 or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def position_buy_day(row: dict[str, Any] | None) -> str:
    """Return YYYY-MM-DD of the latest buy for a position row."""
    if not row:
        return ""
    day = str(row.get("last_buy_date") or "").strip()[:10]
    if day:
        return day
    return str(row.get("created_at") or "").strip()[:10]


def is_t1_locked(row: dict[str, Any] | None, trade_date: str | None) -> bool:
    """Return True when A-share T+1 blocks selling this position today."""
    buy_day = position_buy_day(row)
    day = str(trade_date or "").strip()[:10]
    return bool(buy_day and day and buy_day == day)
