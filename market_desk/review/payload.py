"""Live marks, trends, per-code history, and the review tab payload."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from market_desk.db import (
    apply_signal_user_meta,
    filter_signals_for_viewer,
    list_signal_trade_dates,
    load_review_digests,
    load_signals,
    load_signals_for_code,
    load_signals_for_date,
    purge_sentinel_signal_dates,
    save_review_digest,
)
from market_desk.filters import normalize_code
from market_desk.zt_stats import enrich_signals_with_zt_ytd
from market_desk.numbers import num

from market_desk.review.signals import (
    _dragon_hide_from_review,
    enrich_signals_with_boards,
    enrich_signals_with_holders,
    is_buy_signal,
    is_sell_signal,
    live_price_slope,
    purge_unactionable_dragon_signals,
    resolve_day_mainline,
)
from market_desk.review.outcome import (
    _flatten_signal_prices,
    BUY_HIT_LABELS,
    summarize_signals,
)
from market_desk.review.exec_score import (
    build_exec_score,
    build_sell_exec_score,
    merge_exec_score_rows,
    merge_sell_exec_score_rows,
    MISS_KIND_LABELS,
)
from market_desk.review.hit_rates import (
    attach_low_n_flags,
    build_desk_source_hit_rates,
    build_gate_kill_stats,
    build_kind_hit_rates,
    build_miss_attribution,
    build_missed_buys,
    build_phase_hit_rates,
    build_phase_kind_hit_rates,
    build_theme_hit_rates,
    build_tune_hints,
)
from market_desk.review.digest import build_today_digest, build_week_exec_board
from market_desk.review.bias import build_sell_fly_board, build_sell_review_bias_bundle
from market_desk.review.alerts import (
    _above_plan_pct,
    build_chase_cost,
    build_ready_monitor,
    build_review_session_hint,
)


def enrich_signals_with_live_marks(
    rows: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flag signals whose live price hit stop or chase levels."""
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        code = normalize_code(item.get("code"))
        q = quotes.get(code) or {}
        last = num(q.get("price"))
        day_low = num(q.get("low"))
        stop = num(payload.get("stop_price"))
        chase = num(payload.get("chase_price"))
        wait = num(payload.get("wait_price"))
        sig_px = num(item.get("price"))
        item["chase_price"] = chase
        item["wait_price"] = wait
        item["stop_price"] = stop
        flags: list[str] = []
        labels: list[str] = []
        if last is not None and stop is not None and last <= stop:
            flags.append("stop_hit")
            labels.append("触及止损")
        if last is not None and chase is not None and last >= chase:
            flags.append("chase_hit")
            labels.append("触及不追")
        if (
            last is not None
            and wait is not None
            and stop is not None
            and stop < last < wait
            and "stop_hit" not in flags
            and "chase_hit" not in flags
        ):
            flags.append("near_wait")
            labels.append("回踩区间")
        # Ideal entry never touched today, yet price already ran higher.
        miss_pullback = (
            is_buy_signal(item.get("signal_type"))
            and last is not None
            and sig_px is not None
            and day_low is not None
            and day_low > sig_px
            and last > sig_px
            and "stop_hit" not in flags
        )
        if miss_pullback:
            flags.append("miss_pullback")
            labels.append("未回踩·已上行")
        elif (
            last is not None
            and wait is not None
            and chase is not None
            and wait <= last < chase
            and "chase_hit" not in flags
            and "stop_hit" not in flags
        ):
            flags.append("in_band")
            labels.append("建议价附近")
        above_pct = _above_plan_pct(item.get("signal_type"), last, sig_px, flags)
        if above_pct is not None:
            flags.append("above_plan")
            # In-band yet above plan reads contradictory; the above-plan label wins.
            if "建议价附近" in labels:
                labels.remove("建议价附近")
            labels.append(f"高于计划价 +{above_pct:.1f}%")
        item["above_plan_pct"] = above_pct
        item["live_last"] = last
        item["live_pct"] = num(q.get("pct"))
        # Keep a live last for booking defaults; do not overwrite plan suggest (price).
        if last is not None:
            item["last"] = last
        item.update(live_price_slope(code, last))
        if item.get("plan_qty") is None:
            pq = num(payload.get("qty"))
            if pq is not None and float(pq) > 0:
                item["plan_qty"] = int(pq)
        if last is not None and sig_px is not None and sig_px > 0:
            item["dev_pct"] = round((float(last) / float(sig_px) - 1.0) * 100.0, 2)
        else:
            item["dev_pct"] = None
        if last is not None and chase is not None and chase > 0:
            item["chase_dev_pct"] = round((float(last) / float(chase) - 1.0) * 100.0, 2)
        else:
            item["chase_dev_pct"] = None
        item["price_flags"] = flags
        item["price_mark"] = " / ".join(labels) if labels else ""
        # Buying caution for same-day signals: short line for the table, full text as tip.
        caution, tip = "", ""
        if is_buy_signal(item.get("signal_type")):
            if "stop_hit" in flags:
                caution, tip = "到止损带，今日不买", "现价已到止损带，当日不宜再按原计划买"
            elif "chase_hit" in flags:
                caution, tip = "过不追价，放弃", "现价已过不追价，当日不宜追高"
            elif "above_plan" in flags:
                caution = "挂计划价，不追"
                tip = f"现价高于计划价 +{above_pct:.1f}%：只按计划价挂单，不追；过不追价就放弃"
            elif "miss_pullback" in flags:
                caution, tip = "已上行，别死等", "未回踩建议价已上行，勿死等；可对照不追价决定是否放弃"
            elif "near_wait" in flags:
                caution, tip = "回踩带，看站稳", "现价在回踩带，可观察是否站稳"
        item["buy_caution"] = caution
        item["buy_caution_tip"] = tip
        out.append(item)
    return out


