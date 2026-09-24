"""Open-auction sell buffer: must-sell vs watch-until 09:45.

Gives soft sells a reaction window after 09:30 while hard stops stay immediate.
When minute trends are available, judges break-open / below-VWAP / panic volume
inside the watch window; otherwise falls back to quote open/last/stop.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.config import SELL_OPEN_WATCH_MINUTES
from market_desk.numbers import num


PRE_MATCH_END_MINUTES = 9 * 60 + 25


def hold_sells_before_match(
    advice: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Before the 09:25 auction match, demote ready sells to a preview.

    Nothing fills before the match, so sells are shown as「9:25 后定」and never
    recorded / pushed as executable signals until the match price exists.
    """
    adv = dict(advice or {})
    clock = now or datetime.now()
    if clock.hour * 60 + clock.minute >= PRE_MATCH_END_MINUTES:
        return adv
    for key in ("all_items", "items"):
        rows = adv.get(key)
        if not isinstance(rows, list):
            continue
        out: list[dict[str, Any]] = []
        for raw in rows:
            item = dict(raw or {})
            if item.get("ready"):
                item["ready"] = False
                item["pre_match_hold"] = True
                item["next_action"] = "wait_match"
                item["next_action_zh"] = "9:25 后定"
                item["next_action_note"] = "集合竞价撮合前不出卖点"
                item["role_label"] = _prefix_role(str(item.get("role_label") or ""), "预告·9:25 后定")
            out.append(item)
        adv[key] = out
    return adv


def classify_sell_open_track(item: dict[str, Any] | None) -> str | None:
    """Return ``must``, ``watch``, or None when no open-buffer applies.

    - must: stop urgency, or ready clear exits (hard risk / deep structure).
    - watch: ready soft half/trim/take that is not a hard stop.
    Honors an already-stamped ``open_buffer_track`` so minute re-apply works
    after the first pass has deferred (ready=False).
    """
    it = item or {}
    existing = str(it.get("open_buffer_track") or "").strip().lower()
    if existing in ("must", "watch"):
        return existing
    if it.get("open_buffer_pending"):
        return "watch"
    if not it.get("ready"):
        return None
    if it.get("t1_locked"):
        return None
    urg = str(it.get("urgency") or "").strip().lower()
    mode = str(it.get("exit_mode") or "").strip().lower()
    if urg == "stop":
        return "must"
    if mode == "clear":
        return "must"
    if urg in ("take", "trim") and mode == "half":
        return "watch"
    if urg in ("take", "trim"):
        return "watch"
    return None


def sell_open_clock(
    now: datetime,
    *,
    watch_minutes: int | None = None,
) -> dict[str, Any]:
    """Classify clock vs sell open buffer window."""
    minutes = int(now.hour) * 60 + int(now.minute)
    watch_m = max(0, int(watch_minutes if watch_minutes is not None else SELL_OPEN_WATCH_MINUTES))
    open_m = 9 * 60 + 30
    end_m = open_m + watch_m
    auction = 9 * 60 + 15 <= minutes < open_m
    in_watch = open_m <= minutes < end_m
    after_watch = minutes >= end_m and minutes < 15 * 60
    weekday = now.weekday() < 5
    return {
        "weekday": weekday,
        "minutes": minutes,
        "watch_minutes": watch_m,
        "auction": bool(weekday and auction),
        "in_watch": bool(weekday and in_watch),
        "after_watch": bool(weekday and after_watch),
        "session_open": bool(weekday and open_m <= minutes < 15 * 60),
        "deadline_hhmm": f"{end_m // 60:02d}:{end_m % 60:02d}",
    }


def _hhmm_to_minutes(raw: str | None) -> int | None:
    """Parse ``HH:MM`` or ``YYYY-MM-DD HH:MM`` into minutes from midnight."""
    s = str(raw or "").strip()
    if not s:
        return None
    token = s[-5:] if len(s) >= 5 and s[-3] == ":" else s
    if len(token) >= 5 and token[2] == ":":
        try:
            return int(token[0:2]) * 60 + int(token[3:5])
        except ValueError:
            return None
    return None


