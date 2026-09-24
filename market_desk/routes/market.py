"""Desk snapshot, fund flow, health, board export, charts and theme overrides."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from market_desk.auth import is_guest
from market_desk.db import load_signals
from market_desk.deps import (
    current_admin_required,
    current_member_required,
    current_user_required,
)
from market_desk.eastmoney import fetch_daily_bars, fetch_minute_trends
from market_desk.engine import engine
from market_desk.filters import normalize_code, xueqiu_symbol, xueqiu_url
from market_desk.glossary import GLOSSARY
from market_desk.trend import classify_daily_trend

router = APIRouter()


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


@router.get("/api/glossary")
def api_glossary(user: dict = Depends(current_user_required)) -> dict:
    """Return the full UI glossary (independent of snapshot tab slice)."""
    del user
    return GLOSSARY


@router.get("/api/snapshot")
def snapshot(
    view: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> JSONResponse:
    """Return market snapshot plus the caller's personal layer when allowed."""
    uid = int(user["id"])
    full = engine.snapshot_for_user(uid)
    full["auth_user"] = user
    full["personal_locked"] = bool(is_guest(user) or full.get("personal_locked"))
    # Always ensure glossary is present even if the shared snap omitted it.
    if not full.get("glossary"):
        full["glossary"] = GLOSSARY
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
        if not sliced.get("glossary"):
            sliced["glossary"] = GLOSSARY
        return JSONResponse(sliced)
    return JSONResponse(full)


@router.get("/api/fund-flow")
async def fund_flow(
    force: bool = Query(default=False),
    user: dict = Depends(current_user_required),
) -> dict:
    """Refresh East Money week/month fund-flow boards for the funds tab."""
    del user
    return await engine.refresh_fund_flow(force=force)


@router.get("/api/health")
def health() -> dict:
    """Liveness + data-health probe (degraded / source fail rates)."""
    snap = engine.snapshot or {}
    h = snap.get("health") if isinstance(snap.get("health"), dict) else {}
    return {
        "ok": True,
        "updated_at": snap.get("updated_at"),
        "degraded": bool(h.get("degraded")),
        "level": h.get("level") or "ok",
        "score": h.get("score"),
        "fail_rates": h.get("fail_rates") or {},
        "failed_sources": h.get("failed_sources") or [],
        "stale_seconds": h.get("stale_seconds"),
        "tips": list(h.get("tips") or [])[:6],
        "trading_day": h.get("trading_day"),
    }


@router.get("/api/export/boards")
def export_boards() -> dict:
    """Public soft board list for news-radar (no auth; local/VPS loopback use)."""
    snap = engine.snapshot or {}
    seen: set[str] = set()
    boards: list[dict] = []
    for pool in ("hot_boards", "pin_boards", "ice_boards", "favorite_boards"):
        for raw in snap.get(pool) or []:
            if not isinstance(raw, dict):
                continue
            bk = str(raw.get("bk") or "").upper()
            name = str(raw.get("name") or "")
            if not bk or bk in seen:
                continue
            seen.add(bk)
            boards.append(
                {
                    "bk": bk,
                    "name": name,
                    "pct": raw.get("pct"),
                    "amount": raw.get("amount") or 0,
                    "leader": str(raw.get("leader_name") or ""),
                    "kind": raw.get("kind") or "",
                }
            )
    # Fund-flow boards fill gaps when hot cards are thin.
    ff = snap.get("fund_flow") if isinstance(snap.get("fund_flow"), dict) else {}
    api_raw = ff.get("api_raw") if isinstance(ff, dict) else {}
    day = (api_raw or {}).get("day") if isinstance(api_raw, dict) else {}
    for kind in ("industry", "concept"):
        for raw in (day or {}).get(kind) or []:
            if not isinstance(raw, dict):
                continue
            bk = str(raw.get("bk") or "").upper()
            if not bk or bk in seen:
                continue
            seen.add(bk)
            boards.append(
                {
                    "bk": bk,
                    "name": str(raw.get("name") or ""),
                    "pct": raw.get("pct"),
                    "amount": 0,
                    "leader": str(raw.get("leader_name") or ""),
                    "kind": kind,
                }
            )
    return {
        "ok": True,
        "updated_at": snap.get("updated_at"),
        "boards": boards,
        "n": len(boards),
    }


@router.get("/api/chart/{code}")
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


@router.get("/api/theme-chain")
def theme_chain(
    theme: str = Query(..., min_length=1),
    limit: int = Query(default=14, ge=3, le=40),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return day outcomes for one theme chain (oldest→newest for timeline UI)."""
    del user
    from market_desk.db import load_theme_outcomes_for_theme
    from market_desk.mainline import theme_key

    key = theme_key(theme) or str(theme or "").strip()
    rows = load_theme_outcomes_for_theme(key, limit=limit)
    timeline = list(reversed(rows))
    return {"ok": True, "theme_key": key, "n": len(timeline), "items": timeline}


@router.post("/api/trend-override")
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


@router.post("/api/theme-reputation")
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
