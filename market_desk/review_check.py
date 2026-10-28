"""Paste-friendly review checklist JSON (companion to ``/api/ops/check``).

Builds a compact ledger of pre-registered 10/28-style items from the same
builders the review UI uses, so an admin can ``curl`` once and paste the
payload into chat instead of screenshotting panels.
"""

from __future__ import annotations

from typing import Any

from market_desk.config import (
    EARLY_HALF_SHADOW_SINCE,
    EDGE_SHADOW_SINCE,
    REGIME_SHADOW_SINCE,
)
from market_desk.calendar import today_cn


TODO_MIN_DAYS = 10
TODO_MIN_N = 30


def _sample(days: int | None, n: int | None, *, min_days: int = TODO_MIN_DAYS, min_n: int = TODO_MIN_N) -> dict[str, Any]:
    """Return sample size vs the pre-registered TODO floor (not the UI judge)."""
    d = int(days or 0)
    k = int(n or 0)
    return {
        "days": d,
        "n": k,
        "min_days": min_days,
        "min_n": min_n,
        "enough_for_todo": d >= min_days and k >= min_n,
    }


def _item(
    *,
    id: str,
    title: str,
    since: str | None,
    sample: dict[str, Any],
    metrics: dict[str, Any] | None = None,
    ui_verdict: str | None = None,
    hint: str,
    level: str = "ok",
    skip: bool = False,
) -> dict[str, Any]:
    """One checklist row for the paste payload."""
    return {
        "id": id,
        "title": title,
        "since": since,
        "sample": sample,
        "metrics": metrics or {},
        "ui_verdict": ui_verdict,
        "hint": hint,
        "level": "skip" if skip else level,
        "skip": skip,
    }


def _pick_row(rows: list[dict[str, Any]] | None, *keys: str) -> dict[str, Any] | None:
    """Return the first row whose ``key`` matches any of ``keys``."""
    for row in rows or []:
        if str(row.get("key") or "") in keys:
            return row
    return None


