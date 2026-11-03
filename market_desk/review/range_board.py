"""Range leaderboard and code/board appearance search for the review tab.

Merges buy signals with MA-fan / pot scan hits over recent active trade days.
Observation only — does not change ready or adapt knobs.
"""

from __future__ import annotations

from typing import Any

from market_desk.filters import normalize_code
from market_desk.numbers import num

WINDOW_DAYS = {"1w": 5, "2w": 10, "1m": 20}


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row.get("payload")
    return p if isinstance(p, dict) else {}


def _boards_from_signal(row: dict[str, Any]) -> list[str]:
    """Prefer frozen board_names, then source_board / mainline."""
    from market_desk.db.code_board_seen import boards_from_signal

    return boards_from_signal(row)


def _boards_from_scan(item: dict[str, Any]) -> list[str]:
    """Return scan side-note boards (plus the stored industry)."""
    from market_desk.db.code_board_seen import boards_from_scan_item

    return boards_from_scan_item(item)


def _entry_price(row: dict[str, Any]) -> float | None:
    """Classic entry: fill if present else plan/price."""
    fill = num(row.get("fill_price"))
    if fill is not None and fill > 0:
        return float(fill)
    p = _payload(row)
    plan = num(row.get("plan_price") if row.get("plan_price") is not None else p.get("plan_price"))
    if plan is not None and plan > 0:
        return float(plan)
    px = num(row.get("price"))
    return float(px) if px is not None and px > 0 else None


def _vs_chase_pct(
    *,
    chase: float | None,
    entry: float | None,
    day3_pct: float | None,
    last: float | None,
) -> float | None:
    """Return % of day3 close (or fallback last) vs chase — outcome column."""
    if chase is None or chase <= 0:
        return None
    ref: float | None = None
    if day3_pct is not None and entry is not None and entry > 0:
        ref = float(entry) * (1.0 + float(day3_pct) / 100.0)
    elif last is not None and last > 0:
        ref = float(last)
    if ref is None or ref <= 0:
        return None
    return round((ref / float(chase) - 1.0) * 100.0, 2)


def _vs_chase_now_pct(*, chase: float | None, live_last: float | None) -> float | None:
    """Return % of live last vs chase — same口径 as review「偏离」under 不追价."""
    if chase is None or chase <= 0:
        return None
    if live_last is None or live_last <= 0:
        return None
    return round((float(live_last) / float(chase) - 1.0) * 100.0, 2)


def _active_trade_dates(limit: int) -> list[str]:
    """Newest-first union of signal days and MA-fan scan days."""
    from market_desk.db import list_ma_fan_dates, list_signal_trade_dates

    need = max(1, int(limit))
    sig = list_signal_trade_dates(limit=need * 2)
    fan = list_ma_fan_dates(limit=need * 2)
    seen: set[str] = set()
    out: list[str] = []
    for d in sorted(set(sig) | set(fan), reverse=True):
        day = str(d or "")[:10]
        if len(day) != 10 or day in seen:
            continue
        seen.add(day)
        out.append(day)
        if len(out) >= need:
            break
    return out


def _signal_rows_for_days(days: list[str]) -> list[dict[str, Any]]:
    """Load buy signals for the given trade dates (newest-first days)."""
    from market_desk.db import load_signals_for_date
    from market_desk.review.outcome import _flatten_signal_prices
    from market_desk.review.signals import _dragon_hide_from_review, is_buy_signal

    out: list[dict[str, Any]] = []
    for day in days:
        for raw in load_signals_for_date(day):
            if not is_buy_signal(raw.get("signal_type")):
                continue
            if int(raw.get("skipped") or 0):
                continue
            item = _flatten_signal_prices(raw)
            if _dragon_hide_from_review(item):
                continue
            out.append(item)
    return out


