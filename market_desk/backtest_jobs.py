"""In-memory async backtest job queue (admin)."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = asyncio.Lock()
_MAX_JOBS = 20


def _prune_jobs() -> None:
    """Drop oldest finished jobs beyond ``_MAX_JOBS``."""
    if len(_JOBS) <= _MAX_JOBS:
        return
    finished = sorted(
        (
            (jid, j)
            for jid, j in _JOBS.items()
            if j.get("status") in ("done", "error")
        ),
        key=lambda kv: float(kv[1].get("updated_at") or 0),
    )
    overflow = len(_JOBS) - _MAX_JOBS
    for jid, _ in finished[: max(0, overflow)]:
        _JOBS.pop(jid, None)


async def start_backtest_job(params: dict[str, Any], *, user_id: int = 0) -> str:
    """Create a job and schedule ``run_signal_backtest`` in the background."""
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    async with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "user_id": int(user_id or 0),
            "params": dict(params),
            "progress": {"done": 0, "total": 0, "pct": 0},
            "result": None,
            "error": None,
        }
        _prune_jobs()
    asyncio.create_task(_run_job(job_id))
    return job_id


async def _run_job(job_id: str) -> None:
    """Execute one backtest job and store the result."""
    from market_desk.backtest import (
        MAX_BACKTEST_ASYNC_SPAN_DAYS,
        run_signal_backtest,
    )
    from market_desk.db import save_backtest_run

    async with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        params = dict(job.get("params") or {})
        job["status"] = "running"
        job["updated_at"] = time.time()

    def progress_cb(done: int, total: int) -> None:
        j = _JOBS.get(job_id)
        if not j:
            return
        tot = max(1, int(total))
        d = max(0, int(done))
        j["progress"] = {
            "done": d,
            "total": tot,
            "pct": round(100.0 * min(d, tot) / tot, 1),
        }
        j["updated_at"] = time.time()

    try:
        out = await run_signal_backtest(
            date_from=str(params.get("date_from") or "")[:10],
            date_to=str(params.get("date_to") or "")[:10],
            mode=str(params.get("mode") or "plan"),
            include_sells=bool(params.get("include_sells", True)),
            ready_only=bool(params.get("ready_only")),
            limit=int(params.get("limit") or 120),
            dry_run=False,
            vol_min_ratio=params.get("vol_min_ratio"),
            slip_pct=params.get("slip_pct"),
            gap_pct=params.get("gap_pct"),
            fidelity=str(params.get("fidelity") or "daily"),
            max_span=int(MAX_BACKTEST_ASYNC_SPAN_DAYS),
            progress_cb=progress_cb,
        )
        if not out.get("ok"):
            async with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j["status"] = "error"
                    j["error"] = str(out.get("detail") or "backtest failed")
                    j["updated_at"] = time.time()
            return
        if params.get("persist"):
            try:
                run = save_backtest_run(
                    result=out,
                    created_by=int(params.get("user_id") or 0),
                    label=str(params.get("label") or "").strip(),
                )
                out["run_id"] = run.get("id")
                out["persisted"] = True
            except Exception as exc:
                out["persist_error"] = str(exc)
        async with _LOCK:
            j = _JOBS.get(job_id)
            if j:
                j["status"] = "done"
                j["result"] = out
                j["progress"] = {"done": 1, "total": 1, "pct": 100}
                j["updated_at"] = time.time()
    except Exception as exc:
        async with _LOCK:
            j = _JOBS.get(job_id)
            if j:
                j["status"] = "error"
                j["error"] = f"{type(exc).__name__}: {exc}"[:300]
                j["updated_at"] = time.time()


def get_backtest_job(job_id: str) -> dict[str, Any] | None:
    """Return a job snapshot for polling."""
    j = _JOBS.get(str(job_id or ""))
    if not j:
        return None
    return {
        "id": j.get("id"),
        "status": j.get("status"),
        "progress": j.get("progress"),
        "error": j.get("error"),
        "params": j.get("params"),
        "created_at": j.get("created_at"),
        "updated_at": j.get("updated_at"),
        "result": j.get("result") if j.get("status") == "done" else None,
    }