def _edge_items(edge: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten edge-shadow tables into checklist items."""
    items: list[dict[str, Any]] = []
    since = str(edge.get("since") or EDGE_SHADOW_SINCE)
    reclaim = _pick_row(edge.get("reclaim"), "全部触价卡")
    if reclaim:
        items.append(
            _item(
                id="edge_reclaim",
                title="进场影子·站回再买",
                since=since,
                sample=_sample(reclaim.get("days"), reclaim.get("n")),
                metrics={
                    "touch_d3": reclaim.get("touch_d3"),
                    "reclaim_d3": reclaim.get("reclaim_d3"),
                    "delta": reclaim.get("delta"),
                    "reclaim_n": reclaim.get("reclaim_n"),
                },
                ui_verdict=str(reclaim.get("verdict") or ""),
                hint="并表看站回/熔断/降权后再挑一条软改；UI 结论≠TODO 的 |t|/两半",
                level="ok" if reclaim.get("tone") in ("good", "mid", "bad") else "warn",
            )
        )
    breaker_rows = list(edge.get("breaker") or [])
    if breaker_rows:
        any_n = sum(int(r.get("n") or 0) for r in breaker_rows)
        any_days = max((int(r.get("days") or 0) for r in breaker_rows), default=0)
        items.append(
            _item(
                id="edge_breaker",
                title="进场影子·当日熔断",
                since=since,
                sample=_sample(any_days, any_n),
                metrics={"rows": [{"key": r.get("key"), "n": r.get("n"), "days": r.get("days"), "verdict": r.get("verdict")} for r in breaker_rows]},
                ui_verdict="样本不足" if any_n == 0 else "见 metrics.rows",
                hint="各档被拦卡仍为 0 则继续攒",
                level="warn" if any_n == 0 else "ok",
            )
        )
    reweight = list(edge.get("reweight") or [])
    total_down = _pick_row(reweight, "合计降权")
    up_trend = next((r for r in reweight if "日线上升" in str(r.get("key") or "")), None)
    if total_down or up_trend:
        focus = total_down or up_trend or {}
        items.append(
            _item(
                id="edge_reweight",
                title="进场影子·降权/加权",
                since=since,
                sample=_sample(focus.get("days"), focus.get("n")),
                metrics={
                    "total_down": {
                        "n": (total_down or {}).get("n"),
                        "days": (total_down or {}).get("days"),
                        "excess": (total_down or {}).get("excess"),
                        "verdict": (total_down or {}).get("verdict"),
                    },
                    "daily_up_downweight": {
                        "n": (up_trend or {}).get("n"),
                        "days": (up_trend or {}).get("days"),
                        "excess": (up_trend or {}).get("excess"),
                        "verdict": (up_trend or {}).get("verdict"),
                    },
                    "rows": [
                        {
                            "key": r.get("key"),
                            "n": r.get("n"),
                            "days": r.get("days"),
                            "excess": r.get("excess"),
                            "verdict": r.get("verdict"),
                        }
                        for r in reweight[:12]
                    ],
                },
                ui_verdict=str((total_down or {}).get("verdict") or ""),
                hint="挡对了≠立刻改闸；与日线仓位系数分开判",
                level="ok",
            )
        )
    early_rows = list(edge.get("early_half") or [])
    early = early_rows[0] if early_rows else None
    if early:
        items.append(
            _item(
                id="early_half",
                title="将飞半仓早进",
                since=str(edge.get("early_half_since") or EARLY_HALF_SHADOW_SINCE),
                sample=_sample(early.get("days"), early.get("n")),
                metrics={
                    "early_d3": early.get("early_d3"),
                    "ready_d3": early.get("ready_d3"),
                    "delta": early.get("delta"),
                    "ready_n": early.get("ready_n"),
                    "skip_n": early.get("skip_n"),
                },
                ui_verdict=str(early.get("verdict") or ""),
                hint="满 10 交易日且 |t|/两半过门槛才软提示",
                level="warn" if int(early.get("days") or 0) < TODO_MIN_DAYS else "ok",
            )
        )
    regime_rows = list(edge.get("regime") or [])
    lit = next((r for r in regime_rows if str(r.get("key") or "") == "尺下·亮过灯"), None)
    pos = next((r for r in regime_rows if str(r.get("key") or "") == "尺下·日线降"), None)
    delta_rows = [r for r in regime_rows if r.get("delta")]
    days_candidates = [int(r.get("days") or 0) for r in regime_rows if r.get("days") is not None]
    days_r = max(days_candidates) if days_candidates else 0
    n_r = int((lit or {}).get("n") or 0) + int((pos or {}).get("n") or 0)
    items.append(
        _item(
            id="regime",
            title="大盘尺 regime",
            since=str(edge.get("regime_since") or REGIME_SHADOW_SINCE),
            sample=_sample(days_r, n_r),
            metrics={
                "rows": [
                    {
                        "key": r.get("key"),
                        "n": r.get("n"),
                        "days": r.get("days"),
                        "d3": r.get("d3"),
                        "win": r.get("win"),
                        "note": r.get("note"),
                    }
                    for r in regime_rows
                    if not r.get("delta")
                ][:8],
                "deltas": [{"key": r.get("key"), "d3": r.get("d3"), "note": r.get("note")} for r in delta_rows],
            },
            ui_verdict="样本不足" if days_r < TODO_MIN_DAYS else "见 metrics.deltas",
            hint="自 10/15 起满 10 交易日才判软排序/提示升级；不足则只记账",
            level="warn" if days_r < TODO_MIN_DAYS else "ok",
        )
    )
    return items


def _narrative_item() -> dict[str, Any]:
    """Summarize narrative-shadow lead stats for the checklist."""
    from market_desk.db import load_narrative_shadow
    from market_desk.radar.narrative import summarize_leads

    # Sample window aligned with narrative OOS note in TODO (snap pool era).
    since = "2026-10-12"
    rows = load_narrative_shadow(since)
    snap_rows = [r for r in rows if str(r.get("pool") or "") == "snap"]
    use = snap_rows or rows
    lead = summarize_leads(use)
    days_with = len({str(r.get("trade_date") or "")[:10] for r in use})
    return _item(
        id="narrative",
        title="叙事簇",
        since=since,
        sample=_sample(days_with, int(lead.get("sightings") or 0), min_n=1),
        metrics={
            "pool": "snap" if snap_rows else ("legacy" if rows else "empty"),
            "rows": len(use),
            "sightings": lead.get("sightings"),
            "hits": lead.get("hits"),
            "hit_rate": lead.get("hit_rate"),
            "median_lead_min": lead.get("median_lead_min"),
            "days_with_cluster": days_with,
        },
        ui_verdict=None,
        hint="有簇≥5/10 天且命中≥50%且提前中位≥10 分 → 软提示；否则只攒",
        level="ok" if days_with else "warn",
    )


def _crowd_item() -> dict[str, Any]:
    """Count crowd-shadow hits; excess needs scored joins (left for later)."""
    from market_desk.db import load_crowd_shadow

    since = str(EDGE_SHADOW_SINCE)
    rows = load_crowd_shadow(since)
    days = sorted({str(r.get("trade_date") or "")[:10] for r in rows if r.get("trade_date")})
    return _item(
        id="crowd",
        title="拥挤 crowd_shadow",
        since=since,
        sample=_sample(len(days), len(rows), min_n=1),
        metrics={
            "hits": len(rows),
            "days": len(days),
            "boards": sorted({str(r.get("board") or "") for r in rows if r.get("board")})[:12],
        },
        ui_verdict=None,
        hint="被波及卡三日超额 ≤−1% 且 t≤−2 才提议硬禁；本接口暂只报命中次数",
        level="ok" if rows else "warn",
    )


def build_review_check_sync() -> dict[str, Any]:
    """Build checklist items that need no async kline fetch.

    Returns:
        Dict with ``ok``, ``asof``, ``items`` (edge / narrative / crowd) and notes.
    """
    from market_desk.db import load_signals
    from market_desk.edge_shadow import build_edge_shadow
    from market_desk.review.signals import _dragon_hide_from_review, is_buy_signal

    rows = [
        r
        for r in load_signals(limit=4000)
        if is_buy_signal(r.get("signal_type")) and not _dragon_hide_from_review(r)
    ]
    edge = build_edge_shadow(rows)
    items = _edge_items(edge) if edge.get("ok") else []
    items.append(_narrative_item())
    items.append(_crowd_item())
    items.append(
        _item(
            id="deep_v_bounce",
            title="深 V 反抽确认",
            since=None,
            sample=_sample(0, 0),
            hint="影子未上线；跳过",
            level="skip",
            skip=True,
        )
    )
    items.append(
        _item(
            id="zt_board_share",
            title="涨停占比接班",
            since=None,
            sample=_sample(0, 0),
            hint="影子未上线；跳过",
            level="skip",
            skip=True,
        )
    )
    return {
        "ok": True,
        "asof": today_cn(),
        "edge_note": edge.get("note"),
        "edge_oos_days": edge.get("oos_days"),
        "edge_oos_n": edge.get("oos_n"),
        "items": items,
        "note": (
            "UI verdict 用闸门账本口径（天数/张数/幅度），不等于 TODO 的 |t|≥2+两半同号。"
            "闸门 What-If / ATR / 入场敏感度在 counterfactual 段（需拉日线，可能稍慢）。"
        ),
    }


def merge_counterfactual(base: dict[str, Any], cf: dict[str, Any] | None) -> dict[str, Any]:
    """Append gate / ATR / chase-style rows from a counterfactual audit payload."""
    out = dict(base)
    items = list(out.get("items") or [])
    if not cf or not cf.get("ok"):
        items.append(
            _item(
                id="gates_cf",
                title="阶段二闸门·What-If",
                since=None,
                sample=_sample(0, 0),
                metrics={"error": (cf or {}).get("note") or "counterfactual 不可用"},
                hint="可另 curl /api/review/counterfactual?days=20",
                level="warn",
            )
        )
        out["items"] = items
        out["counterfactual"] = {"ok": False}
        return out

    summary = cf.get("summary") if isinstance(cf.get("summary"), dict) else {}
    gates = list(cf.get("gates") or [])
    heroes = [g for g in gates if g.get("verdict") == "功臣"]
    losses = [g for g in gates if g.get("verdict") == "损耗"]
    short = [g for g in gates if g.get("verdict") == "样本不足"]
    touched = int(summary.get("touched") or 0)
    # Approximate trading days from gate rows
    gate_days = max((int(g.get("days") or 0) for g in gates), default=0)
    items.append(
        _item(
            id="gates_cf",
            title="阶段二闸门·What-If",
            since=None,
            sample=_sample(gate_days, touched),
            metrics={
                "headline": cf.get("headline") or summary.get("headline"),
                "touched": touched,
                "released_n": (summary.get("released") or {}).get("n"),
                "released_mean": (summary.get("released") or {}).get("mean"),
                "blocked_n": (summary.get("blocked") or {}).get("n"),
                "blocked_mean": (summary.get("blocked") or {}).get("mean"),
                "heroes": [{"key": g.get("key"), "days": g.get("days"), "avoid_mean": g.get("avoid_mean"), "ci": [g.get("ci_lo"), g.get("ci_hi")]} for g in heroes[:6]],
                "losses": [{"key": g.get("key"), "days": g.get("days"), "avoid_mean": g.get("avoid_mean"), "ci": [g.get("ci_lo"), g.get("ci_hi")]} for g in losses[:6]],
                "short_n": len(short),
            },
            ui_verdict=("功臣为主·保留" if heroes and not losses else ("有损耗·再看" if losses else "见 metrics")),
            hint="区间整体>0 的功臣保留；整体<0 的损耗才考虑改软；须看 ci",
            level="ok" if heroes or losses else "warn",
        )
    )

    stop = cf.get("stop_cmp") if isinstance(cf.get("stop_cmp"), dict) else {}
    stop_rows = list(stop.get("rows") or [])
    if stop_rows:
        cur = next((r for r in stop_rows if "日低" in str(r.get("label") or "")), stop_rows[0])
        atr = next((r for r in stop_rows if "ATR" in str(r.get("label") or "")), None)
        items.append(
            _item(
                id="stop_atr",
                title="ATR 止损影子",
                since=None,
                sample=_sample(None, (atr or cur).get("n")),
                metrics={"rows": stop_rows, "note": stop.get("note")},
                ui_verdict=(
                    "影子更差·不切"
                    if atr and cur.get("mean") is not None and atr.get("mean") is not None and float(atr["mean"]) < float(cur["mean"])
                    else "见 metrics"
                ),
                hint="影子三日净值反超日低且含上涨日样本才切默认",
                level="ok",
            )
        )

    entry = cf.get("entry_sens")
    if isinstance(entry, dict) and entry:
        items.append(
            _item(
                id="entry_sensitivity",
                title="入场价敏感度",
                since=None,
                sample=_sample(None, entry.get("n") or touched),
                metrics=entry,
                hint="多付越差 → 维持挂计划价，不放宽追价",
                level="ok",
            )
        )

    unk_n = float(summary.get("unknown_n") or 0)
    items.append(
        _item(
            id="cf_unknown",
            title="cf 未明占比",
            since=None,
            sample=_sample(int(summary.get("window_days") or gate_days), touched, min_n=1),
            metrics={
                "unknown_n": unk_n,
                "touched": touched,
                "share": round(unk_n / touched, 3) if touched else None,
            },
            hint="<30% 才用 cf 给闸门下结论",
            level="ok",
        )
    )

    out["items"] = items
    out["counterfactual"] = {
        "ok": True,
        "built_at": cf.get("built_at"),
        "kline_codes": cf.get("kline_codes"),
        "cache_hit": cf.get("cache_hit"),
        "headline": cf.get("headline"),
    }
    return out
