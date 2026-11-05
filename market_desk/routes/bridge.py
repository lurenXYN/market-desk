"""Broker bridge HTTP (PTrade poller ↔ desk). Auth via shared bridge token."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from market_desk.config import DATA_DIR
from market_desk.deps import current_admin_required
from market_desk.engine import engine
from market_desk.exec import get_exec_engine
from market_desk.exec.ptrade_bridge import (
    build_bridge_ingest_payload,
    build_exec_intents,
    get_bridge_token,
    intents_etag,
    set_bridge_token,
    token_configured,
    verify_bridge_token,
)

router = APIRouter()


def _require_bridge_token(
    x_desk_bridge_token: str | None = Header(default=None, alias="X-Desk-Bridge-Token"),
    token: str | None = Query(default=None, description="Fallback when header blocked"),
) -> None:
    """Reject when bridge token missing or wrong."""
    provided = x_desk_bridge_token or token
    if not verify_bridge_token(provided):
        raise HTTPException(401, "invalid bridge token")


class PtradePositionsIn(BaseModel):
    """PTrade position/account push body."""

    account: dict[str, Any] | None = None
    positions: list[dict[str, Any]] = Field(default_factory=list)
    trades: list[dict[str, Any]] = Field(default_factory=list)
    gateway: str = "ptrade"


@router.get("/api/bridge/ptrade/health")
def bridge_ptrade_health(
    _: None = Depends(_require_bridge_token),
) -> dict:
    """Cheap liveness for the PTrade poller."""
    from market_desk.sim_desk import portfolio_stats

    stats = portfolio_stats()
    return {
        "ok": True,
        "broker": "ptrade",
        "token_configured": True,
        "gate_ok": bool(stats.get("gate_ok")),
        "gate_readonly_ok": bool(stats.get("gate_readonly_ok")),
    }


@router.post("/api/bridge/ptrade/positions")
def bridge_ptrade_positions(
    body: PtradePositionsIn,
    _: None = Depends(_require_bridge_token),
) -> dict:
    """Ingest PTrade positions/trades into the read-only gateway (stage 1)."""
    payload = build_bridge_ingest_payload(body.model_dump())
    # Drop private helper keys before persist.
    clean = {
        "gateway": payload.get("gateway") or "ptrade",
        "account": payload.get("account"),
        "positions": payload.get("positions") or [],
        "trades": payload.get("trades") or [],
    }
    path = str((DATA_DIR / "broker_readonly.json").resolve())
    return get_exec_engine().ingest_broker_snapshot(clean, persist_path=path)


@router.get("/api/bridge/ptrade/intents")
def bridge_ptrade_intents(
    _: None = Depends(_require_bridge_token),
    send_allowed: bool = Query(
        default=False,
        description="Stage 2 only; keep false for read-only pollers",
    ),
) -> dict:
    """Return buy/sell intents for PTrade (watch-only unless send_allowed)."""
    # Shared market snap; personal sell_advice empty here — admin personal
    # intents stay on cookie routes. Bridge uses shared recommend + empty sells
    # unless we later bind a bridge user_id.
    snap = dict(engine.snapshot or {})
    out = build_exec_intents(snap, send_allowed=bool(send_allowed))
    out["etag"] = intents_etag(out)
    out["token_hint"] = "header X-Desk-Bridge-Token"
    return out


@router.get("/api/ops/ptrade-bridge")
def ops_ptrade_bridge_status(user: dict = Depends(current_admin_required)) -> dict:
    """Admin view: whether the PTrade bridge token is configured (no plaintext)."""
    del user
    tok = get_bridge_token()
    masked = ""
    if tok:
        masked = tok[:4] + "…" + tok[-4:] if len(tok) > 8 else "****"
    return {
        "ok": True,
        "configured": token_configured(),
        "token_masked": masked,
        "endpoints": {
            "health": "GET /api/bridge/ptrade/health",
            "positions": "POST /api/bridge/ptrade/positions",
            "intents": "GET /api/bridge/ptrade/intents",
        },
        "doc": "deploy/PTRADE_GTHT.md",
    }


@router.post("/api/ops/ptrade-bridge-token")
def ops_ptrade_bridge_token_rotate(
    user: dict = Depends(current_admin_required),
    rotate: bool = Query(default=True),
) -> dict:
    """Create or rotate the PTrade bridge token (admin; plaintext returned once)."""
    del user
    if not rotate and token_configured():
        return {
            "ok": True,
            "configured": True,
            "rotated": False,
            "hint": "已有 token；要轮换请 rotate=true",
        }
    return set_bridge_token()