def _scan_events_for_days(days: list[str]) -> list[dict[str, Any]]:
    """Flatten MA-fan items and pot hits into appearance events."""
    from market_desk.db import load_ma_fan_day

    events: list[dict[str, Any]] = []
    for day in days:
        body = load_ma_fan_day(day) or {}
        for src, key in (("ma_fan", "items"), ("pot", "pot")):
            for it in body.get(key) or []:
                if not isinstance(it, dict):
                    continue
                code = normalize_code(it.get("code"))
                if len(code) != 6:
                    continue
                fwd = it.get("fwd") if isinstance(it.get("fwd"), dict) else {}
                d3 = num(fwd.get("d3"))
                events.append(
                    {
                        "trade_date": day,
                        "code": code,
                        "name": it.get("name"),
                        "kind": "stock",
                        "source": src,
                        "boards": _boards_from_scan(it),
                        "mainline": it.get("theme_tag") or None,
                        "chase_price": None,
                        "vs_chase_pct": None,
                        "outcome_day3_pct": d3,
                        "outcome_label": (
                            "三日红"
                            if d3 is not None and d3 >= 1.0
                            else ("三日绿" if d3 is not None else None)
                        ),
                        "stage": it.get("stage") or it.get("tag") or None,
                        "tags": list(it.get("tags") or [])[:6],
                        "last": num(it.get("close") or it.get("last")),
                        "fwd_final": bool(fwd.get("final")),
                    }
                )
    return events


def _signal_event(row: dict[str, Any]) -> dict[str, Any]:
    """Map one buy signal into a unified appearance event."""
    p = _payload(row)
    chase = num(row.get("chase_price") if row.get("chase_price") is not None else p.get("chase_price"))
    d3 = num(row.get("outcome_day3_pct"))
    entry = _entry_price(row)
    last = num(row.get("last") if row.get("last") is not None else p.get("last"))
    return {
        "trade_date": str(row.get("trade_date") or "")[:10],
        "code": normalize_code(row.get("code")),
        "name": row.get("name"),
        "kind": row.get("kind") or "stock",
        "source": "signal",
        "boards": _boards_from_signal(row),
        "mainline": row.get("mainline"),
        "chase_price": chase,
        "vs_chase_pct": _vs_chase_pct(chase=chase, entry=entry, day3_pct=d3, last=last),
        "outcome_day3_pct": d3,
        "outcome_label": row.get("outcome_label"),
        "desk_source": row.get("desk_source") or p.get("desk_source"),
        "ready": bool(row.get("ready")),
        "signal_id": row.get("id"),
        "last": last,
        "entry_price": entry,
    }


def collect_range_events(*, window: str = "1w") -> dict[str, Any]:
    """Collect merged appearance events for a named window."""
    n = int(WINDOW_DAYS.get(str(window or "1w"), 5))
    days = _active_trade_dates(n)
    sig_events = [_signal_event(r) for r in _signal_rows_for_days(days)]
    scan_events = _scan_events_for_days(days)
    return {
        "window": window if window in WINDOW_DAYS else "1w",
        "window_days": n,
        "days": days,
        "events": sig_events + scan_events,
    }


def _fold_by_code(events: list[dict[str, Any]], *, side: str) -> list[dict[str, Any]]:
    """Collapse to one row per code; keep best/worst day3 among scored events."""
    want_worst = str(side or "worst").lower() != "best"
    by: dict[str, dict[str, Any]] = {}
    for ev in events:
        code = normalize_code(ev.get("code"))
        if len(code) != 6:
            continue
        d3 = num(ev.get("outcome_day3_pct"))
        cur = by.get(code)
        if cur is None:
            row = dict(ev)
            row["sources"] = [str(ev.get("source") or "")]
            row["appear_n"] = 1
            boards = list(ev.get("boards") or [])
            row["boards"] = boards
            row["_has_d3"] = d3 is not None
            by[code] = row
            continue
        src = str(ev.get("source") or "")
        if src and src not in cur["sources"]:
            cur["sources"].append(src)
        cur["appear_n"] = int(cur.get("appear_n") or 1) + 1
        for b in ev.get("boards") or []:
            if b and b not in cur["boards"]:
                cur["boards"].append(b)
        # Prefer scored day3 for ranking; among scored, keep extreme for side.
        if d3 is None:
            continue
        cur_d3 = num(cur.get("outcome_day3_pct"))
        take = False
        if cur_d3 is None:
            take = True
        elif want_worst and d3 < cur_d3:
            take = True
        elif (not want_worst) and d3 > cur_d3:
            take = True
        if take:
            for key in (
                "trade_date",
                "outcome_day3_pct",
                "outcome_label",
                "chase_price",
                "vs_chase_pct",
                "mainline",
                "desk_source",
                "name",
                "kind",
                "signal_id",
                "stage",
                "tags",
            ):
                if key in ev:
                    cur[key] = ev.get(key)
            # Recompute vs_chase if signal chase present on kept event.
            if ev.get("source") == "signal":
                cur["vs_chase_pct"] = ev.get("vs_chase_pct")
            cur["_has_d3"] = True
            if ev.get("boards"):
                # Keep boards union already; main boards from extreme day stay via mainline.
                pass
    return list(by.values())


