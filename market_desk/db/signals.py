"""Review signals, outcomes, per-user signal meta and review digests."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


def upsert_signal(row: dict[str, Any]) -> None:
    """Insert or refresh a same-day signal keyed by date + code + type + owner.

    ``owner_user_id``: ``0`` = shared paper (buys); positive = personal sells.
    ``signaled_at`` and ``price`` are kept from the first insert so the review
    panel shows the first watch time and the first suggested entry price.
    Payload is merged; first non-empty ``board_names`` is locked so later hot-board
    rotations cannot wipe the所属板块 column.
    """
    trade_date = row.get("trade_date")
    code = row.get("code")
    signal_type = row.get("signal_type")
    try:
        owner_id = int(row.get("owner_user_id") or 0)
    except (TypeError, ValueError):
        owner_id = 0
    incoming = dict(row.get("payload") or {})
    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT signaled_at, price, payload
            FROM signals
            WHERE trade_date = ? AND code = ? AND signal_type = ? AND owner_user_id = ?
            """,
            (trade_date, code, signal_type, owner_id),
        ).fetchone()
        old_payload: dict[str, Any] = {}
        if existing and existing["payload"]:
            try:
                raw = json.loads(existing["payload"])
                if isinstance(raw, dict):
                    old_payload = raw
            except json.JSONDecodeError:
                old_payload = {}
        merged = {**old_payload, **incoming}
        old_boards = [
            str(x).strip()
            for x in (old_payload.get("board_names") or [])
            if str(x).strip()
        ]
        new_boards = [
            str(x).strip()
            for x in (incoming.get("board_names") or [])
            if str(x).strip()
        ]
        if old_boards and not new_boards:
            merged["board_names"] = old_boards
            if old_payload.get("vs_mainline") is not None and not incoming.get("vs_mainline"):
                merged["vs_mainline"] = old_payload.get("vs_mainline")
            if "board_match" in old_payload and "board_match" not in incoming:
                merged["board_match"] = old_payload.get("board_match")
            if old_payload.get("source_board") and not incoming.get("source_board"):
                merged["source_board"] = old_payload.get("source_board")
            if old_payload.get("vs_source") is not None and not incoming.get("vs_source"):
                merged["vs_source"] = old_payload.get("vs_source")
            if "source_match" in old_payload and "source_match" not in incoming:
                merged["source_match"] = old_payload.get("source_match")
        elif old_boards and new_boards and old_boards != new_boards:
            # Prefer richer first capture; only replace when newly resolved from empty.
            pass
        if old_payload.get("source_board") and not incoming.get("source_board"):
            merged["source_board"] = old_payload.get("source_board")

        # Gate evolution: keep first_ready / fail history across same-day upserts.
        ready_now = 1 if row.get("ready") else 0
        signaled = str(row.get("signaled_at") or "")
        if ready_now:
            merged["ever_ready"] = True
            if not old_payload.get("first_ready_at"):
                merged["first_ready_at"] = signaled or old_payload.get("first_ready_at")
            else:
                merged["first_ready_at"] = old_payload.get("first_ready_at")
        else:
            if old_payload.get("ever_ready"):
                merged["ever_ready"] = True
            if old_payload.get("first_ready_at"):
                merged["first_ready_at"] = old_payload.get("first_ready_at")
        new_fails = [
            str(x).strip()
            for x in (incoming.get("confirm_fail") or [])
            if str(x).strip()
        ]
        hist = [
            str(x).strip()
            for x in (old_payload.get("confirm_fail_hist") or [])
            if str(x).strip()
        ]
        for flag in new_fails:
            if flag not in hist:
                hist.append(flag)
        if hist:
            merged["confirm_fail_hist"] = hist[:24]
        if new_fails:
            merged["final_fail"] = new_fails
        elif old_payload.get("final_fail") and not ready_now:
            merged["final_fail"] = old_payload.get("final_fail")
        elif old_payload.get("final_fail") and ready_now:
            # Recovered to ready: keep last gated fail for attribution.
            merged["final_fail"] = old_payload.get("final_fail")

        now_payload = json.dumps(merged, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO signals(
                trade_date, signaled_at, signal_type, action, phase, mainline,
                code, name, kind, price, last, ready, payload, owner_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, code, signal_type, owner_user_id) DO UPDATE SET
                action = excluded.action,
                phase = excluded.phase,
                mainline = excluded.mainline,
                name = excluded.name,
                kind = excluded.kind,
                last = excluded.last,
                ready = excluded.ready,
                payload = excluded.payload
            """,
            (
                trade_date,
                row.get("signaled_at") if not existing else existing["signaled_at"],
                signal_type,
                row.get("action"),
                row.get("phase"),
                row.get("mainline"),
                code,
                row.get("name"),
                row.get("kind"),
                row.get("price") if not existing else existing["price"],
                row.get("last"),
                int(row.get("ready") or 0),
                now_payload,
                owner_id,
            ),
        )
        conn.commit()


def load_signal(signal_id: int) -> dict[str, Any] | None:
    """Return one signal row by id, or None."""
    with _connect() as conn:
        row = conn.execute(
            _SIGNAL_SELECT + " WHERE id = ?",
            (int(signal_id),),
        ).fetchone()
    if not row:
        return None
    items = _decode_signal_rows([row])
    return items[0] if items else None


_SIGNAL_SELECT = """
    SELECT id, trade_date, signaled_at, signal_type, action, phase, mainline,
           code, name, kind, price, last, ready, payload,
           outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
           outcome_label, outcome_checked_at, note, skipped, traded,
           fill_price, fill_qty, COALESCE(owner_user_id, 0) AS owner_user_id
    FROM signals
"""


def _decode_signal_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Normalize SQLite signal rows into API-ready dicts."""
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.get("payload")
        if isinstance(raw, str) and raw:
            try:
                item["payload"] = json.loads(raw)
            except json.JSONDecodeError:
                item["payload"] = {}
        else:
            item["payload"] = {}
        item["skipped"] = int(item.get("skipped") or 0)
        item["traded"] = int(item.get("traded") or 0)
        try:
            item["owner_user_id"] = int(item.get("owner_user_id") or 0)
        except (TypeError, ValueError):
            item["owner_user_id"] = 0
        out.append(item)
    return out


def filter_signals_for_viewer(
    rows: list[dict[str, Any]] | None,
    user_id: int | None,
) -> list[dict[str, Any]]:
    """Keep shared buys for everyone; keep sells only for the owning account.

    Legacy sells with ``owner_user_id=0`` stay visible to logged-in users.
    """
    out: list[dict[str, Any]] = []
    uid = None
    try:
        if user_id is not None:
            uid = int(user_id)
    except (TypeError, ValueError):
        uid = None
    for raw in rows or []:
        item = dict(raw)
        st = str(item.get("signal_type") or "")
        try:
            owner = int(item.get("owner_user_id") or 0)
        except (TypeError, ValueError):
            owner = 0
        if st == "sell":
            if owner == 0:
                if uid is None:
                    continue
                out.append(item)
                continue
            if uid is None or owner != uid:
                continue
            out.append(item)
            continue
        if owner not in (0,):
            if uid is None or owner != uid:
                continue
        out.append(item)
    return out


def load_signals(limit: int = 60) -> list[dict[str, Any]]:
    """Return recent signals, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            _SIGNAL_SELECT
            + """
            ORDER BY trade_date DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return _decode_signal_rows(rows)


def load_signals_for_code(code: str, limit: int = 120) -> list[dict[str, Any]]:
    """Return signal history for one ticker, newest first."""
    from market_desk.filters import normalize_code

    c = normalize_code(code)
    if not c:
        return []
    lim = max(1, min(int(limit or 120), 300))
    with _connect() as conn:
        rows = conn.execute(
            _SIGNAL_SELECT
            + """
            WHERE code = ?
            ORDER BY trade_date DESC, id DESC
            LIMIT ?
            """,
            (c, lim),
        ).fetchall()
    return _decode_signal_rows(rows)


def load_signals_for_date(trade_date: str) -> list[dict[str, Any]]:
    """Return all signals for one trade date, newest first."""
    day = str(trade_date or "").strip()[:10]
    if not day:
        return []
    with _connect() as conn:
        rows = conn.execute(
            _SIGNAL_SELECT
            + """
            WHERE trade_date = ?
            ORDER BY id DESC
            """,
            (day,),
        ).fetchall()
    return _decode_signal_rows(rows)


def find_signal(
    trade_date: str,
    code: str,
    signal_type: str,
) -> dict[str, Any] | None:
    """Return the unique same-day signal for code + type, or None."""
    day = str(trade_date or "").strip()[:10]
    c = str(code or "").strip().zfill(6)
    typ = str(signal_type or "").strip().lower()
    if not day or len(c) != 6 or not c.isdigit() or typ not in ("buy", "sell"):
        return None
    with _connect() as conn:
        row = conn.execute(
            _SIGNAL_SELECT
            + """
            WHERE trade_date = ? AND code = ? AND signal_type = ?
            LIMIT 1
            """,
            (day, c, typ),
        ).fetchone()
    rows = _decode_signal_rows([row] if row else [])
    return rows[0] if rows else None


def sync_sell_fill_from_trim(
    *,
    code: str,
    trade_date: str | None,
    fill_price: float | None,
    fill_qty: int,
    note: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    """Mark today's sell signal traded and fill price/qty after a desk trim.

    No-op when there is no same-day sell signal yet. Returns the updated signal
    row summary, or None.
    """
    day = str(trade_date or "").strip()[:10]
    if not day:
        day = datetime.now().strftime("%Y-%m-%d")
    c = str(code or "").strip().zfill(6)
    qty = int(fill_qty or 0)
    if len(c) != 6 or not c.isdigit() or qty <= 0:
        return None
    row = find_signal(day, c, "sell")
    if not row:
        return None
    # Prefer this user's prior fill qty when accumulating partials.
    if user_id is not None:
        meta_map = load_signal_user_meta_map(int(user_id))
        um = meta_map.get(int(row["id"])) or {}
        prev_traded = int(um.get("traded") or 0)
        prev_qty = int(um.get("fill_qty") or 0) if prev_traded else 0
        prev_note = str(um.get("note") or "").strip()
    else:
        prev_traded = int(row.get("traded") or 0)
        prev_qty = int(row.get("fill_qty") or 0) if prev_traded else 0
        prev_note = str(row.get("note") or "").strip()
    px = float(fill_price) if fill_price is not None and float(fill_price) > 0 else None
    if px is None:
        try:
            px = float(row.get("price") or row.get("last") or 0) or None
        except (TypeError, ValueError):
            px = None
    note_to_set = None
    if not prev_note and note:
        note_to_set = note
    elif not prev_traded and note:
        note_to_set = note
    total_qty = prev_qty + qty if prev_qty > 0 else qty
    ok = update_signal_meta(
        int(row["id"]),
        traded=1,
        skipped=0,
        note=note_to_set,
        fill_price=px,
        fill_qty=total_qty,
        user_id=user_id,
    )
    if not ok:
        return None
    return {
        "id": int(row["id"]),
        "code": c,
        "trade_date": day,
        "fill_price": px,
        "fill_qty": total_qty,
    }


def list_signal_trade_dates(limit: int = 40) -> list[str]:
    """Return distinct trade dates that have signals, newest first.

    Skip sentinel / far-future placeholders (e.g. 2099-01-01 test rows) so the
    review day chips stay usable.
    """
    calendar_today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM signals
            WHERE trade_date IS NOT NULL AND trade_date != ''
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (max(1, int(limit) * 3),),
        ).fetchall()
    out: list[str] = []
    for r in rows:
        d = str(r["trade_date"] or "").strip()[:10]
        if len(d) != 10 or d[4] != "-" or d[7] != "-":
            continue
        # Drop far-future / sentinel test days (chip would show as 01-01).
        if d > calendar_today and d[:4] >= "2090":
            continue
        if d.startswith("2099"):
            continue
        out.append(d)
        if len(out) >= max(1, int(limit)):
            break
    return out


def purge_sentinel_signal_dates() -> int:
    """Delete far-future / placeholder signal days (e.g. 2099-01-01 test rows)."""
    with _connect() as conn:
        cur = conn.execute(
            """
            DELETE FROM signals
            WHERE trade_date LIKE '2099%'
               OR (length(trade_date) = 10 AND trade_date > date('now', '+30 days'))
            """
        )
        n = int(cur.rowcount or 0)
        conn.commit()
    return n


def load_unscored_signals(
    before_date: str,
    limit: int = 80,
    *,
    labeled_since: str | None = None,
) -> list[dict[str, Any]]:
    """Return signals before a date that still lack an outcome label.

    With ``labeled_since``, also return already-labeled rows from that date on so
    callers can refresh outcomes whose 3-session horizon was still open.
    """
    label_clause = "(outcome_label IS NULL OR outcome_label = '')"
    params: tuple[Any, ...] = (before_date, limit)
    if labeled_since:
        label_clause = f"({label_clause} OR trade_date >= ?)"
        params = (before_date, labeled_since, limit)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, trade_date, signaled_at, signal_type, action, phase, mainline,
                   code, name, kind, price, last, ready, payload,
                   outcome_day1_pct, outcome_day3_pct, outcome_mfe_pct, outcome_mae_pct,
                   outcome_label, outcome_checked_at, note, skipped, traded,
                   fill_price, fill_qty
            FROM signals
            WHERE trade_date < ?
              AND {label_clause}
              AND IFNULL(skipped, 0) = 0
            ORDER BY trade_date ASC, id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.get("payload")
        if isinstance(raw, str) and raw:
            try:
                item["payload"] = json.loads(raw)
            except json.JSONDecodeError:
                item["payload"] = {}
        else:
            item["payload"] = {}
        item["skipped"] = int(item.get("skipped") or 0)
        item["traded"] = int(item.get("traded") or 0)
        out.append(item)
    return out


def mark_signal_outcome(signal_id: int, outcome: dict[str, Any]) -> bool:
    """Persist scored outcome fields for one signal row."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE signals SET
                outcome_day1_pct = ?,
                outcome_day3_pct = ?,
                outcome_mfe_pct = ?,
                outcome_mae_pct = ?,
                outcome_label = ?,
                outcome_checked_at = ?
            WHERE id = ?
            """,
            (
                outcome.get("outcome_day1_pct"),
                outcome.get("outcome_day3_pct"),
                outcome.get("outcome_mfe_pct"),
                outcome.get("outcome_mae_pct"),
                outcome.get("outcome_label"),
                outcome.get("outcome_checked_at"),
                signal_id,
            ),
        )
        conn.commit()
        return cur.rowcount > 0