def slice_minutes_to_deadline(
    minutes: list[dict[str, Any]] | None,
    *,
    deadline_hhmm: str,
    open_hhmm: str = "09:30",
) -> list[dict[str, Any]]:
    """Keep minute bars from continuous open through the buffer deadline."""
    open_m = _hhmm_to_minutes(open_hhmm)
    end_m = _hhmm_to_minutes(deadline_hhmm)
    if open_m is None or end_m is None:
        return list(minutes or [])
    out: list[dict[str, Any]] = []
    for row in minutes or []:
        t = _hhmm_to_minutes(str(row.get("time") or ""))
        if t is None:
            continue
        if open_m <= t <= end_m:
            out.append(row)
    return out


def summarize_open_buffer_minutes(
    minutes: list[dict[str, Any]] | None,
    *,
    open_px: float | None,
    deadline_hhmm: str = "09:45",
) -> dict[str, Any]:
    """Extract break-open / VWAP / volume features inside the watch window.

    Uses East Money minute ``avg`` as VWAP proxy when present; otherwise a
    volume-weighted price of closes in the window.
    """
    window = slice_minutes_to_deadline(minutes, deadline_hhmm=deadline_hhmm)
    out: dict[str, Any] = {
        "sample_n": len(window),
        "broke_open": False,
        "below_vwap": False,
        "vol_panic": False,
        "low": None,
        "decision_price": None,
        "vwap": None,
        "from_open_low_pct": None,
        "note": "",
    }
    if len(window) < 3:
        out["note"] = "分时样本不足，回退现价判定"
        return out

    prices: list[float] = []
    volumes: list[float] = []
    avgs: list[float] = []
    for row in window:
        px = num(row.get("price"))
        if px is None or px <= 0:
            continue
        prices.append(float(px))
        vol = num(row.get("volume"))
        volumes.append(float(vol) if vol is not None and vol >= 0 else 0.0)
        avg = num(row.get("avg"))
        if avg is not None and avg > 0:
            avgs.append(float(avg))

    if len(prices) < 3:
        out["note"] = "分时样本不足，回退现价判定"
        return out

    low = min(prices)
    last = prices[-1]
    out["low"] = round(low, 4)
    out["decision_price"] = round(last, 4)
    out["sample_n"] = len(prices)

    open_ref = float(open_px) if open_px is not None and open_px > 0 else prices[0]
    from_open_low = (low / open_ref - 1.0) * 100.0
    out["from_open_low_pct"] = round(from_open_low, 2)
    # Broke open: window low clearly under open.
    if low <= open_ref * 0.9985:
        out["broke_open"] = True

    vwap = None
    if avgs:
        vwap = avgs[-1]
    else:
        notional = 0.0
        vol_sum = 0.0
        for px, vol in zip(prices, volumes):
            if vol > 0:
                notional += px * vol
                vol_sum += vol
        if vol_sum > 0:
            vwap = notional / vol_sum
    if vwap is not None and vwap > 0:
        out["vwap"] = round(float(vwap), 4)
        if last < float(vwap) * 0.999:
            out["below_vwap"] = True

    # Panic volume: second half of window avg vol >> first half.
    if len(volumes) >= 6 and sum(volumes) > 0:
        mid = len(volumes) // 2
        first = volumes[:mid]
        second = volumes[mid:]
        avg1 = sum(first) / max(1, len(first))
        avg2 = sum(second) / max(1, len(second))
        if avg1 > 0 and avg2 >= avg1 * 1.8:
            out["vol_panic"] = True

    bits = []
    if out["broke_open"]:
        bits.append(f"破开盘低点 {from_open_low:.2f}%")
    if out["below_vwap"]:
        bits.append("站不回分时均价")
    if out["vol_panic"]:
        bits.append("放量走弱")
    out["note"] = "、".join(bits) if bits else "分时未破开盘且未明显弱于均价"
    return out


