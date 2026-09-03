"""FastAPI application serving the battle desk."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from market_desk.config import STATIC_DIR
from market_desk.db import add_position, delete_position
from market_desk.engine import engine
from market_desk.filters import normalize_code


class PositionIn(BaseModel):
    """Payload for recording a local stock or ETF position."""

    code: str
    name: str = ""
    buy_price: float = Field(gt=0)
    qty: int = Field(gt=0)
    note: str = ""


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
