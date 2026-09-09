"""FastAPI application serving the battle desk."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from market_desk.config import STATIC_DIR
from market_desk.db import (
    add_position,
    add_favorite_board,
    add_watchlist,
    delete_favorite_board,
    delete_favorite_board_by_bk,
    delete_position,
    delete_signal,
    delete_watchlist,
    export_backup_payload,
    import_backup_payload,
    is_t1_locked,
    load_favorite_boards,
    load_positions,
    load_signal,
    load_signals,
    load_watchlist,
    trim_position,
    update_signal_meta,
)
from market_desk.eastmoney import fetch_daily_bars, fetch_minute_trends
from market_desk.engine import engine
from market_desk.filters import normalize_code, xueqiu_symbol, xueqiu_url
from market_desk.lots import clear_sell_qty, half_sell_qty
from market_desk.report import build_daily_report, build_eod_onepager, build_morning_brief
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
    sticky_margin: float | None = None
    switch_min_seconds: int | None = None
    toast_enabled: bool | None = None
    toast_cooldown: int | None = None
    decision_alerts: bool | None = None
    alert_mode: str | None = None
    open_mute_minutes: int | None = None
    daily_loss_cap_pct: float | None = None
    cool_after_losses: int | None = None
    target_total_cost: float | None = None
    equal_weight_target: bool | None = None
    batch_plan: bool | None = None
    auto_backup: bool | None = None
    account_equity: float | None = None
    risk_pct_per_trade: float | None = None
    min_stock_mv_yi: float | None = None


class TrendOverrideIn(BaseModel):
    """Manual daily-trend judgment for one recommended stock."""

    code: str
    verdict: str = Field(description="up or down")


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


class BackupIn(BaseModel):
    """JSON backup import payload."""

    replace: bool = False
    payload: dict[str, Any]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the refresh loop and wait for the first snapshot."""
    engine.start()
    for _ in range(120):
        if engine.snapshot.get("updated_at"):
            break
        await asyncio.sleep(0.25)
    yield
    await engine.stop()


app = FastAPI(title="A-share Market Desk", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    """Serve the dashboard page."""
    return FileResponse(Path(STATIC_DIR) / "index.html")


@app.get("/api/snapshot")
def snapshot() -> JSONResponse:
    """Return the latest assembled market snapshot."""
    return JSONResponse(engine.snapshot)


@app.get("/api/health")
def health() -> dict:
    """Liveness probe used by the startup script."""
    return {"ok": True, "updated_at": engine.snapshot.get("updated_at")}


@app.get("/api/review")
async def review(
    date: str | None = Query(default=None),
    vs_ml: str | None = Query(default=None, description="live or day"),
) -> dict:
    """Return one trade-date's signals with scored outcomes for the review tab."""
    return await engine.build_review(view_date=date, vs_mainline_mode=vs_ml)


@app.get("/api/chart/{code}")
async def chart(code: str, signal_at: str | None = Query(default=None)) -> dict:
    """Return intraday + daily series and a Xueqiu deep-link for one ticker."""
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
    for row in engine.snapshot.get("positions") or []:
        if normalize_code(row.get("code")) == c:
            name = str(row.get("name") or "")
            break
    if not name:
        rec = ((engine.snapshot.get("verdict") or {}).get("recommend") or {}).get("items") or []
        for item in rec:
            if normalize_code(item.get("code")) == c:
                name = str(item.get("name") or "")
                break
    if not name:
        for w in engine.snapshot.get("watch") or []:
            if normalize_code(w.get("code")) == c:
                name = str(w.get("name") or "")
                break
    sig = (signal_at or "").strip() or None
    if not sig:
        today = str(engine.snapshot.get("trade_date") or "")
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
def list_positions() -> dict:
    """Return recorded positions with the last known marks."""
    rows = engine.sync_positions()
    return {
        "ok": True,
        "positions": rows,
        "summary": engine.snapshot.get("position_summary"),
    }


@app.post("/api/positions")
def create_position(body: PositionIn) -> dict:
    """Record a buy: code, price and share count."""
    code = normalize_code(body.code)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "code must be a 6-digit ticker")
    name = (body.name or "").strip()
    add_position(code, name, float(body.buy_price), int(body.qty), body.note.strip())
    rows = engine.sync_positions()
    return {
        "ok": True,
        "positions": rows,
        "summary": engine.snapshot.get("position_summary"),
    }