def update_signal_meta(
    signal_id: int,
    *,
    skipped: int | None = None,
    traded: int | None = None,
    note: str | None = None,
    fill_price: float | None = None,
    fill_qty: int | None = None,
    user_id: int | None = None,
) -> bool:
    """Update per-user review flags / fill fields for one signal.

    When ``user_id`` is omitted, falls back to legacy columns on ``signals``
    (single-user / migration path only).
    """
    if user_id is not None:
        return upsert_signal_user_meta(
            int(user_id),
            int(signal_id),
            skipped=skipped,
            traded=traded,
            note=note,
            fill_price=fill_price,
            fill_qty=fill_qty,
        )
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, note, skipped, traded, fill_price, fill_qty FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        if not row:
            return False
        new_skipped = int(row["skipped"] or 0) if skipped is None else int(skipped)
        new_traded = int(row["traded"] or 0) if traded is None else int(traded)
        if traded is not None and int(traded):
            new_skipped = 0
            new_traded = 1
        if skipped is not None and int(skipped):
            new_traded = 0
            new_skipped = 1
        new_note = row["note"] if note is None else note
        new_fill_px = row["fill_price"] if fill_price is None else float(fill_price)
        new_fill_qty = row["fill_qty"] if fill_qty is None else int(fill_qty)
        cur = conn.execute(
            """
            UPDATE signals
            SET skipped = ?, traded = ?, note = ?, fill_price = ?, fill_qty = ?
            WHERE id = ?
            """,
            (new_skipped, new_traded, new_note, new_fill_px, new_fill_qty, signal_id),
        )
        conn.commit()
        return cur.rowcount > 0