def build_range_leaderboard(
    *,
    window: str = "1w",
    side: str = "worst",
    limit: int = 30,
) -> dict[str, Any]:
    """Rank folded codes by outcome_day3_pct over the active window."""
    pack = collect_range_events(window=window)
    folded = _fold_by_code(pack["events"], side=side)
    scored = [r for r in folded if num(r.get("outcome_day3_pct")) is not None]
    unscored_n = len(folded) - len(scored)
    reverse = str(side or "worst").lower() == "best"
    scored.sort(key=lambda r: float(r.get("outcome_day3_pct") or 0.0), reverse=reverse)
    lim = max(1, min(int(limit), 100))
    items = []
    for r in scored[:lim]:
        items.append(
            {
                "trade_date": r.get("trade_date"),
                "code": r.get("code"),
                "name": r.get("name"),
                "kind": r.get("kind") or "stock",
                "boards": list(r.get("boards") or []),
                "board_text": " / ".join(r.get("boards") or []) or "—",
                "chase_price": r.get("chase_price"),
                "vs_chase_pct": r.get("vs_chase_pct"),
                "vs_chase_now_pct": None,
                "live_last": None,
                "outcome_day3_pct": r.get("outcome_day3_pct"),
                "outcome_label": r.get("outcome_label"),
                "sources": list(r.get("sources") or []),
                "appear_n": r.get("appear_n"),
                "mainline": r.get("mainline"),
                "desk_source": r.get("desk_source"),
            }
        )
    return {
        "ok": True,
        "window": pack["window"],
        "window_days": pack["window_days"],
        "days": pack["days"],
        "side": "best" if reverse else "worst",
        "n": len(items),
        "scored_n": len(scored),
        "unscored_n": unscored_n,
        "items": items,
        "note": (
            "按三日%排序；相对不追·三日=三日收相对冻结不追价；"
            "相对不追·现价=现价相对不追价（同复盘表偏离）。"
            "发散/锅底无不通价则两列皆空；无三日%不进榜。"
        ),
    }


async def attach_live_vs_chase(pack: dict[str, Any]) -> dict[str, Any]:
    """Fill ``vs_chase_now_pct`` / ``live_last`` from a short quote round-trip."""
    import httpx

    from market_desk.tencent import fetch_quotes

    items = list(pack.get("items") or [])
    codes = [
        str(it.get("code") or "")
        for it in items
        if num(it.get("chase_price")) is not None and str(it.get("code") or "")
    ]
    if not codes:
        return pack
    quotes: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0)) as client:
            quotes = await fetch_quotes(client, codes)
    except Exception:
        quotes = {}
    for it in items:
        chase = num(it.get("chase_price"))
        q = quotes.get(str(it.get("code") or "")) or {}
        live = num(q.get("price") if q.get("price") is not None else q.get("last"))
        it["live_last"] = live
        it["vs_chase_now_pct"] = _vs_chase_now_pct(chase=chase, live_last=live)
    return pack


def _days_ago(day: str) -> int | None:
    """Return calendar days from ``day`` to today (Asia/Shanghai)."""
    from datetime import date

    from market_desk.review.outcome import _cn_now

    raw = str(day or "")[:10]
    if len(raw) != 10:
        return None
    try:
        d0 = date.fromisoformat(raw)
    except ValueError:
        return None
    return max(0, (_cn_now().date() - d0).days)


def _seen_to_scan_row(r: dict[str, Any]) -> dict[str, Any]:
    """Map one indexed MA-fan / pot appearance into an API scan row."""
    boards = list(r.get("boards") or [])
    src = str(r.get("source") or "")
    return {
        "source": src,
        "trade_date": r.get("trade_date"),
        "close": num(r.get("price")),
        "pct": num(r.get("pct")),
        "days_ago": _days_ago(str(r.get("trade_date") or "")),
        "d3": num(r.get("d3")),
        "stage": r.get("tag"),
        "tag": r.get("tag") or None,
        "focus": ("浅跌" in str(r.get("tag") or "")) if src == "pot" else None,
        "boards": boards,
        "board_text": " / ".join(boards) or "—",
    }