@app.delete("/api/positions/{pid}")
def remove_position(pid: int) -> dict:
    """Delete a recorded position."""
    if not delete_position(pid):
        raise HTTPException(404, "position not found")
    rows = engine.sync_positions()
    return {
        "ok": True,
        "positions": rows,
        "summary": engine.snapshot.get("position_summary"),
    }


@app.post("/api/positions/{pid}/trim")
def trim_position_api(pid: int, body: TrimIn) -> dict:
    """Sell/reduce shares on a recorded position (local book only)."""
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
    )
    if row is None:
        raise HTTPException(404, "position not found")
    rows = engine.sync_positions()
    return {
        "ok": True,
        "trimmed": row,
        "positions": rows,
        "summary": engine.snapshot.get("position_summary"),
    }


@app.post("/api/review/{sid}")
def annotate_signal(sid: int, body: SignalMetaIn) -> dict:
    """Mark a signal as traded / not traded, or attach a note / fill."""
    ok = update_signal_meta(
        sid,
        skipped=None if body.skipped is None else (1 if body.skipped else 0),
        traded=None if body.traded is None else (1 if body.traded else 0),
        note=body.note,
        fill_price=body.fill_price,
        fill_qty=body.fill_qty,
    )
    if not ok:
        raise HTTPException(404, "signal not found")
    return {"ok": True, "id": sid}


@app.post("/api/review/{sid}/trade")
def trade_signal(sid: int, body: SignalTradeIn) -> dict:
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
    if fill_px is None and sig_type == "buy":
        fill_px = float(row.get("price") or row.get("last") or 0) or None
    if fill_qty is None and sig_type == "buy":
        fill_qty = 100

    if sig_type == "sell" and code:
        trade_day = str(
            row.get("trade_date") or engine.snapshot.get("trade_date") or ""
        )[:10]
        positions = [
            p
            for p in load_positions()
            if normalize_code(p.get("code")) == code and int(p.get("qty") or 0) > 0
        ]
        if positions and is_t1_locked(positions[0], trade_day):
            raise HTTPException(
                400,
                "T+1：当日买入的仓位隔日才能卖，不能在复盘里记卖出",
            )
        bought_today = any(
            str(s.get("trade_date") or "")[:10] == trade_day
            and str(s.get("signal_type") or "") == "buy"
            and normalize_code(s.get("code")) == code
            and int(s.get("traded") or 0)
            for s in load_signals(limit=120)
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
    )

    booked: dict[str, Any] | None = None
    if body.book and code:
        if sig_type == "buy":
            px = float(fill_px or 0)
            if px <= 0:
                raise HTTPException(400, "missing buy price")
            qty = int(fill_qty or 100)
            booked = add_position(code, name, px, qty, note)
        else:
            positions = [
                p
                for p in load_positions()
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
            )
            update_signal_meta(sid, fill_qty=qty, fill_price=fill_px or float(pos.get("buy_price") or 0) or None)

    rows = engine.sync_positions()
    book_summary: dict[str, Any] | None = None
    if booked:
        if sig_type == "buy":
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
        "summary": engine.snapshot.get("position_summary"),
    }


@app.get("/api/settings")
def read_settings() -> dict:
    """Return runtime settings for the parameters panel."""
    return {"ok": True, "settings": get_settings(refresh=True)}


@app.post("/api/settings")
def write_settings(body: SettingsIn) -> dict:
    """Patch runtime settings from the parameters panel."""
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return {"ok": True, "settings": update_settings(patch)}


