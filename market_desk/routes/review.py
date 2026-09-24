"""Review signals: listing, annotation, trade marking and deletion."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from market_desk.auth import is_guest
from market_desk.db import (
    add_position,
    apply_signal_user_meta,
    delete_signal,
    is_t1_locked,
    load_positions,
    load_signal,
    load_signals,
    sync_position_buy_fill,
    sync_position_sell_fill,
    trim_position,
    update_signal_meta,
)
from market_desk.deps import (
    current_admin_required,
    current_member_required,
    current_user_required,
)
from market_desk.engine import engine
from market_desk.filters import normalize_code
from market_desk.lots import clear_sell_qty, half_sell_qty
from market_desk.review import is_buy_signal, is_sell_signal
from market_desk.routes.positions import _day_anchor_for_code, _log_exec_diary

router = APIRouter()


class SignalMetaIn(BaseModel):
    """User annotation for a logged buy/sell signal."""

    skipped: bool | None = None
    traded: bool | None = None
    note: str | None = None
    fill_price: float | None = Field(default=None, gt=0)
    fill_qty: int | None = Field(default=None, gt=0)
    # When editing fill_price/fill_qty, also rewrite the matching local book lot.
    sync_book: bool = False


class SignalTradeIn(BaseModel):
    """Mark a signal traded and optionally mirror it into the position book."""

    book: bool = True
    qty: int | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    note: str | None = None
    # half = lot-rounded 50%; clear = full exit; ignored for buys.
    sell_mode: str | None = None


@router.get("/api/review")
async def review(
    date: str | None = Query(default=None),
    vs_ml: str | None = Query(default=None, description="live or day"),
    oc: str | None = Query(default=None, description="classic or same_day_plan"),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return one trade-date's signals with scored outcomes for the review tab."""
    # Guest sees paper signals only (no fill overlays); members get per-user meta.
    uid = None if is_guest(user) else int(user["id"])
    return await engine.build_review(
        view_date=date,
        vs_mainline_mode=vs_ml,
        user_id=uid,
        outcome_standard=oc,
    )


