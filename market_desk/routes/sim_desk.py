"""Member-facing paper trading book API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from market_desk.deps import current_member_required

router = APIRouter()


@router.get("/api/sim-desk")
def sim_desk_book(
    user: dict = Depends(current_member_required),
    trades: int = Query(default=80, ge=10, le=300),
) -> dict:
    """Paper account, open lots, trade log and start-gate snapshot."""
    del user
    from market_desk.db import load_open_sim_lots
    from market_desk.sim_desk import account_snapshot, recent_trades, start_gate_snapshot

    gate = start_gate_snapshot()
    return {
        "ok": True,
        "account": account_snapshot(),
        "gate": gate,
        "open_lots": load_open_sim_lots(),
        "trades": recent_trades(trades),
    }
