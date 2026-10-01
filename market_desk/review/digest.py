"""Today's review digest and the weekly execution board."""

from __future__ import annotations

from typing import Any
from market_desk.db import load_mainline_switches
from market_desk.numbers import num

from market_desk.review.signals import is_buy_signal, is_sell_signal
from market_desk.review.outcome import BUY_HIT_LABELS
from market_desk.review.exec_score import build_exec_score
from market_desk.review.hit_rates import build_miss_attribution


def build_today_digest(
    rows: list[dict[str, Any]],
    *,
    trade_date: str,
    phase: str | None = None,
    switch_count: int | None = None,
) -> dict[str, Any]:
    """Build a same-day review digest for the summary strip."""
    today_rows = [r for r in rows if str(r.get("trade_date") or "") == trade_date]
    buys = [r for r in today_rows if is_buy_signal(r.get("signal_type")) and not int(r.get("skipped") or 0)]
    sells = [r for r in today_rows if is_sell_signal(r.get("signal_type")) and not int(r.get("skipped") or 0)]
    scored = [r for r in buys if r.get("outcome_label")]
    hit = sum(1 for r in scored if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
    miss = sum(
        1
        for r in buys
        if "miss_pullback" in (r.get("price_flags") or [])
        or "未回踩" in str(r.get("price_mark") or "")
    )
    in_band = sum(
        1
        for r in buys
        if "in_band" in (r.get("price_flags") or []) or "near_wait" in (r.get("price_flags") or [])
    )
    switches = switch_count
    if switches is None:
        try:
            switches = len(load_mainline_switches(trade_date, limit=40))
        except Exception:
            switches = 0
    day1_vals = [num(r.get("outcome_day1_pct")) for r in scored]
    day1_vals = [v for v in day1_vals if v is not None]
    exec_score = build_exec_score(today_rows)
    return {
        "date": trade_date,
        "buy_n": len(buys),
        "sell_n": len(sells),
        "miss_pullback_n": miss,
        "in_band_n": in_band,
        "traded_n": sum(1 for r in today_rows if int(r.get("traded") or 0)),
        "skipped_n": sum(1 for r in today_rows if int(r.get("skipped") or 0)),
        "switch_n": int(switches or 0),
        "phase": phase or "",
        "buy_hit_rate": None if not scored else round(100.0 * hit / len(scored), 1),
        "buy_avg_day1": None if not day1_vals else round(sum(day1_vals) / len(day1_vals), 2),
        "scored_n": len(scored),
        "exec": exec_score,
    }


def build_week_exec_board(
    rows: list[dict[str, Any]],
    *,
    dates: list[str],
    days: int = 5,
) -> dict[str, Any]:
    """Aggregate miss attribution + half-window adherence over recent trade dates.

    Display-only polish for weekly review. Does not feed adapt.
    """
    use_dates = [d for d in (dates or []) if d][: max(1, int(days or 5))]
    counts = {"never_touched": 0, "touched_not_bought": 0, "gate_blocked": 0}
    fly_n = 0
    fly_hit_n = 0
    window_n = 0
    followed_n = 0
    by_day: list[dict[str, Any]] = []
    for day in use_dates:
        day_rows = [r for r in rows if str(r.get("trade_date") or "") == day]
        attr = build_miss_attribution(day_rows, trade_date=day)
        for k, n in (attr.get("counts") or {}).items():
            if k in counts:
                counts[k] += int(n or 0)
        # Sell-fly: reuse price_flags / outcome markers when present.
        day_fly = 0
        day_fly_hit = 0
        for r in day_rows:
            if not is_sell_signal(r.get("signal_type")):
                continue
            lab = str(r.get("outcome_label") or "")
            if lab == "卖飞" or "卖飞" in lab:
                day_fly += 1
                fly_n += 1
            if lab == "卖后回落":
                day_fly_hit += 1
                fly_hit_n += 1
        day_win = 0
        day_fol = 0
        for r in day_rows:
            if str(r.get("signal_type") or "") != "buy":
                continue
            payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            half = bool(
                payload.get("ready_relaxed")
                or payload.get("probe_ok")
                or payload.get("fly_warn")
                or (
                    int(r.get("ready") or 0)
                    and (
                        "价带" in str(payload.get("role_label") or "")
                        or "半" in str(payload.get("role_label") or "")
                    )
                )
            )
            if not half and int(r.get("ready") or 0) and payload.get("near_entry"):
                # Legacy rows: ready + near_entry treated as actionable window.
                half = True
            if not half:
                continue
            day_win += 1
            window_n += 1
            if int(r.get("traded") or 0):
                day_fol += 1
                followed_n += 1
        by_day.append(
            {
                "date": day,
                "miss_total": int(attr.get("total") or 0),
                "counts": dict(attr.get("counts") or {}),
                "window_n": day_win,
                "followed_n": day_fol,
                "follow_rate": round(100.0 * day_fol / day_win, 1) if day_win else None,
            }
        )
    miss_total = sum(counts.values())
    return {
        "ok": True,
        "days": use_dates,
        "day_n": len(use_dates),
        "miss_counts": counts,
        "miss_total": miss_total,
        "miss_share": {
            k: (round(100.0 * v / miss_total, 1) if miss_total else 0.0)
            for k, v in counts.items()
        },
        "window_n": window_n,
        "followed_n": followed_n,
        "follow_rate": round(100.0 * followed_n / window_n, 1) if window_n else None,
        "sell_fly_n": fly_n,
        "sell_fly_hit_n": fly_hit_n,
        "by_day": by_day,
        "note": "近几日漏买三类占比 + 半仓/试探窗口是否当天点了已交易；不进 adapt。",
    }
