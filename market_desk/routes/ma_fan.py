"""MA-fan tab: stored scan, admin background rescan and its progress."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from market_desk.deps import current_admin_required, current_user_required
from market_desk.engine import engine

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/ma-fan")
def api_ma_fan(
    date: str | None = Query(default=None),
    user: dict = Depends(current_user_required),
) -> dict:
    """Return one night's MA-fan scan (review-style day list)."""
    del user
    from market_desk.db import list_ma_fan_dates, load_ma_fan_day
    from market_desk.ma_fan import attach_review_flags_to_ma_fan

    dates = list_ma_fan_dates(limit=40)
    want = str(date or "").strip()[:10]
    if not want:
        want = dates[0] if dates else ""
    body = load_ma_fan_day(want) if want else None
    warn = None
    if body:
        try:
            body = attach_review_flags_to_ma_fan(body, want, snapshot=engine.snapshot)
        except Exception:
            log.exception("ma_fan review flags failed date=%s", want)
            warn = "复盘交集标注失败（已记日志），列表照常显示"
    return {
        "ok": True,
        "view_date": want or None,
        "dates": dates,
        "scan": body,
        "warn": warn,
        "note": "18/20/22 点分档扫成交额榜并合并；标签含额档与主线同主题旁注。观察层，不进 ready。",
    }


_MA_FAN_TASKS: set[asyncio.Task] = set()


@router.post("/api/ma-fan/run")
async def api_ma_fan_run(
    force: bool = Query(default=False),
    user: dict = Depends(current_admin_required),
) -> dict:
    """Admin-only: start MA-fan slices in the background; poll ``/api/ma-fan/progress``.

    ``force=True`` rebuilds 0–1000 for today and is limited to once per cooldown.
    """
    from datetime import datetime as _dt

    from market_desk.ma_fan import force_cooldown_left, scan_progress, try_claim_scan

    day = _dt.now().strftime("%Y-%m-%d")
    if force:
        left = force_cooldown_left()
        if left > 0:
            raise HTTPException(
                429, f"重扫冷却中（防数据源封禁），约 {left // 60 + 1} 分钟后可再扫"
            )
    kind = "force" if force else "slice"
    if not try_claim_scan(kind, day):
        raise HTTPException(409, "已有均线发散扫描在跑，请等进度条走完")
    log.info("ma_fan %s rescan started by %s day=%s", kind, user.get("username"), day)

    async def _job() -> None:
        try:
            await engine._run_ma_fan_scan(day, force_all=bool(force), claimed=True)
        except Exception:
            # run_ma_fan_all_due_slices already logged and released the slot.
            pass
        finally:
            from market_desk.ma_fan import release_scan, scan_progress as _sp

            if _sp().get("running"):
                release_scan()

    task = asyncio.create_task(_job())
    _MA_FAN_TASKS.add(task)
    task.add_done_callback(_MA_FAN_TASKS.discard)
    return {"ok": True, "started": True, "view_date": day, "progress": scan_progress()}


@router.get("/api/ma-fan/progress")
def api_ma_fan_progress(user: dict = Depends(current_admin_required)) -> dict:
    """Admin-only: current / last MA-fan scan progress for the progress bar."""
    del user
    from market_desk.ma_fan import scan_progress

    return {"ok": True, "progress": scan_progress()}
