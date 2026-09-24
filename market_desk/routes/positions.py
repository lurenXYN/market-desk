"""Position book, trims, execution diary and position LHB."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from market_desk.db import (
    add_exec_diary,
    add_position,
    delete_position,
    find_signal,
    load_exec_diary,
    load_positions,
    sync_sell_fill_from_trim,
    trim_position,
    update_signal_meta,
)
from market_desk.deps import current_member_required
from market_desk.engine import engine
from market_desk.filters import normalize_code
from market_desk.lhb import build_positions_lhb
from market_desk.verdict import quote_prev_close

router = APIRouter()


class PositionIn(BaseModel):
    """Payload for recording a local stock or ETF position."""

    code: str
    name: str = ""
    buy_price: float = Field(gt=0)
    qty: int = Field(gt=0)
    note: str = ""
    # Try to mark a same-day untraded buy signal as traded (soft desk sync).
    match_signal: bool = True


class TrimIn(BaseModel):
    """Payload for reducing share count on an existing position."""

    qty: int = Field(gt=0)
    sell_price: float | None = Field(default=None, gt=0)


def _advice_snapshot_for_code(
    code: str, *, source: str | None = None
) -> dict[str, Any]:
    """Capture live desk advice for one ticker into an exec-diary payload."""
    snap = engine.snapshot or {}
    code_n = normalize_code(code)
    verdict = snap.get("verdict") or {}
    ml = (verdict.get("mainline") or {}) if isinstance(verdict, dict) else {}
    out: dict[str, Any] = {
        "action": verdict.get("action"),
        "phase": snap.get("phase"),
        "mainline": ml.get("name"),
        "lifecycle": ml.get("lifecycle"),
        "trade_date": snap.get("trade_date"),
    }
    if source:
        out["source"] = str(source)
    rec = verdict.get("recommend") or {}
    primary = rec.get("primary") if isinstance(rec, dict) else None
    buy_hit: dict[str, Any] | None = None
    if isinstance(primary, dict) and normalize_code(primary.get("code")) == code_n:
        buy_hit = primary
    if buy_hit is None:
        for bucket in (
            rec.get("items") or [],
            (snap.get("side_recommend") or {}).get("items") or [],
            (snap.get("link_recommend") or {}).get("items") or [],
            (snap.get("trial_recommend") or {}).get("items") or [],
            (snap.get("indep_recommend") or {}).get("items") or [],
        ):
            for it in bucket:
                if isinstance(it, dict) and normalize_code(it.get("code")) == code_n:
                    buy_hit = it
                    break
            if buy_hit is not None:
                break
    if buy_hit is not None:
        out["buy"] = {
            "buy_price": buy_hit.get("buy_price") or buy_hit.get("last"),
            "price": buy_hit.get("buy_price") or buy_hit.get("price") or buy_hit.get("last"),
            "wait_price": buy_hit.get("wait_price") or buy_hit.get("buy_price"),
            "chase_price": buy_hit.get("chase_price"),
            "stop_price": buy_hit.get("stop_price"),
            "kind": buy_hit.get("kind") or "stock",
            "ready": buy_hit.get("ready"),
            "role_label": buy_hit.get("role_label"),
            "reason": (str(buy_hit.get("reason") or ""))[:160],
        }
    for it in ((snap.get("sell_advice") or {}).get("items") or []):
        if not isinstance(it, dict):
            continue
        if normalize_code(it.get("code")) != code_n:
            continue
        out["sell"] = {
            "ready": it.get("ready"),
            "urgency": it.get("urgency"),
            "exit_mode": it.get("exit_mode"),
            "sell_price": it.get("sell_price") or it.get("last"),
            "stop_price": it.get("stop_price"),
            "next_action": it.get("next_action"),
            "next_action_zh": it.get("next_action_zh"),
            "reason": (str(it.get("reason") or ""))[:160],
        }
        break
    return out


def _log_exec_diary(
    *,
    user_id: int,
    side: str,
    code: str,
    name: str = "",
    qty: int | None = None,
    price: float | None = None,
    note: str = "",
    trade_date: str | None = None,
    source: str = "manual",
) -> dict[str, Any] | None:
    """Persist one exec diary row; swallow errors so book ops stay primary."""
    try:
        return add_exec_diary(
            user_id=int(user_id),
            side=side,
            code=normalize_code(code),
            name=name or "",
            qty=qty,
            price=price,
            advice=_advice_snapshot_for_code(code, source=source),
            note=note or "",
            trade_date=trade_date,
        )
    except Exception:
        return None


def _try_match_buy_signal(
    *,
    user_id: int,
    code: str,
    fill_price: float,
    fill_qty: int,
    note: str = "",
) -> dict[str, Any] | None:
    """Mark a same-day untraded buy signal as traded when the book matches."""
    day = str((engine.snapshot or {}).get("trade_date") or "")[:10]
    if not day:
        from datetime import datetime

        day = datetime.now().strftime("%Y-%m-%d")
    row = find_signal(day, normalize_code(code), "buy")
    if not row:
        return None
    sid = int(row.get("id") or 0)
    if sid <= 0:
        return None
    # Skip if this user already marked traded.
    try:
        from market_desk.db import load_signal_user_meta_map

        um = (load_signal_user_meta_map(int(user_id)) or {}).get(sid) or {}
        if int(um.get("traded") or 0):
            return {"id": sid, "matched": False, "reason": "already_traded"}
    except Exception:
        pass
    if int(row.get("traded") or 0) and int(row.get("owner_user_id") or 0) == int(user_id):
        return {"id": sid, "matched": False, "reason": "already_traded"}
    ok = update_signal_meta(
        sid,
        traded=1,
        skipped=0,
        fill_price=float(fill_price),
        fill_qty=int(fill_qty),
        note=(note or "仓位记账匹配")[:80] or None,
        user_id=int(user_id),
    )
    if not ok:
        return None
    try:
        engine.clear_review_cache()
    except Exception:
        pass
    return {
        "id": sid,
        "matched": True,
        "code": normalize_code(code),
        "trade_date": day,
        "fill_price": float(fill_price),
        "fill_qty": int(fill_qty),
    }


@router.get("/api/positions")
def list_positions(user: dict = Depends(current_member_required)) -> dict:
    """Return recorded positions with the last known marks."""
    snap = engine.snapshot_for_user(int(user["id"]))
    return {
        "ok": True,
        "positions": snap.get("positions") or [],
        "summary": snap.get("position_summary"),
    }


@router.get("/api/positions/stats")
def positions_stats(
    days: int = Query(default=20, ge=5, le=60),
    user: dict = Depends(current_member_required),
) -> dict:
    """Return personal day/week win-rate calendar (display-only)."""
    from market_desk.personal_stats import build_personal_pnl_calendar

    snap = engine.snapshot_for_user(int(user["id"]))
    return build_personal_pnl_calendar(
        user_id=int(user["id"]),
        days=days,
        trade_date=str(snap.get("trade_date") or "")[:10] or None,
    )


@router.get("/api/exec-diary")
def list_exec_diary(
    trade_date: str | None = Query(default=None),
    limit: int = Query(default=40, ge=1, le=120),
    user: dict = Depends(current_member_required),
) -> dict:
    """Return recent buy/sell execution diary rows for the caller."""
    day = str(trade_date or "").strip()[:10] or None
    rows = load_exec_diary(user_id=int(user["id"]), trade_date=day, limit=limit)
    return {"ok": True, "items": rows, "trade_date": day}


@router.get("/api/positions/lhb")
async def positions_lhb(
    force: bool = Query(default=False),
    user: dict = Depends(current_member_required),
) -> dict:
    """Return dragon-tiger seats for the caller's open positions (view-only)."""
    snap = engine.snapshot_for_user(int(user["id"]))
    payload = await build_positions_lhb(snap.get("positions") or [], force=force)
    try:
        from market_desk.db import load_setting, save_setting
        from market_desk.notify import lhb_seat_edge_alerts, push_serverchan_alerts

        fp_key = f"lhb_seat_edge:{int(user['id'])}"
        prev = load_setting(fp_key) or {}
        if not isinstance(prev, dict):
            prev = {}
        alerts, next_fp = lhb_seat_edge_alerts(payload.get("items") or [], prev_fp=prev)
        save_setting(fp_key, next_fp)
        if alerts:
            push_serverchan_alerts(alerts, snap)
            payload["pushed_alerts"] = len(alerts)
    except Exception:
        pass
    return payload


