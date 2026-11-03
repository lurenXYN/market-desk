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
    p = _payload(row)
    raw = p.get("board_names") or row.get("boards") or []
    out: list[str] = []
    if isinstance(raw, list):
        out = [str(x).strip() for x in raw if str(x).strip()]
    sb = str(p.get("source_board") or row.get("source_board") or "").strip()
    ml = str(row.get("mainline") or p.get("mainline") or "").strip()
    for name in (sb, ml):
        if name and name not in out:
            out.append(name)
    return out


def _boards_from_scan(item: dict[str, Any]) -> list[str]:
    raw = item.get("boards") or []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


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
    """Return % of reference price vs chase (day3 close preferred, else last)."""
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
            "按三日%排序；相对不追%=三日收（或 last）相对冻结不追价。"
            "发散/锅底无不通价则相对不追为空；无三日%不进榜。"
        ),
    }


def build_code_appearances(code: str, *, limit: int = 120) -> dict[str, Any]:
    """Count signal / MA-fan / pot appearances and related frozen boards."""
    from market_desk.db import list_ma_fan_dates, load_ma_fan_day, load_signals_for_code
    from market_desk.review.outcome import _flatten_signal_prices
    from market_desk.review.signals import _dragon_hide_from_review, is_buy_signal

    c = normalize_code(code)
    if len(c) != 6:
        return {"ok": False, "code": c, "note": "代码须为 6 位"}

    sig_rows = []
    boards: list[str] = []
    for raw in load_signals_for_code(c, limit=limit):
        item = _flatten_signal_prices(raw)
        if _dragon_hide_from_review(item):
            continue
        if not is_buy_signal(item.get("signal_type")) and not str(
            item.get("signal_type") or ""
        ).startswith("sell"):
            continue
        for b in _boards_from_signal(item):
            if b not in boards:
                boards.append(b)
        sig_rows.append(
            {
                "trade_date": item.get("trade_date"),
                "name": item.get("name"),
                "signal_type": item.get("signal_type"),
                "outcome_label": item.get("outcome_label"),
                "outcome_day3_pct": item.get("outcome_day3_pct"),
                "mainline": item.get("mainline"),
                "boards": _boards_from_signal(item),
            }
        )

    ma_fan_days: list[dict[str, Any]] = []
    pot_days: list[dict[str, Any]] = []
    scan_name = ""
    for day in list_ma_fan_dates(limit=40):
        body = load_ma_fan_day(day) or {}
        for it in body.get("items") or []:
            if normalize_code(it.get("code")) != c:
                continue
            if not scan_name and it.get("name"):
                scan_name = str(it.get("name"))
            for b in _boards_from_scan(it):
                if b not in boards:
                    boards.append(b)
            fwd = it.get("fwd") if isinstance(it.get("fwd"), dict) else {}
            ma_fan_days.append(
                {
                    "trade_date": day,
                    "stage": it.get("stage"),
                    "theme_tag": it.get("theme_tag"),
                    "boards": _boards_from_scan(it),
                    "d3": num(fwd.get("d3")),
                }
            )
        for it in body.get("pot") or []:
            if normalize_code(it.get("code")) != c:
                continue
            if not scan_name and it.get("name"):
                scan_name = str(it.get("name"))
            for b in _boards_from_scan(it):
                if b not in boards:
                    boards.append(b)
            fwd = it.get("fwd") if isinstance(it.get("fwd"), dict) else {}
            pot_days.append(
                {
                    "trade_date": day,
                    "focus": bool(it.get("focus")),
                    "boards": _boards_from_scan(it),
                    "d3": num(fwd.get("d3")),
                }
            )

    name = next(
        (str(r.get("name") or "") for r in sig_rows if r.get("name")),
        "",
    ) or scan_name
    return {
        "ok": True,
        "code": c,
        "name": name or None,
        "signal_n": len(sig_rows),
        "ma_fan_n": len(ma_fan_days),
        "pot_n": len(pot_days),
        "board_set": boards,
        "signals": sig_rows[:60],
        "ma_fan_days": ma_fan_days,
        "pot_days": pot_days,
        "note": "信号次数含买卖；板名来自冻结 board_names / 扫描旁注，非今日成分回写。",
    }


def build_board_roster(
    query: str,
    *,
    days: int = 20,
    limit: int = 80,
) -> dict[str, Any]:
    """List codes that appeared under a board name in the recent window."""
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "q": q, "items": [], "note": "请输入板块关键词"}

    pack = collect_range_events(window="1m" if days >= 20 else ("2w" if days >= 10 else "1w"))
    # If caller asks for a custom day count, trim/recollect.
    if days not in (5, 10, 20):
        use_days = _active_trade_dates(max(1, min(int(days), 40)))
        events = (
            [_signal_event(r) for r in _signal_rows_for_days(use_days)]
            + _scan_events_for_days(use_days)
        )
        day_list = use_days
    else:
        events = pack["events"]
        day_list = pack["days"]

    q_l = q.lower()
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for ev in events:
        boards = [str(b) for b in (ev.get("boards") or [])]
        ml = str(ev.get("mainline") or "").strip()
        names = boards + ([ml] if ml else [])
        hit = next((b for b in names if q_l in b.lower() or b.lower() in q_l), None)
        if not hit:
            continue
        key = (str(ev.get("trade_date")), normalize_code(ev.get("code")), str(ev.get("source")))
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "trade_date": ev.get("trade_date"),
                "code": normalize_code(ev.get("code")),
                "name": ev.get("name"),
                "source": ev.get("source"),
                "board_hit": hit,
                "boards": boards,
                "mainline": ev.get("mainline"),
                "outcome_day3_pct": ev.get("outcome_day3_pct"),
                "outcome_label": ev.get("outcome_label"),
                "stage": ev.get("stage"),
                "tags": ev.get("tags") or [],
            }
        )
        if len(items) >= max(1, min(int(limit), 200)):
            break

    items.sort(key=lambda r: (str(r.get("trade_date") or ""), str(r.get("code") or "")), reverse=True)
    return {
        "ok": True,
        "q": q,
        "days": day_list,
        "n": len(items),
        "items": items,
        "note": "匹配冻结板名 / 主线旁注；含信号、发散、锅底。",
    }
