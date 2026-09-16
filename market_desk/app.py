"""FastAPI application serving the battle desk."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from market_desk.auth import (
    SESSION_COOKIE,
    admin_approve,
    admin_delete_user,
    admin_list_users,
    admin_reject,
    change_password,
    ensure_bootstrap_admin,
    is_guest,
    login_guest,
    login_user,
    logout_token,
    register_user,
)
from market_desk.deps import (
    current_admin_required,
    current_member_required,
    current_user_optional,
    current_user_required,
)
from market_desk.settings import get_settings_for_user

from market_desk.config import STATIC_DIR
from market_desk.db import (
    add_position,
    add_favorite_board,
    add_stock_blacklist,
    add_watchlist,
    apply_signal_user_meta,
    delete_favorite_board,
    delete_favorite_board_by_bk,
    delete_position,
    delete_signal,
    delete_stock_blacklist,
    delete_watchlist,
    export_user_backup_payload,
    import_user_backup_payload,
    is_t1_locked,
    load_favorite_boards,
    load_positions,
    load_signal,
    load_signals,
    load_stock_blacklist,
    load_watchlist,
    sync_position_buy_fill,
    sync_position_sell_fill,
    sync_sell_fill_from_trim,
    trim_position,
    update_signal_meta,
)
from market_desk.eastmoney import fetch_daily_bars, fetch_minute_trends
from market_desk.engine import engine
from market_desk.filters import normalize_code, xueqiu_symbol, xueqiu_url
from market_desk.lots import clear_sell_qty, half_sell_qty
from market_desk.report import (
    build_daily_report,
    build_eod_onepager,
    build_morning_brief,
    build_tomorrow_brief,
)
from market_desk.review import is_buy_signal, is_sell_signal
from market_desk.lhb import build_positions_lhb
from market_desk.settings import get_settings, update_settings
from market_desk.trend import classify_daily_trend


class PositionIn(BaseModel):
    """Payload for recording a local stock or ETF position."""

    code: str
    name: str = ""
    buy_price: float = Field(gt=0)
    qty: int = Field(gt=0)
    note: str = ""


class TrimIn(BaseModel):
    """Payload for reducing share count on an existing position."""

    qty: int = Field(gt=0)
    sell_price: float | None = Field(default=None, gt=0)


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


class SettingsIn(BaseModel):
    """Partial runtime settings patch from the UI panel."""

    refresh_seconds: int | None = None
    idle_seconds: int | None = None
    auction_refresh_seconds: int | None = None
    sticky_margin: float | None = None
    switch_min_seconds: int | None = None
    toast_enabled: bool | None = None
    toast_cooldown: int | None = None
    decision_alerts: bool | None = None
    alert_mode: str | None = None
    open_mute_minutes: int | None = None
    tail_mute_minutes: int | None = None
    daily_loss_cap_pct: float | None = None
    cool_after_losses: int | None = None
    target_total_cost: float | None = None
    equal_weight_target: bool | None = None
    batch_plan: bool | None = None
    auto_backup: bool | None = None
    account_equity: float | None = None
    risk_pct_per_trade: float | None = None
    min_stock_mv_yi: float | None = None
    # Review hit-rate: traded (executed only) | all (paper signals too).
    hit_rate_mode: str | None = None
    phase_panic_temp: int | None = None
    phase_ferment_temp: int | None = None
    phase_climax_temp: int | None = None


class TrendOverrideIn(BaseModel):
    """Manual daily-trend judgment for one recommended stock."""

    code: str
    verdict: str = Field(description="up or down")


class ThemeRepIn(BaseModel):
    """Manual theme reputation adjustment (absolute, delta, or clear)."""

    theme_key: str
    manual_adj: float | None = None
    delta: float | None = None
    note: str = ""
    clear: bool = False


class WatchlistIn(BaseModel):
    """Payload for adding a personal watchlist ticker."""

    code: str
    name: str = ""
    note: str = ""
    suggest_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    chase_price: float | None = Field(default=None, gt=0)


class FavoriteBoardIn(BaseModel):
    """Payload for adding a personally favored sector board."""

    bk: str
    name: str = ""
    kind: str = ""
    note: str = ""


class BlacklistIn(BaseModel):
    """Payload for manually adding a stock blacklist ticker."""

    code: str
    name: str = ""
    note: str = ""
    reason: str = ""


class AuthCredIn(BaseModel):
    """Login / register payload."""

    username: str
    password: str


class PasswordChangeIn(BaseModel):
    """Change-password payload."""

    old_password: str
    new_password: str


class BackupIn(BaseModel):
    """JSON backup import payload."""

    replace: bool = False
    payload: dict[str, Any]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the refresh loop and wait for the first snapshot."""
    ensure_bootstrap_admin()
    engine.start()
    for _ in range(120):
        if engine.snapshot.get("updated_at"):
            break
        await asyncio.sleep(0.25)
    yield
    await engine.stop()