@router.post("/api/positions")
def create_position(body: PositionIn, user: dict = Depends(current_member_required)) -> dict:
    """Record a buy: code, price and share count."""
    code = normalize_code(body.code)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "code must be a 6-digit ticker")
    name = (body.name or "").strip()
    entry_board = str(
        (((engine.snapshot.get("verdict") or {}).get("mainline") or {}).get("name")) or ""
    )
    booked = add_position(
        code,
        name,
        float(body.buy_price),
        int(body.qty),
        body.note.strip(),
        entry_board=entry_board,
        user_id=int(user["id"]),
    )
    diary = _log_exec_diary(
        user_id=int(user["id"]),
        side="buy",
        code=code,
        name=name or str((booked or {}).get("name") or ""),
        qty=int(body.qty),
        price=float(body.buy_price),
        note=body.note.strip() or "仓位记账",
        source="manual",
    )
    matched = None
    if body.match_signal:
        matched = _try_match_buy_signal(
            user_id=int(user["id"]),
            code=code,
            fill_price=float(body.buy_price),
            fill_qty=int(body.qty),
            note=body.note.strip() or "仓位记账匹配",
        )
    snap = engine.snapshot_for_user(int(user["id"]))
    return {
        "ok": True,
        "positions": snap.get("positions") or [],
        "summary": snap.get("position_summary"),
        "diary": diary,
        "matched_signal": matched,
    }


