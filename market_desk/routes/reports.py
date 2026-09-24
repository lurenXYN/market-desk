"""Daily, end-of-day, tomorrow and morning reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from market_desk.auth import is_guest
from market_desk.deps import current_user_required
from market_desk.engine import engine
from market_desk.report import (
    build_daily_report,
    build_eod_onepager,
    build_morning_brief,
    build_tomorrow_brief,
)

router = APIRouter()


@router.get("/api/report/today")
async def report_today(user: dict = Depends(current_user_required)) -> dict:
    """Return today's markdown journal for copy / download."""
    uid = None if is_guest(user) else int(user["id"])
    snap = engine.snapshot_for_user(int(user["id"]))
    review = await engine.build_review(user_id=uid)
    text = build_daily_report(snapshot=snap, review=review)
    return {"ok": True, "markdown": text, "summary": review.get("summary")}


@router.get("/api/report/eod")
async def report_eod(
    date: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return the compact end-of-day one-pager (phase / switches / exec / P&L)."""
    uid = None if is_guest(user) else int(user["id"])
    snap = engine.snapshot_for_user(int(user["id"]))
    review = await engine.build_review(view_date=date, user_id=uid)
    diary = None
    day_hint = str(date or (review.get("summary") or {}).get("view_date") or "").strip()[:10]
    if uid is not None and day_hint:
        try:
            from market_desk.db import load_exec_diary

            diary = load_exec_diary(user_id=int(uid), trade_date=day_hint, limit=40)
        except Exception:
            diary = None
    brief = build_eod_onepager(snapshot=snap, review=review, diary=diary)
    cached = None
    day = str(date or brief.get("date") or "").strip()[:10]
    if day:
        from market_desk.db import load_setting

        raw = load_setting(f"eod:{day}")
        if isinstance(raw, dict) and raw.get("markdown"):
            cached = raw
    return {
        "ok": True,
        "brief": brief,
        "markdown": brief.get("markdown") or "",
        "cached": cached,
        "auto_saved": bool(cached),
    }


@router.get("/api/report/tomorrow")
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


@router.get("/api/report/morning")
def report_morning(user: dict = Depends(current_user_required)) -> dict:
    """Return the rule-based morning decision brief."""
    snap = engine.snapshot_for_user(int(user["id"]))
    brief = snap.get("morning_brief") or build_morning_brief(snap)
    return {"ok": True, "brief": brief, "markdown": brief.get("markdown") or ""}