def enrich_signals_with_trends(
    rows: list[dict[str, Any]] | None,
    trends: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Attach compact daily-trend fields for review chips (non-destructive)."""
    out: list[dict[str, Any]] = []
    src = trends or {}
    for raw in rows or []:
        item = dict(raw)
        code = normalize_code(item.get("code"))
        tr = src.get(code) if code else None
        if isinstance(tr, dict) and tr:
            item["daily_trend"] = str(tr.get("label") or tr.get("trend") or "")
            item["trend_ok"] = bool(tr.get("up") or tr.get("trend_ok"))
            item["trend_down"] = bool(tr.get("down") or tr.get("trend_down"))
            item["trend_pending"] = bool(tr.get("quality") in ("fetch_fail", "thin") or tr.get("trend_pending"))
            if tr.get("ma5") is not None:
                item["ma5"] = tr.get("ma5")
            if tr.get("ma20") is not None:
                item["ma20"] = tr.get("ma20")
        out.append(item)
    return out


def review_trends_fingerprint(
    trade_date: str,
    rows: list[dict[str, Any]] | None,
    *,
    calendar_day: str,
) -> str:
    """Build a cache key for review trend chips (calendar day + signal set)."""
    import hashlib

    day = str(trade_date or "").strip()[:10]
    cal = str(calendar_day or "").strip()[:10]
    ids = sorted(
        int(r["id"])
        for r in (rows or [])
        if r.get("id") is not None
    )
    digest = hashlib.sha1(",".join(str(i) for i in ids).encode("utf-8")).hexdigest()[:12]
    return f"{cal}|{day}|n{len(ids)}|{digest}"


def build_code_signal_history(
    code: str,
    *,
    limit: int = 120,
    holders: dict[str, Any] | None = None,
    trend: dict[str, Any] | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Assemble per-ticker signal history for the review history drawer.

    Holder / daily-trend fields are current snapshots (not frozen at signal
    time). Plan prices and outcomes come from each stored signal row.
    """
    c = normalize_code(code)
    raw = load_signals_for_code(c, limit=limit) if c else []
    if user_id is not None and raw:
        try:
            raw = apply_signal_user_meta(raw, int(user_id))
        except Exception:
            pass
    rows: list[dict[str, Any]] = []
    for row in raw:
        item = _flatten_signal_prices(row)
        if _dragon_hide_from_review(item):
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        boards = payload.get("board_names") or []
        if isinstance(boards, list) and boards:
            item["boards"] = [str(b) for b in boards if str(b).strip()]
            item["board_text"] = "/".join(item["boards"])
        else:
            item["boards"] = []
            item["board_text"] = ""
        # Compact row for the drawer (keep fields needed for signal description).
        rows.append(
            {
                "id": item.get("id"),
                "trade_date": item.get("trade_date"),
                "signaled_at": item.get("signaled_at"),
                "signal_type": item.get("signal_type"),
                "action": item.get("action"),
                "phase": item.get("phase"),
                "mainline": item.get("mainline"),
                "code": item.get("code"),
                "name": item.get("name"),
                "kind": item.get("kind"),
                "price": item.get("price"),
                "chase_price": item.get("chase_price"),
                "wait_price": item.get("wait_price"),
                "stop_price": item.get("stop_price"),
                "ready": item.get("ready"),
                "traded": int(item.get("traded") or 0),
                "skipped": int(item.get("skipped") or 0),
                "fill_price": item.get("fill_price"),
                "fill_qty": item.get("fill_qty"),
                "outcome_label": item.get("outcome_label"),
                "outcome_day1_pct": item.get("outcome_day1_pct"),
                "outcome_day3_pct": item.get("outcome_day3_pct"),
                "board_text": item.get("board_text") or "",
                "desk_source": item.get("desk_source"),
                "role_label": item.get("role_label") or payload.get("role_label"),
                "reason": payload.get("reason"),
                "pct": payload.get("pct"),
                "confirm_fail": list(payload.get("confirm_fail") or []),
                "confirm_soft": list(payload.get("confirm_soft") or []),
                "minute": payload.get("minute") if isinstance(payload.get("minute"), dict) else None,
                "vs_mainline": payload.get("vs_mainline"),
                "vs_mainline_of": payload.get("vs_mainline_of") or item.get("mainline"),
                "board_match": payload.get("board_match"),
                "payload": {
                    "reason": payload.get("reason"),
                    "confirm_fail": list(payload.get("confirm_fail") or []),
                    "confirm_soft": list(payload.get("confirm_soft") or []),
                    "minute": payload.get("minute") if isinstance(payload.get("minute"), dict) else None,
                    "role_label": payload.get("role_label"),
                    "desk_source": payload.get("desk_source"),
                },
            }
        )

    buys = [r for r in rows if is_buy_signal(r.get("signal_type"))]
    sells = [r for r in rows if is_sell_signal(r.get("signal_type"))]
    scored_buys = [r for r in buys if r.get("outcome_label")]
    hit_n = sum(1 for r in scored_buys if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
    traded_buys = [r for r in buys if int(r.get("traded") or 0)]
    dates = [str(r.get("trade_date") or "")[:10] for r in rows if r.get("trade_date")]
    name = ""
    kind = ""
    for r in rows:
        if r.get("name"):
            name = str(r.get("name") or "")
            kind = str(r.get("kind") or "")
            break

    holder_out: dict[str, Any] | None = None
    if holders and isinstance(holders, dict):
        hit = holders.get(c) or holders.get(str(code or ""))
        if isinstance(hit, dict) and hit:
            holder_out = {
                "holder_num": hit.get("holder_num"),
                "holder_prev": hit.get("holder_prev"),
                "holder_chg": hit.get("holder_chg"),
                "holder_chg_pct": hit.get("holder_chg_pct"),
                "holder_avg_wan": hit.get("holder_avg_wan"),
                "holder_end": hit.get("holder_end"),
                "holder_notice": hit.get("holder_notice"),
            }

    trend_out: dict[str, Any] | None = None
    if trend and isinstance(trend, dict) and trend:
        trend_out = {
            "label": trend.get("label") or trend.get("trend") or "",
            "up": bool(trend.get("up") or trend.get("trend_ok")),
            "down": bool(trend.get("down") or trend.get("trend_down")),
            "quality": trend.get("quality"),
            "ma5": trend.get("ma5"),
            "ma20": trend.get("ma20"),
        }

    return {
        "ok": True,
        "code": c,
        "name": name,
        "kind": kind,
        "summary": {
            "n": len(rows),
            "buy_n": len(buys),
            "sell_n": len(sells),
            "traded_buy_n": len(traded_buys),
            "scored_buy_n": len(scored_buys),
            "buy_hit_n": hit_n,
            "buy_hit_rate": (
                round(100.0 * hit_n / float(len(scored_buys)), 1) if scored_buys else None
            ),
            "first_date": min(dates) if dates else None,
            "last_date": max(dates) if dates else None,
        },
        "holder": holder_out,
        "trend": trend_out,
        "signals": rows,
    }


def build_review_payload(
    limit: int = 180,
    quotes: dict[str, dict[str, Any]] | None = None,
    *,
    trade_date: str | None = None,
    phase: str | None = None,
    boards: list[dict[str, Any]] | None = None,
    live_mainline: str | None = None,
    vs_mainline_mode: str | None = None,
    holders: dict[str, dict[str, Any]] | None = None,
    zt_ytd: dict[str, Any] | None = None,
    user_id: int | None = None,
    trends: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Load one trade-date's signals plus global summary for the review tab.

    ``user_id`` overlays per-user traded / fill annotations and filters sells to
    that account. Shared digest history is saved without personal fills so users
    do not pollute each other.
    """
    calendar_today = datetime.now().strftime("%Y-%m-%d")
    day = str(trade_date or calendar_today).strip()[:10] or calendar_today
    live_ml = str(live_mainline or "").strip() or None
    day_ml = resolve_day_mainline(day)
    mode_raw = str(vs_mainline_mode or "").strip().lower()
    if mode_raw in ("day", "eod", "session", "当日", "当日主线"):
        mode = "day"
    elif mode_raw in ("live", "now", "实时", "实时主线"):
        mode = "live"
    else:
        # Historical days default to that day's mainline; today defaults to live.
        mode = "live" if day == calendar_today else "day"
    compare_ml = day_ml if mode == "day" else live_ml
    if mode == "day" and not compare_ml:
        compare_ml = live_ml
    if mode == "live" and not compare_ml:
        compare_ml = day_ml

    # Drop sealed / limit-up dragons and sentinel test days from DB.
    try:
        purge_sentinel_signal_dates()
        purge_unactionable_dragon_signals(day)
    except Exception:
        pass
    global_rows = [
        r
        for r in (_flatten_signal_prices(r) for r in load_signals(limit=limit))
        if not _dragon_hide_from_review(r)
    ]
    global_rows = filter_signals_for_viewer(global_rows, user_id)
    day_rows = [
        r
        for r in (_flatten_signal_prices(r) for r in load_signals_for_date(day))
        if not _dragon_hide_from_review(r)
    ]
    day_rows = filter_signals_for_viewer(day_rows, user_id)
    if quotes:
        day_rows = enrich_signals_with_live_marks(day_rows, quotes)
    day_rows = enrich_signals_with_boards(
        day_rows, boards, live_mainline=compare_ml
    )
    if trends:
        day_rows = enrich_signals_with_trends(day_rows, trends)
    if holders:
        day_rows = enrich_signals_with_holders(day_rows, holders)
    if zt_ytd:
        day_rows = enrich_signals_with_zt_ytd(day_rows, zt_ytd)
    try:
        from market_desk.ma_fan import enrich_signals_with_ma_fan

        day_rows = enrich_signals_with_ma_fan(day_rows, day)
    except Exception:
        pass
    day_phase = phase
    if not day_phase and day_rows:
        day_phase = str(day_rows[0].get("phase") or "") or None
    # Persist paper digest (no personal traded/fills) for shared history charts.
    paper_day = apply_signal_user_meta(day_rows, None)
    paper_digest = build_today_digest(paper_day, trade_date=day, phase=day_phase)
    if day == calendar_today or day_rows:
        try:
            save_review_digest(day, paper_digest)
        except Exception:
            pass
    # Overlay this user's traded / fill / note flags for the live payload.
    day_rows = apply_signal_user_meta(day_rows, user_id)
    global_rows = apply_signal_user_meta(global_rows, user_id)
    digest = build_today_digest(day_rows, trade_date=day, phase=day_phase)
    summary = summarize_signals(global_rows)
    dates = list_signal_trade_dates(limit=40)
    if calendar_today not in dates:
        dates = [calendar_today] + dates
    elif dates and dates[0] != calendar_today:
        dates = [calendar_today] + [d for d in dates if d != calendar_today]
    if day not in dates:
        dates = [day] + [d for d in dates if d != day]
    summary["today"] = digest
    summary["view_date"] = day
    summary["calendar_today"] = calendar_today
    summary["dates"] = dates
    summary["vs_mainline_mode"] = mode
    summary["vs_mainline_live"] = live_ml
    summary["vs_mainline_day"] = day_ml
    summary["vs_mainline_of"] = compare_ml
    summary["history"] = load_review_digests(limit=20)
    summary["exec"] = digest.get("exec") or build_exec_score(day_rows)
    summary["sell_exec"] = build_sell_exec_score(day_rows)
    # Merge per-user exec diary buys so unsignaled book fills still score.
    if user_id is not None:
        try:
            from market_desk.db import load_exec_diary

            diary = load_exec_diary(user_id=int(user_id), trade_date=day, limit=80)
            merged = merge_exec_score_rows(day_rows, diary)
            summary["exec"] = build_exec_score(merged)
            sell_merged = merge_sell_exec_score_rows(day_rows, diary)
            summary["sell_exec"] = build_sell_exec_score(sell_merged)
        except Exception:
            pass
    summary["phase_hits"] = attach_low_n_flags(build_phase_hit_rates(global_rows))
    summary["kind_hits"] = attach_low_n_flags(build_kind_hit_rates(global_rows))
    summary["phase_kind_hits"] = attach_low_n_flags(build_phase_kind_hit_rates(global_rows))
    summary["desk_hits"] = attach_low_n_flags(build_desk_source_hit_rates(global_rows))
    summary["theme_hits"] = attach_low_n_flags(build_theme_hit_rates(global_rows))
    summary["missed_buys"] = build_missed_buys(day_rows, trade_date=day)
    try:
        summary["miss_attr"] = build_miss_attribution(day_rows, trade_date=day)
    except Exception:
        summary["miss_attr"] = {
            "counts": {"never_touched": 0, "touched_not_bought": 0, "gate_blocked": 0},
            "total": 0,
            "labels": dict(MISS_KIND_LABELS),
            "items": [],
        }
    try:
        week_dates = [d for d in dates if d and d <= calendar_today][:5]
        summary["week_exec"] = build_week_exec_board(
            global_rows,
            dates=week_dates,
            days=5,
        )
    except Exception:
        summary["week_exec"] = {"ok": False, "note": "近周归因暂不可用"}
    # Outcome compare filled later in engine when klines are available.
    summary["outcome_compare"] = {
        "ok": False,
        "rows": [],
        "note": "三标准对照需日线，加载中或暂无样本",
    }
    summary["gate_kills"] = build_gate_kill_stats(global_rows)
    try:
        wide_rows = load_signals(limit=2500)
    except Exception:
        wide_rows = []
    try:
        summary["ready_monitor"] = build_ready_monitor(
            [r for r in wide_rows if not _dragon_hide_from_review(r)]
        )
    except Exception:
        summary["ready_monitor"] = {"ok": False, "tone": "low", "note": "可买入监控暂不可用"}
    try:
        from market_desk.gate_ledger import build_gate_ledger

        summary["gate_ledger"] = build_gate_ledger(
            [r for r in wide_rows if not _dragon_hide_from_review(r)]
        )
    except Exception:
        summary["gate_ledger"] = {"ok": False, "rows": [], "note": "闸门账本暂不可用"}
    try:
        summary["chase_cost"] = build_chase_cost(apply_signal_user_meta(wide_rows, user_id))
    except Exception:
        summary["chase_cost"] = {"ok": False, "n": 0, "items": [], "note": "追价成本暂不可用"}
    summary["session_hint"] = build_review_session_hint(day_rows, is_today=day == calendar_today)
    summary["sell_bias"] = build_sell_review_bias_bundle(global_rows)
    try:
        summary["sell_fly_board"] = build_sell_fly_board(global_rows)
    except Exception:
        summary["sell_fly_board"] = {"ok": False, "n": 0, "note": "卖飞看板暂不可用"}
    try:
        from market_desk.adapt import make_trade_context

        live_ctx = make_trade_context(
            segment_key=None,
            signaled_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            metrics=None,
        )
    except Exception:
        live_ctx = None
    summary["tune_hints"] = build_tune_hints(
        missed=summary["missed_buys"],
        phase_hits=summary["phase_hits"],
        kind_hits=summary["kind_hits"],
        gate_kills=summary["gate_kills"],
        sell_bias=summary["sell_bias"].get("all") or summary["sell_bias"],
        current_context=live_ctx,
        theme_hits=summary["theme_hits"],
        phase_kind_hits=summary["phase_kind_hits"],
    )
    try:
        from market_desk.whitebox import fit_whitebox

        summary["whitebox"] = fit_whitebox(global_rows, use_cache=False)
    except Exception:
        summary["whitebox"] = {"ok": False, "note": "白盒不可用"}
    return {
        "ok": True,
        "signals": day_rows,
        "summary": summary,
        "view_date": day,
        "calendar_today": calendar_today,
        "dates": dates,
    }
