"""FastAPI application serving the battle desk."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from market_desk.config import STATIC_DIR
from market_desk.db import (
    add_position,
    delete_position,
    delete_signal,
    trim_position,
    update_signal_meta,
)
from market_desk.eastmoney import fetch_daily_bars, fetch_minute_trends
from market_desk.engine import engine
from market_desk.filters import normalize_code, xueqiu_symbol, xueqiu_url
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


class SignalMetaIn(BaseModel):
    """User annotation for a logged buy/sell signal."""

    skipped: bool | None = None
    traded: bool | None = None
    note: str | None = None


class TrendOverrideIn(BaseModel):
    """Manual daily-trend judgment for one recommended stock."""

    code: str
    verdict: str = Field(description="up or down")


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
async def review() -> dict:
    """Return signal history with scored outcomes for the review tab."""
    return await engine.build_review()


@app.get("/api/chart/{code}")
async def chart(code: str) -> dict:
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
    return {
        "ok": True,
        "code": c,
        "name": name or c,
        "symbol": xueqiu_symbol(c),
        "xueqiu_url": xueqiu_url(c),
        "trend": trend,
        "daily": bars,
        "minute": minutes,
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
    row = trim_position(pid, int(body.qty))
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
    """Mark a signal as traded / not traded, or attach a note."""
    ok = update_signal_meta(
        sid,
        skipped=None if body.skipped is None else (1 if body.skipped else 0),
        traded=None if body.traded is None else (1 if body.traded else 0),
        note=body.note,
    )
    if not ok:
        raise HTTPException(404, "signal not found")
    return {"ok": True, "id": sid}


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
