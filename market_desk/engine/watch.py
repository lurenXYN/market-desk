"""Watchlist, segment, and watch-pool decoration for the snapshot."""

from __future__ import annotations

from typing import Any
from market_desk.filters import is_limit_down, normalize_code
from market_desk.numbers import num


def _decorate_watchlist(
    rows: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    *,
    verdict: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach live quote fields onto personal watchlist rows."""
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        code = str(item.get("code") or "").zfill(6)
        q = quotes.get(code) or {}
        item["code"] = code
        item["name"] = item.get("name") or q.get("name") or code
        item["last"] = q.get("price")
        item["last_pct"] = q.get("pct")
        out.append(item)
    return _annotate_watchlist_observe(out, verdict)


def _attach_holders_to_items(
    recommend: dict[str, Any] | None,
    holders_by_code: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Copy quarterly holder-count fields onto stock recommendation items."""
    rec = dict(recommend or {})
    by_code = holders_by_code or {}
    items: list[dict[str, Any]] = []
    for raw in rec.get("items") or []:
        item = dict(raw)
        if str(item.get("kind") or "stock") != "stock":
            items.append(item)
            continue
        code = normalize_code(item.get("code"))
        h = by_code.get(code) if code else None
        if isinstance(h, dict) and h:
            item["holder_num"] = h.get("holder_num")
            item["holder_prev"] = h.get("holder_prev")
            item["holder_chg"] = h.get("holder_chg")
            item["holder_chg_pct"] = h.get("holder_chg_pct")
            item["holder_avg_wan"] = h.get("holder_avg_wan")
            item["holder_end"] = h.get("holder_end")
            item["holder_notice"] = h.get("holder_notice")
        items.append(item)
    rec["items"] = items
    return rec


def _annotate_watchlist_observe(
    rows: list[dict[str, Any]],
    verdict: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attach soft buy-readiness labels for personal watchlist observation."""
    from market_desk.config import BUY_DEMOTE_LOCK_NOTES, STOCK_NEAR_ENTRY_DOWN, STOCK_NEAR_ENTRY_UP

    v = verdict if isinstance(verdict, dict) else {}
    action = str(v.get("action") or "")
    notes = {str(n) for n in (v.get("algo_notes") or []) if n}
    gate_locked = bool(notes & set(BUY_DEMOTE_LOCK_NOTES)) or action in (
        "观望",
        "现金",
        "禁追",
    )
    desk_buyable = action in ("可买入", "可小仓") and not gate_locked
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        last = item.get("last")
        suggest = item.get("suggest_price")
        stop = item.get("stop_price")
        chase = item.get("chase_price")
        status = "观察中"
        tone = "slate"
        note = "盯价带，未到动手条件"
        try:
            last_f = float(last) if last not in (None, "") else None
        except (TypeError, ValueError):
            last_f = None
        if last_f is None:
            status, tone, note = "待行情", "slate", "等待报价刷新"
        else:
            try:
                stop_f = float(stop) if stop not in (None, "") else None
            except (TypeError, ValueError):
                stop_f = None
            try:
                chase_f = float(chase) if chase not in (None, "") else None
            except (TypeError, ValueError):
                chase_f = None
            try:
                sug_f = float(suggest) if suggest not in (None, "") else None
            except (TypeError, ValueError):
                sug_f = None
            if stop_f is not None and last_f <= stop_f:
                status, tone, note = "触及止损", "red", "现价≤止损，不宜按原计划买"
            elif chase_f is not None and last_f >= chase_f:
                status, tone, note = "勿追", "amber", "现价≥不追价，等回落"
            elif sug_f is not None and sug_f > 0:
                up = sug_f * (1.0 + float(STOCK_NEAR_ENTRY_UP))
                down = sug_f * (1.0 - float(STOCK_NEAR_ENTRY_DOWN))
                near = down <= last_f <= up
                diff_pct = (last_f - sug_f) / sug_f * 100.0
                diff_desc = "精准吻合" if abs(diff_pct) < 0.1 else f"{diff_pct:+.1f}%"
                if near:
                    if desk_buyable:
                        status, tone, note = "可试探", "green", f"贴近建议买点({diff_desc})且作战台未锁买"
                    elif gate_locked or action in ("观望", "观察回踩", "观察"):
                        status, tone, note = "到位·闸门未开", "amber", f"价位到位({diff_desc})，但市场闸门未开"
                    else:
                        status, tone, note = "回踩到位", "amber", f"贴近建议买点({diff_desc})，仍需对照作战台"
                elif last_f < down:
                    status, tone, note = "等回踩", "slate", f"低于建议带({diff_pct:.1f}%)，继续等"
                elif last_f > up:
                    status, tone, note = "偏高", "slate", f"高于建议带({diff_pct:+.1f}%)，勿追"
            elif not sug_f:
                status, tone, note = "观察中", "slate", "未设建议价，仅盯行情"
        item["observe_status"] = status
        item["observe_tone"] = tone
        item["observe_note"] = note
        out.append(item)
    return out


def _decorate_segments(
    rows: list[dict[str, Any]],
    current_key: str | None,
) -> list[dict[str, Any]]:
    """Merge saved segment rows into a fixed morning→afternoon strip."""
    from market_desk.session import SEGMENT_LABELS, SEGMENT_ORDER

    by = {str(r.get("segment")): dict(r) for r in rows or []}
    out: list[dict[str, Any]] = []
    for key in SEGMENT_ORDER:
        row = by.get(key) or {
            "segment": key,
            "label": SEGMENT_LABELS.get(key, key),
            "action": None,
            "mainline": "",
            "phase": "",
            "reason": "",
            "size_hint": "",
            "updated_at": None,
        }
        row["label"] = row.get("label") or SEGMENT_LABELS.get(key, key)
        row["current"] = key == current_key
        row["filled"] = bool(row.get("action"))
        out.append(row)
    return out


def _watch_pool(
    zt: list[dict[str, Any]],
    zb: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a compact intraday anomaly list (not a buy list)."""
    rows: list[dict[str, Any]] = []
    for item in sorted(zt, key=lambda x: int(x.get("boards") or 0), reverse=True)[:12]:
        rows.append(
            {
                **item,
                "group": "涨停",
                "tag": "禁追" if int(item.get("boards") or 0) >= 2 else "观察",
                "reason": f"{item.get('boards')}板涨停",
            }
        )
    for item in zb[:8]:
        rows.append({**item, "group": "炸板", "tag": "观察", "reason": "炸板"})
    dt = [q for q in quotes if is_limit_down(q.get("name"), q.get("pct"))]
    for item in dt[:6]:
        rows.append(
            {
                "code": item["code"],
                "name": item["name"],
                "pct": item["pct"],
                "group": "跌停",
                "tag": "观察",
                "reason": "跌停",
                "boards": 0,
            }
        )
    seen = {r["code"] for r in rows}
    hot_turn = sorted(
        [q for q in quotes if (q.get("turnover") or 0) >= 15 and q["code"] not in seen],
        key=lambda q: q.get("turnover") or 0,
        reverse=True,
    )
    for item in hot_turn[:6]:
        rows.append(
            {
                "code": item["code"],
                "name": item["name"],
                "pct": item["pct"],
                "group": "高换手",
                "tag": "禁追" if (item.get("pct") or 0) >= 7 else "观察",
                "reason": f"换手 {item.get('turnover'):.0f}%",
                "boards": 0,
            }
        )
    return rows[:24]


def _rough_watch_band(
    item: dict[str, Any],
    quote: dict[str, Any] | None = None,
) -> tuple[float | None, float | None, float | None]:
    """Derive a coarse suggest / stop / chase band for anomaly → watchlist."""
    q = quote or {}
    last = num(item.get("price") or item.get("last") or q.get("price"))
    if last is None or last <= 0:
        return None, None, None
    high = num(item.get("high") or q.get("high"))
    low = num(item.get("low") or q.get("low"))
    tag = str(item.get("tag") or "")
    group = str(item.get("group") or "")
    # Limit-up / chase tags: only watch a pullback; chase = last (do not chase).
    if tag == "禁追" or group == "涨停":
        suggest = round(last * 0.97, 2)
        stop = round((low if low and low < last else last * 0.94), 2)
        chase = round(last, 2)
        return suggest, stop, chase
    if group == "跌停":
        # Do not treat limit-down as a buy band; stop near last, chase slightly above.
        suggest = round(last * 1.01, 2)
        stop = round(last * 0.97, 2)
        chase = round(last * 1.03, 2)
        return suggest, stop, chase
    mid = (float(low) + float(last)) / 2.0 if low and low < last else last * 0.985
    suggest = round(min(float(last) * 0.985, mid), 2)
    stop = round(float(low) if low and low < last else last * 0.97, 2)
    chase = round(float(high) if high and high > last else last * 1.02, 2)
    if stop >= suggest:
        stop = round(suggest * 0.98, 2)
    if chase <= suggest:
        chase = round(suggest * 1.02, 2)
    return suggest, stop, chase


def _decorate_watch_pool(
    rows: list[dict[str, Any]],
    boards: list[dict[str, Any]] | None,
    mainline: str | None,
    watchlist_codes: set[str] | None = None,
    quotes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach board / mainline hints, rough price bands, and watchlist membership."""
    from market_desk.review import compare_boards_to_mainline, lookup_code_boards

    wl = watchlist_codes or set()
    qmap = {
        normalize_code(q.get("code")): q
        for q in (quotes or [])
        if normalize_code(q.get("code"))
    }
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        code = normalize_code(item.get("code"))
        names = lookup_code_boards(code, boards)
        cmp = compare_boards_to_mainline(names, mainline)
        item["board_names"] = cmp["boards"]
        item["board_text"] = cmp["board_text"] if names else ""
        item["vs_mainline"] = cmp["vs_mainline"] if names else None
        item["board_match"] = cmp["match"]
        item["in_watchlist"] = code in wl
        suggest, stop, chase = _rough_watch_band(item, qmap.get(code))
        item["suggest_price"] = suggest
        item["stop_price"] = stop
        item["chase_price"] = chase
        out.append(item)
    return out
