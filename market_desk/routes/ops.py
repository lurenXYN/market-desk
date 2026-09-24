"""Backups, ops checks, logs, client error reports and daily patches."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from market_desk.db import export_user_backup_payload, import_user_backup_payload
from market_desk.deps import (
    current_admin_required,
    current_member_required,
    current_user_optional,
)
from market_desk.engine import engine

log = logging.getLogger(__name__)
router = APIRouter()


class BackupIn(BaseModel):
    """JSON backup import payload."""

    replace: bool = False
    payload: dict[str, Any]


@router.get("/api/backup")
def backup_export(user: dict = Depends(current_member_required)) -> dict:
    """Export this user's personal desk data as JSON."""
    return {"ok": True, "backup": export_user_backup_payload(int(user["id"]))}


@router.get("/api/backup/auto")
def backup_auto_list(
    limit: int = Query(default=12, ge=1, le=40),
    user: dict = Depends(current_admin_required),
) -> dict:
    """List recent auto JSON / SQLite backups on the server (admin)."""
    from market_desk.backup_store import list_auto_backups
    from market_desk.settings import get_settings

    return {
        "ok": True,
        "items": list_auto_backups(limit=limit),
        "keep": int(get_settings().get("backup_keep") or 30),
    }


@router.get("/api/backup/auto/download")
def backup_auto_download(
    name: str = Query(..., min_length=3, max_length=120),
    user: dict = Depends(current_admin_required),
):
    """Download one auto backup file (admin). Path traversal rejected."""
    del user
    from market_desk.backup_store import resolve_backup_file

    path = resolve_backup_file(name)
    if path is None:
        raise HTTPException(404, "backup not found")
    media = "application/json" if path.suffix == ".json" else "application/octet-stream"
    return FileResponse(
        path,
        media_type=media,
        filename=path.name,
    )


@router.get("/api/ops/db-integrity")
def ops_db_integrity(user: dict = Depends(current_admin_required)) -> dict:
    """Admin probe for live desk.db integrity."""
    del user
    from market_desk.backup_store import check_db_integrity

    return {"ok": True, **check_db_integrity()}


@router.get("/api/ops/logs")
def ops_logs(
    lines: int = Query(default=200, ge=1, le=2000),
    q: str = Query(default=""),
    level: str = Query(default=""),
    format: str = Query(default="text"),
    user: dict = Depends(current_admin_required),
) -> Any:
    """Admin tail of ``data/logs/market-desk.log`` (``format=json`` for API use)."""
    del user
    from fastapi.responses import PlainTextResponse

    from market_desk.logs import LOG_FILE, tail_log

    rows = tail_log(lines, q, level=level)
    if str(format).lower() == "json":
        return {"ok": True, "file": str(LOG_FILE), "lines": rows}
    head = f"# {LOG_FILE} · q={q or '-'} · level={level or 'all'} · {len(rows)} 行\n"
    return PlainTextResponse(head + ("\n".join(rows) or "(无匹配日志)"))


class ClientErrorIn(BaseModel):
    """Front-end failure report written to the server log."""

    where: str = Field(default="", max_length=60)
    message: str = Field(default="", max_length=500)
    status: int | None = None
    detail: str = Field(default="", max_length=500)


_CLIENT_ERR_HITS: dict[str, list[float]] = {}


@router.post("/api/ops/client-error")
def ops_client_error(
    body: ClientErrorIn,
    request: Request,
    user: dict | None = Depends(current_user_optional),
) -> dict:
    """Record a browser-side load/render failure (max 20 per 10 min per caller)."""
    import time as _time

    who = str((user or {}).get("username") or (request.client.host if request.client else "?"))
    now = _time.monotonic()
    hits = [t for t in _CLIENT_ERR_HITS.get(who, []) if now - t < 600]
    if len(hits) >= 20:
        _CLIENT_ERR_HITS[who] = hits
        return {"ok": True, "dropped": True}
    hits.append(now)
    _CLIENT_ERR_HITS[who] = hits
    log.warning(
        "client-error user=%s where=%s status=%s msg=%s detail=%s",
        who,
        body.where,
        body.status,
        body.message.replace("\n", " ")[:500],
        body.detail.replace("\n", " ")[:500],
    )
    return {"ok": True}


