"""Strategy profile, portfolio risk, decision trace and pre-registered experiments."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from market_desk.deps import current_user_required

router = APIRouter()


@router.get("/api/strategy-profile")
def strategy_profile(user: dict = Depends(current_user_required)) -> dict:
    """All trading-philosophy profiles plus the active one."""
    del user
    from market_desk.profiles import profile_summary

    return profile_summary()


@router.get("/api/portfolio-risk")
def portfolio_risk(
    drawdown_pct: float | None = Query(default=None, ge=-100, le=100),
    user: dict = Depends(current_user_required),
) -> dict:
    """Portfolio-level soft caps for the caller's book (concentration/correlation/drawdown)."""
    from market_desk.db.positions import load_positions
    from market_desk.portfolio_risk import portfolio_gate

    uid = int(user["id"])
    positions = load_positions(user_id=uid)
    return portfolio_gate(positions=positions, drawdown_pct=drawdown_pct)


@router.get("/api/decisions")
def decisions(
    trade_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(current_user_required),
) -> dict:
    """Decision trace rows (evidence → action → outcome)."""
    del user
    from market_desk.db.decision_trace import load_decisions, trace_summary

    return {
        "ok": True,
        "trade_date": trade_date,
        "summary": trace_summary(trade_date=trade_date),
        "items": load_decisions(trade_date=trade_date, limit=limit),
    }


@router.get("/api/experiments")
def experiments(
    day: str | None = Query(default=None, description="YYYY-MM-DD; default today"),
    user: dict = Depends(current_user_required),
) -> dict:
    """Pre-registered experiments, their windows and current validation errors."""
    del user
    from market_desk.experiments import due_experiments, load_experiments

    loaded = load_experiments()
    return {
        "ok": loaded.get("ok"),
        "path": loaded.get("path"),
        "errors": loaded.get("errors") or [],
        "items": loaded.get("experiments") or [],
        "due": due_experiments(day),
        "note": "判据先于数据；样本不足或提升不够即判 inconclusive/fail",
    }