@router.get("/api/review/zt-ytd")
async def review_zt_ytd(
    date: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return calendar-year limit-up counts for the review day's equities."""
    del user
    return await engine.build_review_zt_ytd(view_date=date)


@router.get("/api/review/trends")
async def review_trends(
    date: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return daily up/down/sideways classifications for review-day tickers."""
    del user
    return await engine.build_review_trends(view_date=date)


@router.get("/api/review/history/{code}")
async def review_code_history(
    code: str,
    limit: int = Query(default=120, ge=1, le=300),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return signal history for one ticker (dates, plan prices, outcomes)."""
    c = normalize_code(code)
    if len(c) != 6 or not c.isdigit():
        raise HTTPException(400, "code must be a 6-digit ticker")
    uid = None if is_guest(user) else int(user["id"])
    return await engine.build_review_code_history(c, user_id=uid, limit=limit)


@router.post("/api/review/{sid}")
def annotate_signal(
    sid: int,
    body: SignalMetaIn,
    user: dict = Depends(current_member_required),
) -> dict:
    """Mark a signal as traded / not traded, or attach a note / fill.

    When ``sync_book`` is set with a fill edit, reverse the prior fill on the
    local position book and apply the new price/qty (buy average or sell trim).
    """
    uid = int(user["id"])
    row = load_signal(sid)
    if not row:
        raise HTTPException(404, "signal not found")
    annotated = apply_signal_user_meta([row], uid)[0]
    old_px = annotated.get("fill_price")
    old_qty = annotated.get("fill_qty")
    try:
        old_px_f = float(old_px) if old_px not in (None, "") else None
    except (TypeError, ValueError):
        old_px_f = None
    try:
        old_qty_i = int(old_qty) if old_qty not in (None, "") else None
    except (TypeError, ValueError):
        old_qty_i = None

    ok = update_signal_meta(
        sid,
        skipped=None if body.skipped is None else (1 if body.skipped else 0),
        traded=None if body.traded is None else (1 if body.traded else 0),
        note=body.note,
        fill_price=body.fill_price,
        fill_qty=body.fill_qty,
        user_id=uid,
    )
    if not ok:
        raise HTTPException(404, "signal not found")

    booked: dict[str, Any] | None = None
    book_summary: dict[str, Any] | None = None
    if body.sync_book and body.fill_price is not None and body.fill_qty is not None:
        code = normalize_code(annotated.get("code"))
        name = str(annotated.get("name") or code)
        sig_type = str(annotated.get("signal_type") or "buy")
        new_px = float(body.fill_price)
        new_qty = int(body.fill_qty)
        try:
            if is_buy_signal(sig_type):
                booked = sync_position_buy_fill(
                    code,
                    name,
                    old_price=old_px_f,
                    old_qty=old_qty_i,
                    new_price=new_px,
                    new_qty=new_qty,
                    user_id=uid,
                    note="复盘改成交",
                    entry_board=str(annotated.get("mainline") or ""),
                )
                book_summary = {
                    "side": "buy",
                    "code": code,
                    "name": name,
                    "qty": int(booked.get("qty") or new_qty),
                    "price": float(booked.get("buy_price") or new_px),
                    "message": (
                        f"已同步仓位 {name or code}：成交改成 {new_qty}股 @ {new_px}；"
                        f"持仓现 {int(booked.get('qty') or 0)}股 / 成本 "
                        f"{booked.get('buy_price')}"
                    ),
                }
            elif is_sell_signal(sig_type):
                booked = sync_position_sell_fill(
                    code,
                    old_price=old_px_f,
                    old_qty=old_qty_i,
                    new_price=new_px,
                    new_qty=new_qty,
                    user_id=uid,
                    trade_date=str(annotated.get("trade_date") or "")[:10] or None,
                    day_anchor=_day_anchor_for_code(code),
                )
                book_summary = {
                    "side": "sell",
                    "code": code,
                    "name": name,
                    "qty": int(booked.get("trimmed") or new_qty),
                    "left": int(booked.get("qty") or 0),
                    "price": booked.get("sell_price") or new_px,
                    "message": (
                        f"已同步仓位卖出 {name or code}：成交改成 "
                        f"{int(booked.get('trimmed') or new_qty)}股 @ {new_px}；"
                        f"剩余 {int(booked.get('qty') or 0)}股"
                    ),
                }
            else:
                raise HTTPException(400, "unknown signal type for book sync")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    engine.clear_review_cache()
    out: dict[str, Any] = {"ok": True, "id": sid}
    if booked is not None:
        snap = engine.snapshot_for_user(uid)
        out["booked"] = booked
        out["book_summary"] = book_summary
        out["positions"] = snap.get("positions") or []
        out["summary"] = snap.get("position_summary")
    return out


@router.post("/api/review/{sid}/trade")
def trade_signal(
    sid: int, body: SignalTradeIn, user: dict = Depends(current_member_required)
) -> dict:
    """Mark traded; optionally book a buy or trim a sell into local positions."""
    row = load_signal(sid)
    if not row:
        raise HTTPException(404, "signal not found")
    code = normalize_code(row.get("code"))
    name = str(row.get("name") or code)
    sig_type = str(row.get("signal_type") or "buy")
    note = (body.note if body.note is not None else "复盘已交易").strip()
    fill_px = float(body.price) if body.price is not None else None
    fill_qty = int(body.qty) if body.qty is not None else None
    if fill_px is None:
        # Never silently book at plan/suggest price (signals.price).
        fill_px = float(row.get("last") or 0) or None
        if fill_px is None:
            q = (getattr(engine, "_book_quotes", None) or {}).get(code) or {}
            fill_px = float(q.get("price") or 0) or None
    if fill_qty is None and is_buy_signal(sig_type):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        plan_qty = payload.get("qty")
        try:
            fill_qty = int(plan_qty) if plan_qty not in (None, "") else 100
        except (TypeError, ValueError):
            fill_qty = 100
        if fill_qty <= 0:
            fill_qty = 100

    if is_sell_signal(sig_type) and code:
        trade_day = str(
            row.get("trade_date") or engine.snapshot.get("trade_date") or ""
        )[:10]
        positions = [
            p
            for p in load_positions(user_id=int(user["id"]))
            if normalize_code(p.get("code")) == code and int(p.get("qty") or 0) > 0
        ]
        if positions and is_t1_locked(positions[0], trade_day):
            raise HTTPException(
                400,
                "T+1：当日买入的仓位隔日才能卖，不能在复盘里记卖出",
            )
        bought_today = any(
            str(s.get("trade_date") or "")[:10] == trade_day
            and is_buy_signal(s.get("signal_type"))
            and normalize_code(s.get("code")) == code
            and int(s.get("traded") or 0)
            for s in apply_signal_user_meta(load_signals(limit=120), int(user["id"]))
        )
        if bought_today:
            raise HTTPException(
                400,
                "T+1：当日买入的仓位隔日才能卖，不能在复盘里记卖出",
            )

    update_signal_meta(
        sid,
        traded=1,
        skipped=0,
        note=note or None,
        fill_price=fill_px,
        fill_qty=fill_qty,
        user_id=int(user["id"]),
    )

    booked: dict[str, Any] | None = None
    if body.book and code:
        if is_buy_signal(sig_type):
            px = float(fill_px or 0)
            if px <= 0:
                raise HTTPException(400, "missing buy price")
            qty = int(fill_qty or 100)
            booked = add_position(
                code,
                name,
                px,
                qty,
                note,
                entry_board=str(row.get("mainline") or "")
                or str(
                    (((engine.snapshot.get("verdict") or {}).get("mainline") or {}).get("name"))
                    or ""
                ),
                user_id=int(user["id"]),
            )
            _log_exec_diary(
                user_id=int(user["id"]),
                side="buy",
                code=code,
                name=name,
                qty=qty,
                price=px,
                note=note or "复盘已交易",
                trade_date=str(row.get("trade_date") or "")[:10] or None,
            )
        else:
            positions = [
                p
                for p in load_positions(user_id=int(user["id"]))
                if normalize_code(p.get("code")) == code and int(p.get("qty") or 0) > 0
            ]
            if not positions:
                raise HTTPException(400, "no local position to trim for this sell signal")
            pos = positions[0]
            hold = int(pos.get("qty") or 0)
            mode = str(body.sell_mode or "").strip().lower()
            if body.qty is not None:
                qty = int(body.qty)
            elif mode in ("clear", "all", "full", "清仓"):
                qty = clear_sell_qty(hold)
            else:
                qty = half_sell_qty(hold)
            qty = min(qty, hold)
            if qty <= 0:
                raise HTTPException(400, "position qty is zero")
            sell_px = float(fill_px or 0) or None
            booked = trim_position(
                int(pos["id"]),
                qty,
                sell_price=sell_px,
                trade_date=str(row.get("trade_date") or "")[:10] or None,
                user_id=int(user["id"]),
                day_anchor=_day_anchor_for_code(code),
            )
            left = int((booked or {}).get("qty") or 0)
            _log_exec_diary(
                user_id=int(user["id"]),
                side="clear" if left <= 0 else "half",
                code=code,
                name=name,
                qty=qty,
                price=sell_px,
                note=note or "复盘已交易",
                trade_date=str(row.get("trade_date") or "")[:10] or None,
            )
            update_signal_meta(
                sid,
                fill_qty=qty,
                fill_price=fill_px or float(pos.get("buy_price") or 0) or None,
                user_id=int(user["id"]),
            )

    engine.clear_review_cache()
    snap = engine.snapshot_for_user(int(user["id"]))
    rows = snap.get("positions") or []
    book_summary: dict[str, Any] | None = None
    if booked:
        if is_buy_signal(sig_type):
            book_summary = {
                "side": "buy",
                "code": code,
                "name": name or booked.get("name"),
                "qty": int(booked.get("qty") or fill_qty or 0),
                "price": float(booked.get("buy_price") or fill_px or 0) or None,
                "message": (
                    f"已记买入 {name or code} "
                    f"{int(fill_qty or booked.get('qty') or 0)}股 "
                    f"@ {fill_px or booked.get('buy_price')}；"
                    f"持仓现 {int(booked.get('qty') or 0)}股"
                ),
            }
        else:
            trimmed = int(booked.get("trimmed") or 0)
            left = int(booked.get("qty") or 0)
            book_summary = {
                "side": "sell",
                "code": code,
                "name": name or booked.get("name"),
                "qty": trimmed,
                "left": left,
                "price": booked.get("sell_price") or fill_px,
                "realized": booked.get("realized_chunk"),
                "message": (
                    f"已记卖出 {name or code} {trimmed}股"
                    + (f" @ {booked.get('sell_price')}" if booked.get("sell_price") else "")
                    + (f"；剩余 {left}股" if left > 0 else "；已清仓（今日已平）")
                    + (
                        f"；本笔盈亏 {booked.get('realized_chunk')}"
                        if booked.get("realized_chunk") is not None
                        else ""
                    )
                ),
            }
    return {
        "ok": True,
        "id": sid,
        "signal_type": sig_type,
        "booked": booked,
        "book_summary": book_summary,
        "positions": rows,
        "summary": snap.get("position_summary"),
    }


@router.delete("/api/review/{sid}")
def remove_signal(sid: int, user: dict = Depends(current_admin_required)) -> dict:
    """Delete one review signal permanently (admin only)."""
    del user
    if not delete_signal(sid):
        raise HTTPException(404, "signal not found")
    engine.clear_review_cache()
    return {"ok": True, "id": sid}
