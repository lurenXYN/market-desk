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
                "level": (
                    "warn"
                    if clist.get("backoff")
                    or clist.get("quotes_source") == "tencent"
                    or clist.get("boards_source") == "sina"
                    else "ok"
                ),
                "detail": (
                    f"host={clist.get('host') or '—'} · backoff={clist.get('backoff')} "
                    f"剩{clist.get('backoff_sec') or 0}s · 主板行情源={clist.get('quotes_source')}"
                    f" · 东财暂停剩{clist.get('quotes_pause_sec') or 0}s"
                    f" · 板块源={clist.get('boards_source')}"
                    f" · 板块东财暂停剩{clist.get('boards_pause_sec') or 0}s"
                    f" · 分时源={clist.get('minute_source')}"
                ),
            }
        )
        blocked = clist.get("em_blocked") or []
        today = clist.get("em_today") or {}
        ok_n = sum(int(v.get("ok") or 0) for v in today.values())
        fail_n = sum(int(v.get("fail") or 0) for v in today.values())
        checks.append(
            {
                "id": "em_avail",
                "title": "东财接口可用性",
                "level": "warn" if blocked else "ok",
                "detail": (
                    ("被拦：" + "、".join(f"{b['family']}@{b['since']}" for b in blocked) + " · " if blocked else "")
                    + f"今日请求 成功{ok_n}/失败{fail_n} · 按小时明细 GET /api/ops/em-avail"
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
    radar = snap.get("radar") if isinstance(snap.get("radar"), dict) else {}
    try:
        from market_desk.shadow_health import shadow_check, shadow_health

        checks.append(shadow_check(shadow_health(1), radar.get("narrative")))
    except Exception as exc:
        checks.append(
            {"id": "shadows", "title": "影子账本", "level": "warn", "detail": f"{type(exc).__name__}: {exc}"[:120]}
        )
    try:
        from market_desk.calendar import today_cn
        from market_desk.db import load_deep_v_day

        day = today_cn().isoformat() if hasattr(today_cn(), "isoformat") else str(today_cn())[:10]
        dv = load_deep_v_day(day) or radar.get("deep_v")
        if isinstance(dv, dict) and dv.get("ok"):
            detail = (
                f"{'深V' if dv.get('deep_v') else '非深V'} · 回撤{dv.get('dd_pct')}%"
                f" · 回补{dv.get('recover')}"
                + (" · 盘中暂定" if dv.get("provisional") else "")
            )
            checks.append({"id": "deep_v", "title": "深V日（中证1000）", "level": "ok", "detail": detail})
        else:
            checks.append(
                {
                    "id": "deep_v",
                    "title": "深V日（中证1000）",
                    "level": "ok",
                    "detail": (dv or {}).get("note") if isinstance(dv, dict) else "尚无分类",
                }
            )
    except Exception as exc:
        checks.append(
            {"id": "deep_v", "title": "深V日（中证1000）", "level": "warn", "detail": f"{type(exc).__name__}: {exc}"[:120]}
        )
    try:
        zs = radar.get("zt_share") if isinstance(radar, dict) else None
        if isinstance(zs, dict) and zs.get("ok"):
            checks.append(
                {
                    "id": "zt_share",
                    "title": "涨停占比抬升",
                    "level": "ok",
                    "detail": f"今日表 {zs.get('n')} 行 · 抬升 {zs.get('lift_n')}",
                }
            )
        else:
            checks.append(
                {
                    "id": "zt_share",
                    "title": "涨停占比抬升",
                    "level": "ok",
                    "detail": (zs or {}).get("note") if isinstance(zs, dict) else "尚无日表",
                }
            )
    except Exception as exc:
        checks.append(
            {"id": "zt_share", "title": "涨停占比抬升", "level": "warn", "detail": f"{type(exc).__name__}: {exc}"[:120]}
        )
    try:
        from market_desk.sim_desk import start_gate_snapshot

        gate = start_gate_snapshot()
        cum = gate.get("cum_pnl_pct")
        cum_s = f"{cum:+.2f}%" if cum is not None else "—"
        checks.append(
            {
                "id": "sim_desk",
                "title": "模拟操盘开工闸",
                "level": "ok" if gate.get("gate_ok") else "warn",
                "detail": (
                    f"{gate.get('gate_label')} · 平仓{gate.get('closed_n', 0)} · "
                    f"持仓{gate.get('open_n', 0)} · 累计{cum_s}"
                ),
                "gate": gate,
            }
        )
    except Exception as exc:
        checks.append(
            {
                "id": "sim_desk",
                "title": "模拟操盘开工闸",
                "level": "warn",
                "detail": f"{type(exc).__name__}: {exc}"[:120],
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
    try:
        from market_desk.notify import serverchan_quota_snapshot

        q = serverchan_quota_snapshot()
        checks.append(
            {
                "id": "serverchan_quota",
                "title": "Server酱额度",
                "level": q.get("level") or "ok",
                "detail": (
                    f"今日 {q.get('used')}/{q.get('cap')} · 剩 {q.get('remaining')}"
                    + (f" · flags {','.join(q.get('flags') or [])}" if q.get("flags") else "")
                ),
                "quota": q,
            }
        )
    except Exception as exc:
        checks.append(
            {
                "id": "serverchan_quota",
                "title": "Server酱额度",
                "level": "warn",
                "detail": f"{type(exc).__name__}: {exc}"[:120],
            }
        )
    try:
        from market_desk.db.bars_cache import bars_cache_stats

        st = bars_cache_stats()
        rows = int(st.get("rows") or 0)
        codes = int(st.get("codes") or 0)
        lvl = "ok" if rows > 0 and codes > 0 else "warn"
        checks.append(
            {
                "id": "bars_cache",
                "title": "日线缓存",
                "level": lvl,
                "detail": (
                    f"{codes} 码 · {rows} 行 · "
                    f"{st.get('min_date') or '—'}→{st.get('max_date') or '—'}"
                ),
                "stats": st,
            }
        )
    except Exception as exc:
        checks.append(
            {
                "id": "bars_cache",
                "title": "日线缓存",
                "level": "warn",
                "detail": f"{type(exc).__name__}: {exc}"[:120],
            }
        )
    try:
        from market_desk.db import load_setting

        attr = load_setting("attribution_last")
        if isinstance(attr, dict) and attr.get("at"):
            checks.append(
                {
                    "id": "attribution_timer",
                    "title": "归因审计",
                    "level": "ok",
                    "detail": f"最近 {attr.get('at')}",
                    "last": attr,
                }
            )
        else:
            checks.append(
                {
                    "id": "attribution_timer",
                    "title": "归因审计",
                    "level": "warn",
                    "detail": "尚无 attribution_last 戳（timer 跑过后写入）",
                }
            )
    except Exception as exc:
        checks.append(
            {
                "id": "attribution_timer",
                "title": "归因审计",
                "level": "warn",
                "detail": f"{type(exc).__name__}: {exc}"[:120],
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


@router.get("/api/ops/shadows")
def ops_shadows(
    days: int = Query(default=10, ge=1, le=40),
    user: dict = Depends(current_admin_required),
) -> dict:
    """Per-day shadow-ledger rows and trigger proximity (empty because quiet vs. broken)."""
    del user
    from market_desk.shadow_health import shadow_health

    return {"ok": True, "days": shadow_health(days)}


@router.get("/api/ops/exec-desk")
def ops_exec_desk(user: dict = Depends(current_admin_required)) -> dict:
    """vnpy-style execution facade snapshot (paper now; live after gate_ok)."""
    del user
    from market_desk.exec import get_exec_engine

    return get_exec_engine().snapshot()


@router.get("/api/ops/serverchan-quota")
def ops_serverchan_quota(user: dict = Depends(current_admin_required)) -> dict:
    """Today's desk Server酱 usage vs free daily cap."""
    del user
    from market_desk.notify import serverchan_quota_snapshot

    return serverchan_quota_snapshot()


@router.get("/api/ops/exec-triggers")
def ops_exec_triggers(user: dict = Depends(current_admin_required)) -> dict:
    """Map current sell advice into unified exec trigger intents."""
    del user
    from market_desk.exec import get_exec_engine

    eng = get_exec_engine()
    triggers = eng.sell_triggers()
    return {
        "ok": True,
        "n": len(triggers),
        "executable_n": sum(1 for t in triggers if t.get("executable")),
        "triggers": triggers,
        "circuit": eng.circuit.to_dict(),
    }


@router.get("/api/ops/fill-rules")
def ops_fill_rules(user: dict = Depends(current_admin_required)) -> dict:
    """Sim ? backtest ? cf fill-rule parity map (vnpy mid-priority)."""
    del user
    from market_desk.fill_rules import fill_rules_parity

    return fill_rules_parity()


@router.get("/api/ops/bars-cache")
def ops_bars_cache(
    code: str | None = Query(default=None),
    user: dict = Depends(current_admin_required),
) -> dict:
    """Daily bar cache stats; optional per-code recent rows."""
    del user
    from market_desk.db.bars_cache import bars_cache_stats, load_daily_bars

    out: dict[str, Any] = {"ok": True, "stats": bars_cache_stats()}
    if code:
        out["code"] = str(code).zfill(6)
        out["bars"] = load_daily_bars(str(code), limit=40)
    return out


@router.get("/api/ops/sim-desk")
def ops_sim_desk(
    backfill: bool = Query(default=False),
    replay: bool = Query(default=False),
    reset: bool = Query(default=False, description="Wipe book and reset cash to SIM_INITIAL_CASH"),
    user: dict = Depends(current_admin_required),
) -> dict:
    """Paper book + auto-order start gate (ever-ready → sell signal)."""
    del user
    from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH
    from market_desk.db.sim_desk import get_sim_account_epoch, wipe_sim_book
    from market_desk.db import load_open_sim_lots, load_sim_lots
    from market_desk.sim_desk import (
        backfill_open_from_db,
        ensure_sim_book_epoch,
        recent_trades,
        replay_exits_from_sell_signals,
        start_gate_snapshot,
    )

    wiped = False
    if reset:
        wipe_sim_book(initial_cash=SIM_INITIAL_CASH, epoch=str(SIM_BOOK_SINCE)[:10])
        wiped = True
    else:
        ensure_sim_book_epoch()
    opened = 0
    replayed = 0
    if backfill:
        opened = backfill_open_from_db()
    if replay:
        replayed = replay_exits_from_sell_signals()
    gate = start_gate_snapshot()
    return {
        "ok": True,
        "reset": wiped,
        "since": SIM_BOOK_SINCE,
        "epoch": get_sim_account_epoch(),
        "backfilled": opened,
        "replayed": replayed,
        "gate": gate,
        "account": gate.get("account"),
        "open_lots": load_open_sim_lots()[:40],
        "recent_lots": load_sim_lots(limit=40),
        "trades": recent_trades(80),
        "curl": (
            "GET /api/ops/sim-desk  "
            "(?reset=true wipe; ?backfill=true since SIM_BOOK_SINCE; ?replay=true sell replay)"
        ),
    }


@router.get("/api/ops/review-check")
async def ops_review_check(
    cf: bool = Query(default=True, description="Include What-If counterfactual (needs klines; slower)"),
    cf_days: int = Query(default=20, ge=5, le=60),
    user: dict = Depends(current_admin_required),
) -> dict:
    """Paste-friendly 10/28-style review checklist (edge / regime / narrative / gates).

    Prefer this over screenshots: curl once and paste the JSON into chat.
    ``cf=false`` skips the kline-backed What-If block for a faster edge-only dump.
    """
    del user
    from market_desk.engine import engine
    from market_desk.review_check import build_review_check_sync, merge_counterfactual

    base = build_review_check_sync()
    if not cf:
        base["counterfactual"] = {"ok": False, "skipped": True}
        base["curl"] = "GET /api/ops/review-check  (?cf=false skips gate What-If)"
        return base
    try:
        audit = await engine.build_counterfactual(days=cf_days, strict=True)
    except Exception as exc:
        base["counterfactual"] = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:160]}
        base["curl"] = "GET /api/ops/review-check"
        return base
    out = merge_counterfactual(base, audit)
    out["curl"] = "GET /api/ops/review-check  (?cf=false skips gate What-If)"
    return out


@router.get("/api/ops/em-avail")
def ops_em_avail(
    days: int = Query(default=7, ge=1, le=30),
    user: dict = Depends(current_admin_required),
) -> dict:
    """East Money availability timeline: hourly counters, hour-of-day folding, transitions."""
    del user
    from market_desk.db import load_em_avail, summarize_em_avail
    from market_desk.eastmoney import avail_state

    engine._flush_em_avail(force=True)
    data = load_em_avail(days)
    return {
        "ok": True,
        "days": days,
        "live": avail_state(),
        "by_hour": summarize_em_avail(data["hourly"]),
        "events": data["events"],
        "hourly": data["hourly"],
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
