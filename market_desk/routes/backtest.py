"""Backtest runs, jobs, comparisons, grids and applying run forms."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from market_desk.deps import current_admin_required

router = APIRouter()


class BacktestIn(BaseModel):
    """Parameters for a lightweight signal-replay backtest run."""

    date_from: str
    date_to: str
    mode: str = "wait"
    include_sells: bool = True
    ready_only: bool = False
    limit: int = Field(default=120, ge=20, le=400)
    dry_run: bool = False
    # Realism: volume vs prior median (0=off); buy/sell slip %; gap-down threshold %.
    vol_min_ratio: float | None = Field(default=None, ge=0, le=2)
    slip_pct: float | None = Field(default=None, ge=0, le=3)
    gap_pct: float | None = Field(default=None, ge=0, le=10)
    # Persist into signal_backtest_run / fill (never signals.payload).
    persist: bool = False
    label: str = ""
    # daily | minute (minute uses today's trends for day0 chase order).
    fidelity: str = "daily"
    # When True (or span>90), run as background job.
    async_job: bool = False


@router.post("/api/backtest/run")
async def backtest_run(
    body: BacktestIn,
    user: dict = Depends(current_admin_required),
) -> dict:
    """Replay paper signals with daily OHLC simulated fills (admin only)."""
    from datetime import datetime as _dt

    from market_desk.backtest import (
        MAX_BACKTEST_ASYNC_SPAN_DAYS,
        MAX_BACKTEST_SPAN_DAYS,
        run_signal_backtest,
    )
    from market_desk.backtest_jobs import start_backtest_job
    from market_desk.db import save_backtest_run

    mode = str(body.mode or "wait").strip().lower()
    if mode not in ("wait", "plan", "mid"):
        raise HTTPException(400, "mode must be wait|plan|mid")
    fid = str(body.fidelity or "daily").strip().lower()
    if fid not in ("daily", "minute"):
        fid = "daily"

    span_days = 0
    try:
        a = _dt.strptime(str(body.date_from)[:10], "%Y-%m-%d")
        b = _dt.strptime(str(body.date_to)[:10], "%Y-%m-%d")
        span_days = (b - a).days + 1
    except ValueError:
        span_days = 0
    use_async = bool(body.async_job) or (
        not body.dry_run and span_days > int(MAX_BACKTEST_SPAN_DAYS)
    )
    if use_async and body.dry_run:
        raise HTTPException(400, "dry_run cannot be async")
    if use_async:
        if span_days > int(MAX_BACKTEST_ASYNC_SPAN_DAYS):
            raise HTTPException(
                400,
                f"异步回测跨度最多 {MAX_BACKTEST_ASYNC_SPAN_DAYS} 天",
            )
        job_id = await start_backtest_job(
            {
                "date_from": str(body.date_from)[:10],
                "date_to": str(body.date_to)[:10],
                "mode": mode,
                "include_sells": bool(body.include_sells),
                "ready_only": bool(body.ready_only),
                "limit": int(body.limit),
                "vol_min_ratio": body.vol_min_ratio,
                "slip_pct": body.slip_pct,
                "gap_pct": body.gap_pct,
                "fidelity": fid,
                "persist": bool(body.persist),
                "label": str(body.label or "").strip(),
                "user_id": int(user.get("id") or 0),
            },
            user_id=int(user.get("id") or 0),
        )
        return {
            "ok": True,
            "async": True,
            "job_id": job_id,
            "status": "queued",
            "max_span_days": MAX_BACKTEST_ASYNC_SPAN_DAYS,
            "note": "已提交异步回测，请轮询 /api/backtest/jobs/{id}",
        }

    out = await run_signal_backtest(
        date_from=str(body.date_from)[:10],
        date_to=str(body.date_to)[:10],
        mode=mode,
        include_sells=bool(body.include_sells),
        ready_only=bool(body.ready_only),
        limit=int(body.limit),
        dry_run=bool(body.dry_run),
        vol_min_ratio=body.vol_min_ratio,
        slip_pct=body.slip_pct,
        gap_pct=body.gap_pct,
        fidelity=fid,
        max_span=int(MAX_BACKTEST_SPAN_DAYS),
    )
    if not out.get("ok"):
        raise HTTPException(400, str(out.get("detail") or "backtest failed"))
    if body.persist and not body.dry_run:
        try:
            run = save_backtest_run(
                result=out,
                created_by=int(user.get("id") or 0),
                label=str(body.label or "").strip(),
            )
            out["run_id"] = run.get("id")
            out["run"] = run
            out["persisted"] = True
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return out


@router.get("/api/backtest/jobs/{job_id}")
def backtest_job_get(
    job_id: str,
    user: dict = Depends(current_admin_required),
) -> dict:
    """Poll an async backtest job."""
    del user
    from market_desk.backtest_jobs import get_backtest_job

    job = get_backtest_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {"ok": True, "job": job}


@router.get("/api/backtest/runs")
def backtest_runs_list(
    limit: int = Query(default=30, ge=1, le=100),
    user: dict = Depends(current_admin_required),
) -> dict:
    """List saved backtest runs (headers only)."""
    del user
    from market_desk.db import list_backtest_runs

    return {"ok": True, "runs": list_backtest_runs(limit=limit)}


@router.get("/api/backtest/runs/{run_id}")
def backtest_run_get(
    run_id: int,
    user: dict = Depends(current_admin_required),
) -> dict:
    """Load one saved run with fill rows for the backtest table."""
    del user
    from market_desk.db import get_backtest_run

    run = get_backtest_run(int(run_id), with_fills=True)
    if not run:
        raise HTTPException(404, "run not found")
    summary = run.get("summary") or {}
    return {
        "ok": True,
        "dry_run": False,
        "persisted": True,
        "run_id": run.get("id"),
        "run": run,
        "date_from": run.get("date_from"),
        "date_to": run.get("date_to"),
        "mode": run.get("mode"),
        "ready_only": run.get("ready_only"),
        "include_sells": run.get("include_sells"),
        "realism": {
            "vol_min_ratio": run.get("vol_min_ratio"),
            "slip_pct": run.get("slip_pct"),
            "gap_pct": run.get("gap_pct"),
        },
        "n": run.get("item_n"),
        "summary": summary,
        "items": run.get("items") or [],
        "note": run.get("note") or "",
        "disclaimer": (
            "已存档回测结果（独立表 signal_backtest_*，未写入 signals.payload）。"
        ),
    }


@router.delete("/api/backtest/runs/{run_id}")
def backtest_run_delete(
    run_id: int,
    user: dict = Depends(current_admin_required),
) -> dict:
    """Delete one saved backtest run and its fills."""
    del user
    from market_desk.db import delete_backtest_run

    ok = delete_backtest_run(int(run_id))
    if not ok:
        raise HTTPException(404, "run not found")
    return {"ok": True, "deleted": run_id}


@router.post("/api/backtest/runs/clear")
def backtest_runs_clear(
    keep: int = Query(default=0, ge=0, le=100),
    user: dict = Depends(current_admin_required),
) -> dict:
    """Wipe saved runs, optionally keeping the newest ``keep``."""
    del user
    from market_desk.db import clear_backtest_runs

    deleted = clear_backtest_runs(keep=int(keep))
    return {"ok": True, "deleted": deleted, "keep": int(keep)}


@router.get("/api/backtest/compare")
def backtest_compare(
    ids: str = Query(default="", description="Comma-separated run ids"),
    user: dict = Depends(current_admin_required),
) -> dict:
    """Compare summary metrics across saved parameter groups."""
    del user
    from market_desk.db import compare_backtest_runs

    raw = [x.strip() for x in str(ids or "").split(",") if x.strip()]
    return compare_backtest_runs(raw)


class BacktestVsFilledIn(BaseModel):
    """Compare simulated fills against real traded outcomes."""

    items: list[dict[str, Any]] | None = None
    run_id: int | None = None
    date_from: str = ""
    date_to: str = ""


@router.post("/api/backtest/vs-filled")
def backtest_vs_filled(
    body: BacktestVsFilledIn,
    user: dict = Depends(current_admin_required),
) -> dict:
    """Side-by-side paper sim vs filled trades for the same code/day."""
    del user
    from market_desk.backtest import compare_sim_vs_filled
    from market_desk.db import get_backtest_run

    items = list(body.items or [])
    date_from = str(body.date_from or "")[:10]
    date_to = str(body.date_to or "")[:10]
    if body.run_id and not items:
        run = get_backtest_run(int(body.run_id))
        if not run:
            raise HTTPException(404, "run not found")
        items = list(run.get("items") or [])
        date_from = date_from or str(run.get("date_from") or "")[:10]
        date_to = date_to or str(run.get("date_to") or "")[:10]
    if not items:
        raise HTTPException(400, "需要 items 或 run_id")
    return compare_sim_vs_filled(items, date_from=date_from, date_to=date_to)


class BacktestGridIn(BaseModel):
    """Run several parameter variants and persist each (admin)."""

    date_from: str
    date_to: str
    include_sells: bool = True
    ready_only: bool = False
    limit: int = Field(default=120, ge=20, le=400)
    variants: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/api/backtest/grid")
async def backtest_grid(
    body: BacktestGridIn,
    user: dict = Depends(current_admin_required),
) -> dict:
    """Run up to 6 mode/vol/slip/gap variants and archive each run."""
    from market_desk.backtest import run_signal_backtest
    from market_desk.db import save_backtest_run

    variants = list(body.variants or [])[:6]
    if not variants:
        variants = [
            {"mode": "plan", "vol_min_ratio": 0.4, "slip_pct": 0.15, "gap_pct": 1.0},
            {"mode": "wait", "vol_min_ratio": 0.4, "slip_pct": 0.15, "gap_pct": 1.0},
            {"mode": "plan", "vol_min_ratio": 0.6, "slip_pct": 0.25, "gap_pct": 1.0},
        ]
    runs: list[dict[str, Any]] = []
    for i, var in enumerate(variants):
        mode = str(var.get("mode") or "plan").strip().lower()
        if mode not in ("wait", "plan", "mid"):
            mode = "plan"
        out = await run_signal_backtest(
            date_from=str(body.date_from)[:10],
            date_to=str(body.date_to)[:10],
            mode=mode,
            include_sells=bool(body.include_sells),
            ready_only=bool(body.ready_only),
            limit=int(body.limit),
            dry_run=False,
            vol_min_ratio=var.get("vol_min_ratio"),
            slip_pct=var.get("slip_pct"),
            gap_pct=var.get("gap_pct"),
        )
        if not out.get("ok"):
            runs.append({"ok": False, "detail": out.get("detail"), "variant": var})
            continue
        label = str(var.get("label") or "").strip() or (
            f"grid{i+1}·{mode}·v{var.get('vol_min_ratio', '—')}"
        )
        run = save_backtest_run(
            result=out,
            created_by=int(user.get("id") or 0),
            label=label,
        )
        runs.append({"ok": True, "run_id": run.get("id"), "run": run, "variant": var})
    return {"ok": True, "n": len(runs), "runs": runs}


@router.post("/api/backtest/runs/{run_id}/apply-form")
def backtest_apply_form(
    run_id: int,
    user: dict = Depends(current_admin_required),
) -> dict:
    """Return form fields from a saved run so the UI can refill controls."""
    del user
    from market_desk.db import get_backtest_run

    run = get_backtest_run(int(run_id), with_fills=False)
    if not run:
        raise HTTPException(404, "run not found")
    return {
        "ok": True,
        "form": {
            "date_from": run.get("date_from"),
            "date_to": run.get("date_to"),
            "mode": run.get("mode") or "plan",
            "ready_only": bool(run.get("ready_only")),
            "include_sells": bool(run.get("include_sells")),
            "vol_min_ratio": run.get("vol_min_ratio"),
            "slip_pct": run.get("slip_pct"),
            "gap_pct": run.get("gap_pct"),
            "label": run.get("label") or "",
        },
    }