def evaluate_watch_still_weak(
    item: dict[str, Any] | None,
    minutes: list[dict[str, Any]] | None = None,
    *,
    deadline_hhmm: str = "09:45",
) -> dict[str, Any]:
    """Judge whether a deferred soft sell should still fire after the buffer.

    Prefer minute features when sample is enough; else open/last/stop quotes.
    Still weak if broke open, below VWAP, panic volume, tagged stop, or session red.
    Recovered if last reclaimed open, above VWAP, and stays above stop.
    """
    it = item or {}
    last = num(it.get("last"))
    open_px = num(it.get("open") if it.get("open") is not None else it.get("open_price"))
    stop = num(it.get("stop_price"))
    day_pct = num(it.get("pct"))
    out: dict[str, Any] = {
        "still_weak": False,
        "recovered": False,
        "from_open_pct": None,
        "minute": None,
        "decision_price": None,
        "note": "",
        "source": "quote",
    }
    feat = summarize_open_buffer_minutes(
        minutes, open_px=open_px, deadline_hhmm=deadline_hhmm
    )
    out["minute"] = feat
    if feat.get("sample_n", 0) >= 3 and feat.get("note") != "分时样本不足，回退现价判定":
        out["source"] = "minute"
        out["decision_price"] = feat.get("decision_price")
        if feat.get("decision_price") is not None:
            last = float(feat["decision_price"])
        weak_bits = []
        if feat.get("broke_open"):
            weak_bits.append(str(feat.get("note") or "破开盘").split("、")[0])
        if feat.get("below_vwap"):
            weak_bits.append("站不回分时均价")
        if feat.get("vol_panic"):
            weak_bits.append("放量走弱")
        hit_stop = stop is not None and stop > 0 and float(last) <= float(stop) * 1.001
        if hit_stop:
            weak_bits.append("贴近/触及止损")
        if open_px is not None and open_px > 0:
            out["from_open_pct"] = round((float(last) / float(open_px) - 1.0) * 100.0, 2)
        if weak_bits:
            out["still_weak"] = True
            out["note"] = "缓冲后仍弱（分时）：" + "、".join(weak_bits)
            return out
        # Recovered: reclaim open and not below VWAP, above stop.
        above_open = open_px is None or open_px <= 0 or float(last) >= float(open_px) * 0.999
        above_stop = stop is None or float(last) > float(stop)
        not_below_vwap = not bool(feat.get("below_vwap"))
        if above_open and above_stop and not_below_vwap:
            out["recovered"] = True
            out["note"] = "缓冲后分时收回开盘且未弱于均价，软卖降为观察"
            return out
        out["still_weak"] = True
        out["note"] = "缓冲结束：分时未明显修复，维持原软卖"
        return out

    # Quote fallback (v1).
    if last is None or last <= 0:
        out["note"] = "无现价，缓冲结束后仍按原软卖提示"
        out["still_weak"] = True
        return out
    from_open = None
    if open_px is not None and open_px > 0:
        from_open = (float(last) / float(open_px) - 1.0) * 100.0
        out["from_open_pct"] = round(from_open, 2)
    hit_stop = stop is not None and stop > 0 and float(last) <= float(stop) * 1.001
    lost_open = from_open is not None and from_open <= -0.15
    session_red = day_pct is not None and float(day_pct) <= -0.8
    if hit_stop or lost_open or session_red:
        out["still_weak"] = True
        bits = []
        if hit_stop:
            bits.append("贴近/触及止损")
        if lost_open:
            bits.append(f"相对开盘 {from_open:.2f}%")
        if session_red:
            bits.append(f"日内 {day_pct:.1f}%")
        out["note"] = "缓冲后仍弱：" + "、".join(bits)
        return out
    if open_px is not None and open_px > 0 and float(last) >= float(open_px) * 0.999:
        if stop is None or float(last) > float(stop):
            out["recovered"] = True
            out["note"] = "缓冲后收回开盘且未破止损，软卖降为观察"
            return out
    out["still_weak"] = True
    out["note"] = "缓冲结束：信号未明显修复，维持原软卖"
    return out