@router.delete("/api/positions/{pid}")
def remove_position(pid: int, user: dict = Depends(current_member_required)) -> dict:
    """Delete a recorded position."""
    if not delete_position(pid, user_id=int(user["id"])):
        raise HTTPException(404, "position not found")
    snap = engine.snapshot_for_user(int(user["id"]))
    return {
        "ok": True,
        "positions": snap.get("positions") or [],
        "summary": snap.get("position_summary"),
    }


def _day_anchor_for_code(code: str) -> float | None:
    """Resolve 昨收 for a ticker from the live book-quote cache."""
    c = normalize_code(code)
    if not c:
        return None
    qmap = getattr(engine, "_book_quotes", None) or {}
    q = qmap.get(c) or {}
    if not q:
        # Fall back to any decorated open position still on the snapshot.
        for row in (engine.snapshot or {}).get("positions") or []:
            if normalize_code(row.get("code")) == c and row.get("prev") not in (None, ""):
                try:
                    return float(row["prev"])
                except (TypeError, ValueError):
                    break
        return None
    return quote_prev_close(q)


@router.post("/api/positions/{pid}/trim")
def trim_position_api(
    pid: int, body: TrimIn, user: dict = Depends(current_member_required)
) -> dict:
    """Sell/reduce shares on a recorded position (local book only).

    When a same-day sell signal exists in review, auto-mark it traded and fill
    the sale price/qty (desk clear/half stays in sync with the review table).
    """
    trade_day = str(engine.snapshot.get("trade_date") or "")
    if len(trade_day) == 8:
        trade_day = f"{trade_day[:4]}-{trade_day[4:6]}-{trade_day[6:8]}"
    else:
        trade_day = trade_day[:10] or None
    # Peek code for 昨收 before trim (row may close).
    pre = None
    for row in load_positions(user_id=int(user["id"])):
        if int(row.get("id") or 0) == int(pid):
            pre = row
            break
    anchor = _day_anchor_for_code(str((pre or {}).get("code") or ""))
    row = trim_position(
        pid,
        int(body.qty),
        sell_price=body.sell_price,
        trade_date=trade_day,
        user_id=int(user["id"]),
        day_anchor=anchor,
    )
    if row is None:
        raise HTTPException(404, "position not found")
    left = int(row.get("qty") or 0)
    trimmed = int(row.get("trimmed") or body.qty or 0)
    note = "作战台清仓" if left <= 0 else "作战台减仓"
    signal_fill = sync_sell_fill_from_trim(
        code=str(row.get("code") or ""),
        trade_date=trade_day,
        fill_price=row.get("sell_price") if row.get("sell_price") is not None else body.sell_price,
        fill_qty=trimmed,
        note=note,
        user_id=int(user["id"]),
    )
    diary = _log_exec_diary(
        user_id=int(user["id"]),
        side="clear" if left <= 0 else "half",
        code=str(row.get("code") or ""),
        name=str(row.get("name") or ""),
        qty=trimmed,
        price=(
            float(row["sell_price"])
            if row.get("sell_price") is not None
            else (float(body.sell_price) if body.sell_price is not None else None)
        ),
        note=note,
        trade_date=trade_day,
    )
    engine.clear_review_cache()
    # Soft reputation write-back from realized chunk PnL%.
    feedback = None
    try:
        from market_desk.adapt import record_trade_feedback

        buy = float(row.get("buy_price") or 0)
        sell_px = float(row.get("sell_price") or 0)
        pnl_pct = ((sell_px / buy) - 1.0) * 100.0 if buy > 0 and sell_px > 0 else None
        feedback = record_trade_feedback(
            code=str(row.get("code") or ""),
            name=str(row.get("name") or ""),
            pnl_pct=pnl_pct,
            entry_board=str(row.get("entry_board") or ""),
            trade_date=trade_day,
        )
        # Bust day cache so next refresh sees new heat/rep.
        from market_desk import adapt as adapt_mod

        adapt_mod._ADAPT_CACHE["day"] = ""
    except Exception:
        feedback = None
    snap = engine.snapshot_for_user(int(user["id"]))
    return {
        "ok": True,
        "trimmed": row,
        "signal_fill": signal_fill,
        "feedback": feedback,
        "diary": diary,
        "positions": snap.get("positions") or [],
        "summary": snap.get("position_summary"),
    }