@router.get("/api/ops/check")
def ops_check(user: dict = Depends(current_admin_required)) -> dict:
    """One-shot VPS / deploy health checklist for admins."""
    del user
    import os
    from pathlib import Path

    from market_desk.backup_store import check_db_integrity, list_auto_backups
    from market_desk.db import DB_PATH
    from market_desk.eastmoney import clist_runtime_status

    checks: list[dict] = []
    integ = check_db_integrity()
    checks.append(
        {
            "id": "db_integrity",
            "title": "SQLite 完整性",
            "level": "ok" if integ.get("ok") else "bad",
            "detail": integ.get("detail") or integ.get("path") or "",
        }
    )
    db_ok = Path(DB_PATH).exists()
    size_mb = round(Path(DB_PATH).stat().st_size / (1024 * 1024), 2) if db_ok else 0
    checks.append(
        {
            "id": "db_file",
            "title": "desk.db 文件",
            "level": "ok" if db_ok else "bad",
            "detail": f"{DB_PATH} · {size_mb} MB" if db_ok else "缺失",
        }
    )
    snap = engine.snapshot or {}
    updated = str(snap.get("updated_at") or "")
    health = snap.get("health") if isinstance(snap.get("health"), dict) else {}
    stale = health.get("stale_seconds")
    snap_level = "warn"
    if updated and snap.get("ok"):
        snap_level = "ok"
        if stale is not None and int(stale) > 180:
            snap_level = "warn"
    elif not snap:
        snap_level = "bad"
    checks.append(
        {
            "id": "snapshot",
            "title": "引擎快照",
            "level": snap_level,
            "detail": f"updated={updated or '—'} · score={health.get('score')} · degraded={bool(health.get('degraded'))}",
        }
    )
    try:
        clist = clist_runtime_status()
        checks.append(
            {
                "id": "clist",
                "title": "东财 clist",
                "level": "warn" if clist.get("backoff") else "ok",
                "detail": (
                    f"host={clist.get('host') or '—'} · backoff={clist.get('backoff')} "
                    f"剩{clist.get('backoff_sec') or 0}s"
                ),
            }
        )
    except Exception as exc:
        checks.append(
            {
                "id": "clist",
                "title": "东财 clist",
                "level": "warn",
                "detail": f"{type(exc).__name__}: {exc}"[:120],
            }
        )
    backups = list_auto_backups(limit=3)
    if backups:
        age_h = None
        try:
            from datetime import datetime as _dt

            mtime = backups[0].get("mtime") or backups[0].get("modified")
            if isinstance(mtime, (int, float)):
                age_h = round(( _dt.now().timestamp() - float(mtime)) / 3600.0, 1)
            elif isinstance(mtime, str) and len(mtime) >= 16:
                ts = _dt.strptime(mtime[:19], "%Y-%m-%d %H:%M:%S")
                age_h = round((_dt.now() - ts).total_seconds() / 3600.0, 1)
        except Exception:
            age_h = None
        lvl = "ok"
        if age_h is not None and age_h > 48:
            lvl = "warn"
        if age_h is not None and age_h > 120:
            lvl = "bad"
        checks.append(
            {
                "id": "backup",
                "title": "自动备份",
                "level": lvl,
                "detail": f"最近 {backups[0].get('name')} · 约 {age_h if age_h is not None else '—'}h 前 · 共{len(backups)}+",
            }
        )
    else:
        checks.append(
            {
                "id": "backup",
                "title": "自动备份",
                "level": "warn",
                "detail": "尚无自动备份文件",
            }
        )
    nr = snap.get("news_radar") if isinstance(snap.get("news_radar"), dict) else {}
    if nr.get("enabled"):
        st = str(nr.get("status") or "")
        checks.append(
            {
                "id": "news_radar",
                "title": "新闻雷达",
                "level": "ok" if st in ("online", "stale") else "warn",
                "detail": nr.get("status_label") or st or "enabled",
            }
        )
    else:
        checks.append(
            {
                "id": "news_radar",
                "title": "新闻雷达",
                "level": "ok",
                "detail": "未启用（软关）",
            }
        )
    bad_n = sum(1 for c in checks if c.get("level") == "bad")
    warn_n = sum(1 for c in checks if c.get("level") == "warn")
    overall = "ok" if bad_n == 0 and warn_n == 0 else ("bad" if bad_n else "warn")
    return {
        "ok": True,
        "overall": overall,
        "checks": checks,
        "host": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
        "pid": os.getpid(),
    }


def _apply_daily_patches_payload(*, force: bool = True) -> dict:
    """Shared patch apply + in-memory history refresh for admin/internal routes."""
    from market_desk.db import load_daily
    from market_desk.patches_apply import apply_pending_daily_patches, verify_day_breadth

    applied = apply_pending_daily_patches(force=bool(force))
    hist = load_daily(14)
    try:
        if isinstance(engine.snapshot, dict):
            engine.snapshot["history"] = hist
    except Exception:
        pass
    return {
        "ok": True,
        "applied": applied,
        "history_n": len(hist),
        "day_2026_09_21": verify_day_breadth("2026-09-21"),
    }


@router.get("/api/admin/daily-patches")
def admin_list_daily_patches(
    admin: dict = Depends(current_admin_required),
) -> dict:
    """List shipped daily_snapshot patches and whether each id was applied."""
    del admin
    from market_desk.patches_apply import list_daily_patches, verify_day_breadth

    return {
        "ok": True,
        "items": list_daily_patches(),
        "day_2026_09_21": verify_day_breadth("2026-09-21"),
    }


@router.post("/api/admin/daily-patches/apply")
def admin_apply_daily_patches(
    force: bool = Query(default=True),
    admin: dict = Depends(current_admin_required),
) -> dict:
    """Force-apply shipped daily_snapshot patches and refresh in-memory history."""
    del admin
    return _apply_daily_patches_payload(force=bool(force))


@router.post("/api/internal/daily-patches/apply")
def internal_apply_daily_patches(
    request: Request,
    force: bool = Query(default=True),
) -> dict:
    """Localhost-only force apply used by deploy scripts (no login cookie)."""
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "localhost only")
    return _apply_daily_patches_payload(force=bool(force))


@router.post("/api/backup/import")
def backup_import(
    body: BackupIn, user: dict = Depends(current_member_required)
) -> dict:
    """Import a previously exported JSON backup into this user's books."""
    try:
        counts = import_user_backup_payload(
            int(user["id"]), body.payload, replace=bool(body.replace)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    from market_desk.settings import get_settings

    get_settings(refresh=True)
    return {"ok": True, "counts": counts}
