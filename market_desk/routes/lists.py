"""Watchlist, favorite boards and the stock blacklist."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from market_desk.db import (
    add_favorite_board,
    add_stock_blacklist,
    add_watchlist,
    delete_favorite_board,
    delete_favorite_board_by_bk,
    delete_stock_blacklist,
    delete_watchlist,
    load_favorite_boards,
    load_stock_blacklist,
)
from market_desk.deps import (
    current_admin_required,
    current_member_required,
    current_user_required,
)
from market_desk.engine import engine
from market_desk.filters import normalize_code

router = APIRouter()


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


@router.get("/api/watchlist")
def list_watchlist(user: dict = Depends(current_member_required)) -> dict:
    """Return personal watchlist rows with last known marks."""
    rows = engine.sync_watchlist(user_id=int(user["id"]))
    return {"ok": True, "watchlist": rows}


@router.post("/api/watchlist")
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


@router.delete("/api/watchlist/{item_id}")
def remove_watchlist(item_id: int, user: dict = Depends(current_member_required)) -> dict:
    """Delete one personal watchlist row."""
    if not delete_watchlist(item_id, user_id=int(user["id"])):
        raise HTTPException(404, "watchlist item not found")
    return {"ok": True, "watchlist": engine.sync_watchlist(user_id=int(user["id"]))}


@router.get("/api/favorite-boards")
def list_favorite_boards(user: dict = Depends(current_member_required)) -> dict:
    """Return personally favored boards from the live snapshot."""
    rows = engine.sync_favorite_boards(user_id=int(user["id"]))
    return {
        "ok": True,
        "favorite_boards": rows,
        "stored": load_favorite_boards(user_id=int(user["id"])),
    }


@router.post("/api/favorite-boards")
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


@router.delete("/api/favorite-boards/by-bk/{bk}")
def remove_favorite_board_bk(bk: str, user: dict = Depends(current_member_required)) -> dict:
    """Delete one favored board by East Money board code."""
    if not delete_favorite_board_by_bk(bk, user_id=int(user["id"])):
        raise HTTPException(404, "favorite board not found")
    return {"ok": True, "favorite_boards": engine.sync_favorite_boards(user_id=int(user["id"]))}


@router.delete("/api/favorite-boards/{item_id}")
def remove_favorite_board(item_id: int, user: dict = Depends(current_member_required)) -> dict:
    """Delete one favored board by id."""
    if not delete_favorite_board(item_id, user_id=int(user["id"])):
        raise HTTPException(404, "favorite board not found")
    return {"ok": True, "favorite_boards": engine.sync_favorite_boards(user_id=int(user["id"]))}


@router.get("/api/blacklist")
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


@router.post("/api/blacklist")
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


@router.delete("/api/blacklist/{code}")
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