def upsert_signal_user_meta(
    user_id: int,
    signal_id: int,
    *,
    skipped: int | None = None,
    traded: int | None = None,
    note: str | None = None,
    fill_price: float | None = None,
    fill_qty: int | None = None,
) -> bool:
    """Insert or patch one user's traded / fill annotation for a signal."""
    uid = int(user_id)
    sid = int(signal_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        exists = conn.execute(
            "SELECT id FROM signals WHERE id = ?", (sid,)
        ).fetchone()
        if not exists:
            return False
        row = conn.execute(
            """
            SELECT skipped, traded, note, fill_price, fill_qty
            FROM signal_user_meta
            WHERE user_id = ? AND signal_id = ?
            """,
            (uid, sid),
        ).fetchone()
        if row:
            new_skipped = int(row["skipped"] or 0) if skipped is None else int(skipped)
            new_traded = int(row["traded"] or 0) if traded is None else int(traded)
            new_note = row["note"] if note is None else note
            new_fill_px = row["fill_price"] if fill_price is None else float(fill_price)
            new_fill_qty = row["fill_qty"] if fill_qty is None else int(fill_qty)
        else:
            new_skipped = 0 if skipped is None else int(skipped)
            new_traded = 0 if traded is None else int(traded)
            new_note = note
            new_fill_px = None if fill_price is None else float(fill_price)
            new_fill_qty = None if fill_qty is None else int(fill_qty)
        if traded is not None and int(traded):
            new_skipped = 0
            new_traded = 1
        if skipped is not None and int(skipped):
            new_traded = 0
            new_skipped = 1
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
                new_skipped,
                new_traded,
                new_note,
                new_fill_px,
                new_fill_qty,
                now,
            ),
        )
        conn.commit()
    return True