@app.delete("/api/review/{sid}")
def remove_signal(sid: int) -> dict:
    """Delete one review signal permanently."""
    if not delete_signal(sid):
        raise HTTPException(404, "signal not found")
    return {"ok": True, "id": sid}


@app.post("/api/trend-override")
def trend_override(body: TrendOverrideIn) -> dict:
    """Accept a manual up/down trend judgment on the battle desk."""
    code = normalize_code(body.code)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "code must be a 6-digit ticker")
    flag = (body.verdict or "").strip().lower()
    if flag not in ("up", "down"):
        raise HTTPException(400, "verdict must be up or down")
    return engine.apply_trend_override(code, flag)


@app.get("/api/watchlist")
def list_watchlist() -> dict:
    """Return personal watchlist rows with last known marks."""
    rows = engine.sync_watchlist()
    return {"ok": True, "watchlist": rows}


@app.post("/api/watchlist")
def create_watchlist(body: WatchlistIn) -> dict:
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
    )
    return {"ok": True, "watchlist": engine.sync_watchlist()}


@app.delete("/api/watchlist/{item_id}")
def remove_watchlist(item_id: int) -> dict:
    """Delete one personal watchlist row."""
    if not delete_watchlist(item_id):
        raise HTTPException(404, "watchlist item not found")
    return {"ok": True, "watchlist": engine.sync_watchlist()}


@app.get("/api/favorite-boards")
def list_favorite_boards() -> dict:
    """Return personally favored boards from the live snapshot."""
    rows = engine.sync_favorite_boards()
    return {"ok": True, "favorite_boards": rows, "stored": load_favorite_boards()}


@app.post("/api/favorite-boards")
def create_favorite_board(body: FavoriteBoardIn) -> dict:
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
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "favorite_boards": engine.sync_favorite_boards()}


@app.delete("/api/favorite-boards/by-bk/{bk}")
def remove_favorite_board_bk(bk: str) -> dict:
    """Delete one favored board by East Money board code."""
    if not delete_favorite_board_by_bk(bk):
        raise HTTPException(404, "favorite board not found")
    return {"ok": True, "favorite_boards": engine.sync_favorite_boards()}


@app.delete("/api/favorite-boards/{item_id}")
def remove_favorite_board(item_id: int) -> dict:
    """Delete one favored board by id."""
    if not delete_favorite_board(item_id):
        raise HTTPException(404, "favorite board not found")
    return {"ok": True, "favorite_boards": engine.sync_favorite_boards()}


@app.get("/api/report/today")
async def report_today() -> dict:
    """Return today's markdown journal for copy / download."""
    review = await engine.build_review()
    text = build_daily_report(snapshot=engine.snapshot, review=review)
    return {"ok": True, "markdown": text, "summary": review.get("summary")}


@app.get("/api/report/eod")
async def report_eod(date: str | None = Query(default=None)) -> dict:
    """Return the compact end-of-day one-pager (phase / switches / exec / P&L)."""
    review = await engine.build_review(view_date=date)
    brief = build_eod_onepager(snapshot=engine.snapshot, review=review)
    return {"ok": True, "brief": brief, "markdown": brief.get("markdown") or ""}


@app.get("/api/report/morning")
def report_morning() -> dict:
    """Return the rule-based morning decision brief."""
    brief = (engine.snapshot or {}).get("morning_brief") or build_morning_brief(
        engine.snapshot
    )
    return {"ok": True, "brief": brief, "markdown": brief.get("markdown") or ""}


@app.get("/api/backup")
def backup_export() -> dict:
    """Export local desk data as JSON."""
    return {"ok": True, "backup": export_backup_payload()}


@app.post("/api/backup/import")
def backup_import(body: BackupIn) -> dict:
    """Import a previously exported JSON backup."""
    try:
        counts = import_backup_payload(body.payload, replace=bool(body.replace))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    from market_desk.settings import get_settings

    get_settings(refresh=True)
    engine.sync_positions()
    engine.sync_watchlist()
    return {"ok": True, "counts": counts}