def build_code_appearances(code: str, *, limit: int = 400) -> dict[str, Any]:
    """Return indexed signal / MA-fan / pot appearances plus frozen and current boards.

    Reads only the local ``code_board_seen`` index (and today's snapshot for the
    separate ``current_boards`` hint); no live network request.
    """
    from market_desk.db import current_boards_for_code, load_seen_by_code

    c = normalize_code(code)
    if len(c) != 6:
        return {"ok": False, "code": c, "note": "代码须为 6 位"}

    rows = load_seen_by_code(c, limit=limit)
    boards: list[str] = []
    sig_rows: list[dict[str, Any]] = []
    ma_fan_days: list[dict[str, Any]] = []
    pot_days: list[dict[str, Any]] = []
    scan_rows: list[dict[str, Any]] = []
    name = ""
    for r in rows:
        if not name and r.get("name"):
            name = str(r.get("name"))
        for b in r.get("boards") or []:
            if b not in boards:
                boards.append(b)
        src = str(r.get("source") or "")
        if src == "signal":
            sig_rows.append(
                {
                    "trade_date": r.get("trade_date"),
                    "name": r.get("name"),
                    "signal_type": r.get("signal_type") or r.get("tag"),
                    "price": num(r.get("price")),
                    "outcome_label": r.get("outcome_label"),
                    "outcome_day3_pct": r.get("outcome_day3_pct"),
                    "mainline": r.get("mainline"),
                    "boards": list(r.get("boards") or []),
                }
            )
            continue
        row = _seen_to_scan_row(r)
        scan_rows.append(row)
        (ma_fan_days if src == "ma_fan" else pot_days).append(row)

    scan_rows.sort(
        key=lambda x: (str(x.get("trade_date") or ""), str(x.get("source") or "")),
        reverse=True,
    )
    cur = current_boards_for_code(c)
    return {
        "ok": True,
        "code": c,
        "name": name or None,
        "signal_n": len(sig_rows),
        "ma_fan_n": len(ma_fan_days),
        "pot_n": len(pot_days),
        "board_set": boards,
        "current_boards": cur,
        "signals": sig_rows[:60],
        "ma_fan_days": ma_fan_days,
        "pot_days": pot_days,
        "scan_rows": scan_rows,
        "note": (
            "读本地出现索引：冻结板名（当时挂上的）+ 当时价；"
            "现况板来自今日成分快照，仅供对照，不进榜。"
        ),
    }


def build_board_roster(
    query: str,
    *,
    days: int = 20,
    limit: int = 80,
) -> dict[str, Any]:
    """List codes that appeared under a board keyword in the recent indexed window."""
    from market_desk.db import load_seen_by_board

    q = str(query or "").strip()
    if not q:
        return {"ok": False, "q": q, "items": [], "note": "请输入板块关键词"}

    lim = max(1, min(int(limit), 200))
    rows = load_seen_by_board(q, days=days, limit=lim * 3)
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for r in rows:
        key = (str(r.get("trade_date")), str(r.get("code")), str(r.get("source")))
        if key in seen:
            continue
        seen.add(key)
        d3 = r.get("outcome_day3_pct")
        if d3 is None:
            d3 = r.get("d3")
        boards_text = str(r.get("boards_text") or "")
        items.append(
            {
                "trade_date": r.get("trade_date"),
                "code": r.get("code"),
                "name": r.get("name"),
                "source": r.get("source"),
                "board_hit": r.get("board_name"),
                "boards": [b for b in boards_text.split(" / ") if b],
                "price": num(r.get("price")),
                "days_ago": _days_ago(str(r.get("trade_date") or "")),
                "mainline": r.get("mainline"),
                "outcome_day3_pct": num(d3),
                "outcome_label": r.get("outcome_label"),
                "stage": r.get("tag"),
            }
        )
        if len(items) >= lim:
            break

    items.sort(
        key=lambda x: (str(x.get("trade_date") or ""), str(x.get("code") or "")),
        reverse=True,
    )
    return {
        "ok": True,
        "q": q,
        "n": len(items),
        "items": items,
        "note": "读本地出现索引；匹配冻结板名（含后缀归一）；含信号、发散、锅底。",
    }