app = FastAPI(title="牛来-作战台", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    """Serve the dashboard page."""
    return FileResponse(Path(STATIC_DIR) / "index.html")


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    """Serve the tab icon (browsers request this at site root)."""
    return FileResponse(Path(STATIC_DIR) / "favicon.ico")



@app.post("/api/auth/register")
def auth_register(body: AuthCredIn) -> dict:
    """Self-register; account stays pending until an admin approves."""
    try:
        user = register_user(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "ok": True,
        "user": user,
        "message": "已提交注册，等待管理员同意后方可登录",
    }


@app.post("/api/auth/login")
def auth_login(body: AuthCredIn, response: Response) -> dict:
    """Log in and set an HttpOnly session cookie."""
    try:
        user, token, expires = login_user(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=14 * 24 * 3600,
        path="/",
    )
    return {"ok": True, "user": user, "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S")}


@app.post("/api/auth/guest")
def auth_guest(response: Response) -> dict:
    """Enter as the shared guest account (view-only personal layer)."""
    try:
        user, token, expires = login_guest()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=14 * 24 * 3600,
        path="/",
    )
    return {"ok": True, "user": user, "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S")}


@app.post("/api/auth/logout")
def auth_logout(
    response: Response,
    desk_sid: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    """Clear the session cookie and invalidate the token."""
    logout_token(desk_sid)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(user: dict | None = Depends(current_user_optional)) -> dict:
    """Return the current session user (or null)."""
    return {"ok": True, "user": user}


@app.post("/api/auth/password")
def auth_password(
    body: PasswordChangeIn,
    user: dict = Depends(current_member_required),
) -> dict:
    """Change the logged-in user's password."""
    try:
        change_password(int(user["id"]), body.old_password, body.new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/admin/users")
def admin_users(admin: dict = Depends(current_admin_required)) -> dict:
    """List users for admin approval."""
    del admin
    return {"ok": True, "users": admin_list_users()}


@app.post("/api/admin/users/{uid}/approve")
def admin_user_approve(uid: int, admin: dict = Depends(current_admin_required)) -> dict:
    """Approve a pending registration."""
    try:
        row = admin_approve(uid, int(admin["id"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "user": row}


@app.post("/api/admin/users/{uid}/reject")
def admin_user_reject(uid: int, admin: dict = Depends(current_admin_required)) -> dict:
    """Reject a pending registration."""
    try:
        row = admin_reject(uid, int(admin["id"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "user": row}


@app.delete("/api/admin/users/{uid}")
def admin_user_delete(uid: int, admin: dict = Depends(current_admin_required)) -> dict:
    """Permanently delete another account (admin only)."""
    try:
        row = admin_delete_user(uid, int(admin["id"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "user": row}


@app.get("/api/snapshot")
def snapshot(
    view: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> JSONResponse:
    """Return market snapshot plus the caller's personal layer when allowed."""
    uid = int(user["id"])
    full = engine.snapshot_for_user(uid)
    full["auth_user"] = user
    full["personal_locked"] = bool(is_guest(user) or full.get("personal_locked"))
    if view:
        # Reuse slice keys on the already-personalized payload.
        engine.snapshot, prev = full, engine.snapshot
        try:
            sliced = engine.slice_snapshot(view)
        finally:
            engine.snapshot = prev
        sliced["auth_user"] = full.get("auth_user")
        sliced["auth_required_personal"] = full.get("auth_required_personal")
        sliced["personal_locked"] = full.get("personal_locked")
        return JSONResponse(sliced)
    return JSONResponse(full)


@app.get("/api/fund-flow")
async def fund_flow(
    force: bool = Query(default=False),
    user: dict = Depends(current_user_required),
) -> dict:
    """Refresh East Money week/month fund-flow boards for the funds tab."""
    del user
    return await engine.refresh_fund_flow(force=force)


@app.get("/api/health")
def health() -> dict:
    """Liveness probe used by the startup script."""
    return {"ok": True, "updated_at": engine.snapshot.get("updated_at")}


@app.get("/api/review")
async def review(
    date: str | None = Query(default=None),
    vs_ml: str | None = Query(default=None, description="live or day"),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return one trade-date's signals with scored outcomes for the review tab."""
    # Guest sees paper signals only (no fill overlays); members get per-user meta.
    uid = None if is_guest(user) else int(user["id"])
    return await engine.build_review(
        view_date=date, vs_mainline_mode=vs_ml, user_id=uid
    )


@app.get("/api/review/zt-ytd")
async def review_zt_ytd(
    date: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return calendar-year limit-up counts for the review day's equities."""
    del user
    return await engine.build_review_zt_ytd(view_date=date)


@app.get("/api/review/trends")
async def review_trends(
    date: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return daily up/down/sideways classifications for review-day tickers."""
    del user
    return await engine.build_review_trends(view_date=date)


@app.get("/api/review/history/{code}")
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


@app.get("/api/chart/{code}")
async def chart(
    code: str,
    signal_at: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return intraday + daily series and a Xueqiu deep-link for one ticker."""
    snap = engine.snapshot_for_user(int(user["id"]))
    c = normalize_code(code)
    if len(c) != 6 or not c.isdigit():
        raise HTTPException(400, "code must be a 6-digit ticker")
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        bars, minutes = await asyncio.gather(
            fetch_daily_bars(client, c, limit=60),
            fetch_minute_trends(client, c),
        )
    closes = [float(b["close"]) for b in bars]
    trend = classify_daily_trend(closes, fetch_ok=bool(closes))
    name = ""
    for row in snap.get("positions") or []:
        if normalize_code(row.get("code")) == c:
            name = str(row.get("name") or "")
            break
    if not name:
        rec = ((snap.get("verdict") or {}).get("recommend") or {}).get("items") or []
        for item in rec:
            if normalize_code(item.get("code")) == c:
                name = str(item.get("name") or "")
                break
    if not name:
        for w in snap.get("watch") or []:
            if normalize_code(w.get("code")) == c:
                name = str(w.get("name") or "")
                break
    sig = (signal_at or "").strip() or None
    if not sig:
        today = str(snap.get("trade_date") or "")
        for row in load_signals(limit=120):
            if normalize_code(row.get("code")) != c:
                continue
            if today and str(row.get("trade_date") or "") != today:
                continue
            raw = str(row.get("signaled_at") or "").strip()
            if raw:
                sig = raw
                break
    return {
        "ok": True,
        "code": c,
        "name": name or c,
        "symbol": xueqiu_symbol(c),
        "xueqiu_url": xueqiu_url(c),
        "trend": trend,
        "daily": bars,
        "minute": minutes,
        "signal_at": sig,
    }


@app.get("/api/positions")
def list_positions(user: dict = Depends(current_member_required)) -> dict:
    """Return recorded positions with the last known marks."""
    snap = engine.snapshot_for_user(int(user["id"]))
    return {
        "ok": True,
        "positions": snap.get("positions") or [],
        "summary": snap.get("position_summary"),
    }


@app.get("/api/positions/lhb")
async def positions_lhb(
    force: bool = Query(default=False),
    user: dict = Depends(current_member_required),
) -> dict:
    """Return dragon-tiger seats for the caller's open positions (view-only)."""
    snap = engine.snapshot_for_user(int(user["id"]))
    return await build_positions_lhb(snap.get("positions") or [], force=force)


@app.post("/api/positions")
def create_position(body: PositionIn, user: dict = Depends(current_member_required)) -> dict:
    """Record a buy: code, price and share count."""
    code = normalize_code(body.code)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "code must be a 6-digit ticker")
    name = (body.name or "").strip()
    entry_board = str(
        (((engine.snapshot.get("verdict") or {}).get("mainline") or {}).get("name")) or ""
    )
    add_position(
        code,
        name,
        float(body.buy_price),
        int(body.qty),
        body.note.strip(),
        entry_board=entry_board,
        user_id=int(user["id"]),
    )
    snap = engine.snapshot_for_user(int(user["id"]))
    return {
        "ok": True,
        "positions": snap.get("positions") or [],
        "summary": snap.get("position_summary"),
    }


@app.delete("/api/positions/{pid}")
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


@app.post("/api/positions/{pid}/trim")
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
    row = trim_position(
        pid,
        int(body.qty),
        sell_price=body.sell_price,
        trade_date=trade_day,
        user_id=int(user["id"]),
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
        "positions": snap.get("positions") or [],
        "summary": snap.get("position_summary"),
    }


@app.post("/api/review/{sid}")
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


@app.post("/api/review/{sid}/trade")
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


@app.get("/api/settings")
def read_settings(user: dict = Depends(current_user_required)) -> dict:
    """Return runtime settings (merged with user private knobs when allowed)."""
    uid = None if is_guest(user) else int(user["id"])
    return {"ok": True, "settings": get_settings_for_user(uid), "auth_user": user}


@app.post("/api/settings")
def write_settings(
    body: SettingsIn,
    user: dict = Depends(current_member_required),
) -> dict:
    """Patch settings: private keys per user; global keys require admin."""
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    from market_desk.settings import USER_PRIVATE_KEYS

    global_keys = [k for k in patch if k not in USER_PRIVATE_KEYS]
    if global_keys and user.get("role") != "admin":
        raise HTTPException(403, "全局参数仅管理员可改：" + "、".join(global_keys[:6]))
    return {
        "ok": True,
        "settings": update_settings(patch, user_id=int(user["id"])),
        "auth_user": user,
    }


@app.delete("/api/review/{sid}")
def remove_signal(sid: int, user: dict = Depends(current_admin_required)) -> dict:
    """Delete one review signal permanently (admin only)."""
    del user
    if not delete_signal(sid):
        raise HTTPException(404, "signal not found")
    engine.clear_review_cache()
    return {"ok": True, "id": sid}


@app.post("/api/trend-override")
def trend_override(
    body: TrendOverrideIn, user: dict = Depends(current_member_required)
) -> dict:
    """Accept a manual up/down trend judgment on the battle desk."""
    del user
    code = normalize_code(body.code)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "code must be a 6-digit ticker")
    flag = (body.verdict or "").strip().lower()
    if flag not in ("up", "down"):
        raise HTTPException(400, "verdict must be up or down")
    return engine.apply_trend_override(code, flag)


@app.post("/api/theme-reputation")
def theme_reputation_adjust(
    body: ThemeRepIn, user: dict = Depends(current_admin_required)
) -> dict:
    """Set, nudge, or clear a manual theme reputation adjustment (admin only)."""
    del user
    theme = (body.theme_key or "").strip()
    if not theme:
        raise HTTPException(400, "theme_key required")
    if not body.clear and body.manual_adj is None and body.delta is None:
        raise HTTPException(400, "provide manual_adj, delta, or clear")
    out = engine.apply_theme_manual_adj(
        theme,
        manual_adj=body.manual_adj,
        delta=body.delta,
        note=(body.note or "").strip() or None,
        clear=bool(body.clear),
    )
    if not out.get("ok"):
        raise HTTPException(400, out.get("error") or "adjust failed")
    return out


@app.get("/api/watchlist")
def list_watchlist(user: dict = Depends(current_member_required)) -> dict:
    """Return personal watchlist rows with last known marks."""
    rows = engine.sync_watchlist(user_id=int(user["id"]))
    return {"ok": True, "watchlist": rows}


@app.post("/api/watchlist")
def create_watchlist(body: WatchlistIn, user: dict = Depends(current_member_required)) -> dict:
    """Add or refresh one personal watchlist ticker."""
    code = normalize_code(body.code)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "code must be a 6-digit ticker")
    add_watchlist(
        code,
        body.name.strip(),
        note=body.note.strip(),
        suggest_price=body.suggest_price,
        stop_price=body.stop_price,
        chase_price=body.chase_price,
        user_id=int(user["id"]),
    )
    return {"ok": True, "watchlist": engine.sync_watchlist(user_id=int(user["id"]))}


@app.delete("/api/watchlist/{item_id}")
def remove_watchlist(item_id: int, user: dict = Depends(current_member_required)) -> dict:
    """Delete one personal watchlist row."""
    if not delete_watchlist(item_id, user_id=int(user["id"])):
        raise HTTPException(404, "watchlist item not found")
    return {"ok": True, "watchlist": engine.sync_watchlist(user_id=int(user["id"]))}


@app.get("/api/favorite-boards")
def list_favorite_boards(user: dict = Depends(current_member_required)) -> dict:
    """Return personally favored boards from the live snapshot."""
    rows = engine.sync_favorite_boards(user_id=int(user["id"]))
    return {
        "ok": True,
        "favorite_boards": rows,
        "stored": load_favorite_boards(user_id=int(user["id"])),
    }


@app.post("/api/favorite-boards")
def create_favorite_board(
    body: FavoriteBoardIn,
    user: dict = Depends(current_member_required),
) -> dict:
    """Add or refresh one favored sector board."""
    bk = str(body.bk or "").strip().upper()
    if not bk.startswith("BK") or len(bk) < 4:
        raise HTTPException(400, "bk must look like BKXXXX")
    try:
        add_favorite_board(
            bk,
            body.name.strip(),
            kind=body.kind.strip(),
            note=body.note.strip(),
            user_id=int(user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "favorite_boards": engine.sync_favorite_boards(user_id=int(user["id"]))}


@app.delete("/api/favorite-boards/by-bk/{bk}")
def remove_favorite_board_bk(bk: str, user: dict = Depends(current_member_required)) -> dict:
    """Delete one favored board by East Money board code."""
    if not delete_favorite_board_by_bk(bk, user_id=int(user["id"])):
        raise HTTPException(404, "favorite board not found")
    return {"ok": True, "favorite_boards": engine.sync_favorite_boards(user_id=int(user["id"]))}


@app.delete("/api/favorite-boards/{item_id}")
def remove_favorite_board(item_id: int, user: dict = Depends(current_member_required)) -> dict:
    """Delete one favored board by id."""
    if not delete_favorite_board(item_id, user_id=int(user["id"])):
        raise HTTPException(404, "favorite board not found")
    return {"ok": True, "favorite_boards": engine.sync_favorite_boards(user_id=int(user["id"]))}


@app.get("/api/blacklist")
def list_blacklist(user: dict = Depends(current_user_required)) -> dict:
    """Return the active stock blacklist."""
    del user
    rows = load_stock_blacklist()
    snap = (engine.snapshot or {}).get("stock_blacklist") or {}
    return {
        "ok": True,
        "items": rows,
        "flagged_today": snap.get("flagged_today"),
        "scanned": snap.get("scanned"),
    }


@app.post("/api/blacklist")
def create_blacklist(
    body: BlacklistIn, user: dict = Depends(current_admin_required)
) -> dict:
    """Manually add one ticker to the blacklist."""
    del user
    code = normalize_code(body.code)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "code must be a 6-digit ticker")
    try:
        row = add_stock_blacklist(
            code,
            body.name.strip(),
            reason=body.reason.strip() or "手动加入",
            source="manual",
            note=body.note.strip(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    rows = load_stock_blacklist()
    if isinstance(engine.snapshot, dict):
        engine.snapshot["stock_blacklist"] = {
            **(engine.snapshot.get("stock_blacklist") or {}),
            "items": rows,
            "codes": [str(r.get("code") or "").zfill(6) for r in rows],
        }
    return {"ok": True, "item": row, "items": rows}


@app.delete("/api/blacklist/{code}")
def remove_blacklist(code: str, user: dict = Depends(current_admin_required)) -> dict:
    """Remove one ticker from the blacklist."""
    del user
    c = normalize_code(code)
    if not delete_stock_blacklist(c):
        raise HTTPException(404, "blacklist item not found")
    rows = load_stock_blacklist()
    if isinstance(engine.snapshot, dict):
        engine.snapshot["stock_blacklist"] = {
            **(engine.snapshot.get("stock_blacklist") or {}),
            "items": rows,
            "codes": [str(r.get("code") or "").zfill(6) for r in rows],
        }
    return {"ok": True, "items": rows}


@app.get("/api/report/today")
async def report_today(user: dict = Depends(current_user_required)) -> dict:
    """Return today's markdown journal for copy / download."""
    uid = None if is_guest(user) else int(user["id"])
    snap = engine.snapshot_for_user(int(user["id"]))
    review = await engine.build_review(user_id=uid)
    text = build_daily_report(snapshot=snap, review=review)
    return {"ok": True, "markdown": text, "summary": review.get("summary")}


@app.get("/api/report/eod")
async def report_eod(
    date: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return the compact end-of-day one-pager (phase / switches / exec / P&L)."""
    uid = None if is_guest(user) else int(user["id"])
    snap = engine.snapshot_for_user(int(user["id"]))
    review = await engine.build_review(view_date=date, user_id=uid)
    brief = build_eod_onepager(snapshot=snap, review=review)
    return {"ok": True, "brief": brief, "markdown": brief.get("markdown") or ""}


@app.get("/api/report/tomorrow")
async def report_tomorrow(
    date: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return after-close tomorrow-watch brief (observe-only, no buy gates)."""
    uid = None if is_guest(user) else int(user["id"])
    snap = engine.snapshot_for_user(int(user["id"]))
    review = await engine.build_review(view_date=date, user_id=uid)
    brief = build_tomorrow_brief(snapshot=snap, review=review)
    return {"ok": True, "brief": brief, "markdown": brief.get("markdown") or ""}


@app.get("/api/report/morning")
def report_morning(user: dict = Depends(current_user_required)) -> dict:
    """Return the rule-based morning decision brief."""
    snap = engine.snapshot_for_user(int(user["id"]))
    brief = snap.get("morning_brief") or build_morning_brief(snap)
    return {"ok": True, "brief": brief, "markdown": brief.get("markdown") or ""}


@app.get("/api/backup")
def backup_export(user: dict = Depends(current_member_required)) -> dict:
    """Export this user's personal desk data as JSON."""
    return {"ok": True, "backup": export_user_backup_payload(int(user["id"]))}


@app.post("/api/backup/import")
def backup_import(
    body: BackupIn, user: dict = Depends(current_member_required)
) -> dict:
    """Import a previously exported JSON backup into this user's books."""
    try:
        counts = import_user_backup_payload(
            int(user["id"]), body.payload, replace=bool(body.replace)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    from market_desk.settings import get_settings

    get_settings(refresh=True)
    return {"ok": True, "counts": counts}