def load_signal_user_meta_map(user_id: int) -> dict[int, dict[str, Any]]:
    """Return ``signal_id -> meta`` for one user."""
    uid = int(user_id)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT signal_id, skipped, traded, note, fill_price, fill_qty, updated_at
            FROM signal_user_meta
            WHERE user_id = ?
            """,
            (uid,),
        ).fetchall()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        out[int(row["signal_id"])] = dict(row)
    return out


def apply_signal_user_meta(
    rows: list[dict[str, Any]] | None,
    user_id: int | None,
) -> list[dict[str, Any]]:
    """Overlay per-user traded / fill fields onto shared signal rows.

    Without ``user_id``, clear personal flags so anonymous clients never see
    another user's fills.
    """
    src = list(rows or [])
    if user_id is None:
        cleaned: list[dict[str, Any]] = []
        for raw in src:
            item = dict(raw)
            item["skipped"] = 0
            item["traded"] = 0
            item["note"] = None
            item["fill_price"] = None
            item["fill_qty"] = None
            cleaned.append(item)
        return cleaned
    meta = load_signal_user_meta_map(int(user_id))
    out: list[dict[str, Any]] = []
    for raw in src:
        item = dict(raw)
        # Start from blank personal fields; only show this user's annotations.
        item["skipped"] = 0
        item["traded"] = 0
        item["note"] = None
        item["fill_price"] = None
        item["fill_qty"] = None
        try:
            sid = int(item.get("id") or 0)
        except (TypeError, ValueError):
            sid = 0
        hit = meta.get(sid)
        if hit:
            item["skipped"] = int(hit.get("skipped") or 0)
            item["traded"] = int(hit.get("traded") or 0)
            item["note"] = hit.get("note")
            item["fill_price"] = hit.get("fill_price")
            item["fill_qty"] = hit.get("fill_qty")
        out.append(item)
    return out


def save_review_digest(trade_date: str, payload: dict[str, Any]) -> None:
    """Upsert one trade-date review digest for historical charts."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO review_digest(trade_date, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (trade_date, json.dumps(payload, ensure_ascii=False), now),
        )
        conn.commit()


def load_review_digests(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent review digests, oldest-first for charting."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, payload, updated_at
            FROM review_digest
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in reversed(list(rows)):
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["trade_date"] = row["trade_date"]
        payload["updated_at"] = row["updated_at"]
        out.append(payload)
    return out


def delete_signal(signal_id: int) -> bool:
    """Hard-delete one review signal row."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM signals WHERE id = ?", (signal_id,))
        conn.commit()
        return cur.rowcount > 0