def apply_sell_open_buffer(
    advice: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    watch_minutes: int | None = None,
    minutes_by_code: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Annotate / defer soft sells during the open watch window.

    Must-track stays ready immediately. Watch-track is deferred until deadline,
    then re-armed or demoted using minute features when available.
    """
    from market_desk.verdict import _enrich_sell_next_action, _refresh_sell_advice_summary

    out = dict(advice or {})
    clock = sell_open_clock(now or datetime.now(), watch_minutes=watch_minutes)
    minutes_by_code = minutes_by_code or {}
    out["open_buffer"] = {
        "watch_minutes": clock["watch_minutes"],
        "deadline_hhmm": clock["deadline_hhmm"],
        "auction": clock["auction"],
        "in_watch": clock["in_watch"],
        "after_watch": clock["after_watch"],
        "minute_mode": bool(minutes_by_code),
    }
    if not clock["weekday"] or not (
        clock["auction"] or clock["in_watch"] or clock["after_watch"]
    ):
        tagged: list[dict[str, Any]] = []
        for raw in out.get("all_items") or out.get("items") or []:
            item = dict(raw)
            track = classify_sell_open_track(item)
            if track:
                item["open_buffer_track"] = track
            tagged.append(item)
        if tagged:
            out["all_items"] = tagged
            out = _refresh_sell_advice_summary(out)
        return out

    deadline = clock["deadline_hhmm"]
    new_all: list[dict[str, Any]] = []
    deferred_n = 0
    must_n = 0
    held_n = 0

    for raw in out.get("all_items") or out.get("items") or []:
        item = dict(raw)
        track = classify_sell_open_track(item)
        if not track:
            new_all.append(item)
            continue
        item["open_buffer_track"] = track
        digits = 3 if item.get("kind") == "etf" else 2
        code = str(item.get("code") or "").zfill(6)
        series = minutes_by_code.get(code) if code else None

        if track == "must":
            # Soft shallow-break buffer: stop/clear that barely lost open can wait
            # inside the watch window (false open break); deep stop stays immediate.
            shallow_ok = False
            if clock["in_watch"]:
                from market_desk.config import SELL_OPEN_SHALLOW_BREAK_PCT

                open_px = num(item.get("open") or item.get("day_open"))
                last_px = num(item.get("last") or item.get("price"))
                stop_px = num(item.get("stop_price") or item.get("stop"))
                thr = float(SELL_OPEN_SHALLOW_BREAK_PCT)
                if open_px and open_px > 0 and last_px is not None:
                    from_open = (float(last_px) - float(open_px)) / float(open_px) * 100.0
                    deep_stop = (
                        stop_px is not None
                        and float(last_px) <= float(stop_px) * 1.001
                    )
                    if (not deep_stop) and (-thr <= from_open < 0):
                        shallow_ok = True
                        item["open_buffer_track"] = "watch"
                        item["open_buffer_shallow"] = True
                        item["open_buffer_pending"] = True
                        item["ready"] = False
                        deferred_n += 1
                        tip = (
                            f"浅破开盘缓冲（相对开盘 {from_open:.2f}%≥-{thr:g}%）"
                            f"·观察至 {deadline}"
                        )
                        item["role_label"] = _prefix_role(
                            str(item.get("role_label") or ""), tip
                        )
                        item["reason"] = _join_reason(str(item.get("reason") or ""), tip)
                        item = _enrich_sell_next_action(
                            item,
                            hold_peak=float(item.get("hold_peak") or 0),
                            last_sell_price=item.get("half_anchor_price"),
                            digits=digits,
                        )
                        new_all.append(item)
                        continue
            if not shallow_ok:
                must_n += 1
                item["open_buffer_phase"] = (
                    "auction_preview" if clock["auction"] else "immediate"
                )
                tip = (
                    "竞价预告·开盘必卖（止损/清仓不等待）"
                    if clock["auction"]
                    else "开盘必卖（止损/清仓，不等待缓冲）"
                )
                item["role_label"] = _prefix_role(str(item.get("role_label") or ""), tip)
                item["reason"] = _join_reason(str(item.get("reason") or ""), tip)
                item = _enrich_sell_next_action(
                    item,
                    hold_peak=float(item.get("hold_peak") or 0),
                    last_sell_price=item.get("half_anchor_price"),
                    digits=digits,
                )
                new_all.append(item)
                continue

        if clock["auction"]:
            tip = f"软卖·开盘后观察至 {deadline} 再定"
            item["open_buffer_phase"] = "auction_preview"
            item["role_label"] = _prefix_role(str(item.get("role_label") or ""), tip)
            item["reason"] = _join_reason(str(item.get("reason") or ""), tip)
            new_all.append(item)
            continue

        if clock["in_watch"]:
            deferred_n += 1
            pending_reason = str(item.get("reason") or "")
            item["open_buffer_phase"] = "watching"
            # Progressive minute hint while waiting (does not re-arm).
            if series:
                preview = summarize_open_buffer_minutes(
                    series,
                    open_px=num(item.get("open") or item.get("open_price")),
                    deadline_hhmm=deadline,
                )
                item["open_buffer_minute"] = preview
            item["open_buffer_pending"] = {
                "ready": True,
                "exit_mode": item.get("exit_mode"),
                "urgency": item.get("urgency"),
                "role_label": item.get("role_label"),
                "sell_price": item.get("sell_price"),
                "sell_pct": item.get("sell_pct"),
                "sell_qty": item.get("sell_qty"),
                "reason": pending_reason,
            }
            item["ready"] = False
            item["exit_mode"] = "hold"
            item["sell_pct"] = 0
            item["sell_qty"] = 0
            item["role_label"] = f"开盘观察至{deadline}"
            item["reason"] = _join_reason(
                pending_reason,
                f"软卖进入开盘缓冲，{deadline} 前先看分时是否继续弱；止损轨不受影响",
            )
            item = _enrich_sell_next_action(
                item,
                hold_peak=float(item.get("hold_peak") or 0),
                last_sell_price=item.get("half_anchor_price"),
                digits=digits,
            )
            item["next_action"] = "watch"
            item["next_action_zh"] = f"观察至{deadline}"
            item["next_action_note"] = "缓冲窗内不催软卖"
            new_all.append(item)
            continue

        # after_watch: finalize with minute features when present
        judge = evaluate_watch_still_weak(
            item, series, deadline_hhmm=deadline
        )
        item["open_buffer_judge"] = judge
        if judge.get("minute"):
            item["open_buffer_minute"] = judge.get("minute")
        if judge.get("decision_price") is not None:
            item["open_buffer_decision_price"] = judge.get("decision_price")
        if judge.get("recovered"):
            held_n += 1
            item["open_buffer_phase"] = "released"
            item["ready"] = False
            item["exit_mode"] = "hold"
            item["urgency"] = "hold"
            item["sell_pct"] = 0
            item["sell_qty"] = 0
            item["role_label"] = "缓冲后修复·继续持有"
            item["reason"] = _join_reason(
                str(item.get("reason") or ""), str(judge.get("note") or "")
            )
        else:
            item["open_buffer_phase"] = "armed"
            tip = str(judge.get("note") or "缓冲结束·维持软卖")
            pending = item.get("open_buffer_pending") if isinstance(item.get("open_buffer_pending"), dict) else {}
            if pending:
                item["ready"] = True
                if pending.get("exit_mode") is not None:
                    item["exit_mode"] = pending.get("exit_mode")
                if pending.get("urgency") is not None:
                    item["urgency"] = pending.get("urgency")
                if pending.get("sell_price") is not None:
                    item["sell_price"] = pending.get("sell_price")
                if pending.get("sell_pct") is not None:
                    item["sell_pct"] = pending.get("sell_pct")
                if pending.get("sell_qty") is not None:
                    item["sell_qty"] = pending.get("sell_qty")
                if pending.get("role_label"):
                    item["role_label"] = str(pending.get("role_label"))
            item["role_label"] = _prefix_role(str(item.get("role_label") or ""), "缓冲后仍卖")
            item["reason"] = _join_reason(str(item.get("reason") or ""), tip)
        item = _enrich_sell_next_action(
            item,
            hold_peak=float(item.get("hold_peak") or 0),
            last_sell_price=item.get("half_anchor_price"),
            digits=digits,
        )
        new_all.append(item)

    out["all_items"] = new_all
    out = _refresh_sell_advice_summary(out)
    ob = dict(out.get("open_buffer") or {})
    ob.update(
        {
            "must_n": must_n,
            "deferred_n": deferred_n,
            "released_n": held_n,
        }
    )
    if clock["in_watch"] and deferred_n and not any(
        x.get("ready") and x.get("open_buffer_track") == "must" for x in new_all
    ):
        if not any(x.get("ready") for x in new_all):
            out["title"] = f"开盘观察至{deadline}"
            out["text"] = f"{deferred_n} 只软卖观察中 · 止损/清仓仍会立即提示"
            out["size_note"] = _join_reason(
                str(out.get("size_note") or ""),
                f"开盘缓冲 {clock['watch_minutes']} 分钟：软减看分时至 {deadline}；必须卖不等待。",
            )
    elif clock["in_watch"] and must_n:
        out["size_note"] = _join_reason(
            str(out.get("size_note") or ""),
            f"开盘必卖 {must_n} · 软卖观察 {deferred_n}（至 {deadline}）",
        )
    out["open_buffer"] = ob
    return out


def _prefix_role(role: str, tip: str) -> str:
    role = str(role or "").strip()
    tip = str(tip or "").strip()
    if not tip:
        return role
    if tip in role:
        return role
    return f"{tip}·{role}" if role else tip


def _join_reason(base: str, tip: str) -> str:
    base = str(base or "").strip()
    tip = str(tip or "").strip()
    if not tip:
        return base
    if tip in base:
        return base
    return f"{base}；{tip}" if base else tip
