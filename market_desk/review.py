"""Signal logging and post-trade review helpers."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from market_desk.db import (
    list_signal_trade_dates,
    load_mainline_switches,
    load_review_digests,
    load_session_segments,
    load_signals,
    load_signals_for_date,
    mark_signal_outcome,
    save_review_digest,
    upsert_signal,
)
from market_desk.filters import normalize_code
from market_desk.numbers import num
from market_desk.settings import setting

# In-memory last-price ticks for short-horizon ↑/↓ on the review panel.
_PRICE_TICKS: dict[str, list[tuple[float, float]]] = {}
_LIVE_LOOKBACK_SEC = 60.0
_LIVE_KEEP_SEC = 240.0
_LIVE_FLAT_PCT = 0.08  # treat |Δ| below this as flat


def note_quote_ticks(quotes: dict[str, Any] | list[Any] | None) -> None:
    """Record latest prices so review can compare against ~1 minute ago."""
    if not quotes:
        return
    now = time.time()
    items: list[tuple[str, float]] = []
    if isinstance(quotes, dict):
        for code, row in quotes.items():
            if not isinstance(row, dict):
                continue
            px = num(row.get("price") or row.get("last"))
            c = normalize_code(code or row.get("code"))
            if c and px is not None:
                items.append((c, float(px)))
    else:
        for row in quotes:
            if not isinstance(row, dict):
                continue
            px = num(row.get("price") or row.get("last"))
            c = normalize_code(row.get("code"))
            if c and px is not None:
                items.append((c, float(px)))
    for code, px in items:
        series = _PRICE_TICKS.setdefault(code, [])
        if series and abs(series[-1][1] - px) < 1e-9 and now - series[-1][0] < 5:
            series[-1] = (now, px)
        else:
            series.append((now, px))
        cutoff = now - _LIVE_KEEP_SEC
        _PRICE_TICKS[code] = [t for t in series if t[0] >= cutoff]


def live_price_slope(code: str, last: float | None) -> dict[str, Any]:
    """Compare ``last`` with the price about one minute earlier.

    Returns arrow / vs-pct / lookback seconds for the review UI.
    """
    c = normalize_code(code)
    empty = {"live_arrow": None, "live_vs_pct": None, "live_vs_sec": None}
    if not c or last is None:
        return empty
    note_quote_ticks({c: {"price": last}})
    series = _PRICE_TICKS.get(c) or []
    if len(series) < 2:
        return empty
    now = time.time()
    target = now - _LIVE_LOOKBACK_SEC
    # Prefer a tick near the lookback window; fall back to the oldest kept tick.
    candidates = [t for t in series[:-1] if now - t[0] >= 25]
    if not candidates:
        return empty
    ref_ts, ref_px = min(candidates, key=lambda t: abs(t[0] - target))
    if ref_px <= 0:
        return empty
    vs = (float(last) / float(ref_px) - 1.0) * 100.0
    age = int(max(1, round(now - ref_ts)))
    if abs(vs) < _LIVE_FLAT_PCT:
        arrow = "flat"
    elif vs > 0:
        arrow = "up"
    else:
        arrow = "down"
    return {
        "live_arrow": arrow,
        "live_vs_pct": round(vs, 2),
        "live_vs_sec": age,
    }


def lookup_code_boards(
    code: str,
    boards: list[dict[str, Any]] | None,
    *,
    limit: int = 4,
) -> list[str]:
    """Return board names whose constituent pool contains the code."""
    c = normalize_code(code)
    if not c:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for board in boards or []:
        name = str(board.get("name") or "").strip()
        if not name or name in seen:
            continue
        pool = board.get("pool") or board.get("members") or []
        hit = False
        for member in pool:
            if normalize_code(member.get("code")) == c:
                hit = True
                break
        if not hit:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= limit:
            break
    return names


def resolve_day_mainline(trade_date: str) -> str | None:
    """Best-effort end-of-day / session mainline for a historical trade date.

    Preference: latest filled session segment → last switch ``to_name`` →
    most common signal-time mainline label that day.
    """
    day = str(trade_date or "").strip()[:10]
    if not day:
        return None
    order = {"afternoon": 4, "midday": 3, "open": 2, "auction": 1}
    best: str | None = None
    best_rank = -1
    for seg in load_session_segments(day):
        name = str(seg.get("mainline") or "").strip()
        if not name or name in ("未明", "—"):
            continue
        rank = order.get(str(seg.get("segment") or ""), 0)
        if rank >= best_rank:
            best_rank = rank
            best = name
    if best:
        return best
    switches = load_mainline_switches(day, limit=1)
    if switches:
        name = str(switches[0].get("to_name") or "").strip()
        if name and name not in ("未明", "—"):
            return name
    counts: dict[str, int] = {}
    for row in load_signals_for_date(day):
        name = str(row.get("mainline") or "").strip()
        if name and name not in ("未明", "—"):
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda x: x[1])[0]


def compare_boards_to_mainline(
    board_names: list[str] | None,
    mainline: str | None,
) -> dict[str, Any]:
    """Classify whether listed boards align with the session mainline."""
    boards = [str(x).strip() for x in (board_names or []) if str(x).strip()]
    ml = str(mainline or "").strip()
    if not boards:
        return {
            "boards": [],
            "board_text": "未归类",
            "vs_mainline": "未归类",
            "match": None,
        }
    board_text = " / ".join(boards)
    if not ml or ml in ("未明", "—"):
        return {
            "boards": boards,
            "board_text": board_text,
            "vs_mainline": "主线未明",
            "match": None,
        }
    if ml in boards:
        return {
            "boards": boards,
            "board_text": board_text,
            "vs_mainline": "属于主线",
            "match": True,
        }
    soft = any(ml in b or b in ml for b in boards)
    if soft:
        return {
            "boards": boards,
            "board_text": board_text,
            "vs_mainline": "接近主线",
            "match": True,
        }
    return {
        "boards": boards,
        "board_text": board_text,
        "vs_mainline": "偏离主线",
        "match": False,
    }


def enrich_signals_with_holders(
    rows: list[dict[str, Any]],
    holders_by_code: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Attach quarterly shareholder-count fields onto review signal rows."""
    by_code = holders_by_code or {}
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        code = normalize_code(item.get("code"))
        kind = str(item.get("kind") or "")
        if kind == "etf" or not code:
            out.append(item)
            continue
        h = by_code.get(code) or {}
        item["holder_num"] = h.get("holder_num")
        item["holder_prev"] = h.get("holder_prev")
        item["holder_chg"] = h.get("holder_chg")
        item["holder_chg_pct"] = h.get("holder_chg_pct")
        item["holder_avg_wan"] = h.get("holder_avg_wan")
        item["holder_end"] = h.get("holder_end")
        item["holder_notice"] = h.get("holder_notice")
        out.append(item)
    return out


def enrich_signals_with_boards(
    rows: list[dict[str, Any]],
    boards: list[dict[str, Any]] | None,
    *,
    live_mainline: str | None = None,
) -> list[dict[str, Any]]:
    """Attach board membership and compare them to the chosen mainline target.

    ``所属板块`` prefers the first stored board names. ``对照主线`` uses
    ``live_mainline`` when provided (caller selects live vs day-end target).
    """
    out: list[dict[str, Any]] = []
    live_ml = str(live_mainline or "").strip()
    for row in rows:
        item = dict(row)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        stored = payload.get("board_names")
        if stored is None:
            stored = item.get("boards")
        names = [str(x).strip() for x in (stored or []) if str(x).strip()]
        if not names:
            names = lookup_code_boards(item.get("code") or "", boards)
        # Hot/pin pools rotate; fall back to the signal's session mainline label
        # only as a board-name hint, not as the comparison target.
        if not names:
            ml = str(item.get("mainline") or "").strip()
            if ml and ml not in ("未明", "—"):
                names = [ml]
        compare_to = live_ml if live_ml and live_ml not in ("未明", "—") else item.get("mainline")
        cmp = compare_boards_to_mainline(names, compare_to)
        item["boards"] = cmp["boards"]
        item["board_text"] = cmp["board_text"]
        item["vs_mainline"] = cmp["vs_mainline"]
        item["board_match"] = cmp["match"]
        item["vs_mainline_of"] = str(compare_to or "").strip() or None
        out.append(item)
    return out


def record_session_signals(snapshot: dict[str, Any]) -> int:
    """Persist buy/sell recommendations for the current session. Return insert/update count."""
    trade_date = snapshot.get("trade_date") or ""
    if not trade_date:
        return 0
    signaled_at = snapshot.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    phase = snapshot.get("phase") or ""
    verdict = snapshot.get("verdict") or {}
    action = verdict.get("action") or ""
    mainline = ((verdict.get("mainline") or {}).get("name")) or ""
    board_cards = list(snapshot.get("hot_boards") or []) + list(
        snapshot.get("pin_boards") or []
    )
    n = 0

    rec = verdict.get("recommend") or {}
    # Log buys that matter for review: 可买入 always; 观察回踩 only near-entry/ready
    # (full wait-list dumps make the review panel too noisy).
    if rec.get("items"):
        for item in rec.get("items") or []:
            code = normalize_code(item.get("code"))
            if not code:
                continue
            ready = bool(item.get("ready"))
            near = bool(item.get("near_entry"))
            buyable = action in ("可买入", "可小仓") or bool(rec.get("buy"))
            if not (buyable or ready or near):
                continue
            kind = item.get("kind") or "stock"
            if kind == "etf" and not ready and not buyable and not near:
                continue
            price = num(item.get("buy_price"))
            if price is None:
                price = num(item.get("last"))
            if price is None:
                continue
            board_names = lookup_code_boards(code, board_cards)
            if not board_names and mainline:
                board_names = [mainline]
            board_cmp = compare_boards_to_mainline(board_names, mainline)
            upsert_signal(
                {
                    "trade_date": trade_date,
                    "signaled_at": signaled_at,
                    "signal_type": "buy",
                    "action": action,
                    "phase": phase,
                    "mainline": mainline,
                    "code": code,
                    "name": item.get("name") or "",
                    "kind": kind,
                    "price": float(price),
                    "last": num(item.get("last")),
                    "ready": 1 if item.get("ready") else 0,
                    "payload": {
                        "wait_price": item.get("wait_price"),
                        "stop_price": item.get("stop_price"),
                        "chase_price": item.get("chase_price"),
                        "pct": item.get("pct"),
                        "trend": item.get("trend"),
                        "trend_quality": item.get("trend_quality"),
                        "trend_pending": False,
                        "trend_unknown": bool(item.get("trend_unknown")),
                        "trend_ok": bool(item.get("trend_ok")),
                        "trend_down": bool(item.get("trend_down")),
                        "board_names": board_cmp["boards"],
                        "vs_mainline": board_cmp["vs_mainline"],
                        "board_match": board_cmp["match"],
                    },
                }
            )
            n += 1

    sell = snapshot.get("sell_advice") or {}
    for item in sell.get("items") or []:
        if not item.get("ready"):
            continue
        code = normalize_code(item.get("code"))
        if not code:
            continue
        price = num(item.get("sell_price"))
        if price is None:
            price = num(item.get("last"))
        if price is None:
            continue
        board_names = lookup_code_boards(code, board_cards)
        if not board_names and mainline:
            board_names = [mainline]
        board_cmp = compare_boards_to_mainline(board_names, mainline)
        upsert_signal(
            {
                "trade_date": trade_date,
                "signaled_at": signaled_at,
                "signal_type": "sell",
                "action": item.get("role_label") or "建议卖出",
                "phase": phase,
                "mainline": mainline,
                "code": code,
                "name": item.get("name") or "",
                "kind": item.get("kind") or "stock",
                "price": float(price),
                "last": num(item.get("last")),
                "ready": 1,
                "payload": {
                    "buy_price": item.get("buy_price"),
                    "stop_price": item.get("stop_price"),
                    "pnl_pct": item.get("pnl_pct"),
                    "qty": item.get("qty"),
                    "board_names": board_cmp["boards"],
                    "vs_mainline": board_cmp["vs_mainline"],
                    "board_match": board_cmp["match"],
                },
            }
        )
        n += 1
    return n


def score_signal_with_closes(
    signal: dict[str, Any],
    closes: list[float],
    dates: list[str] | None = None,
) -> dict[str, Any] | None:
    """Score a buy/sell signal against subsequent daily closes. Return outcome fields or None."""
    price = num(signal.get("fill_price"))
    if price is None or price <= 0:
        price = num(signal.get("price"))
    if price is None or price <= 0 or not closes:
        return None
    trade_date = str(signal.get("trade_date") or "")
    sig_type = signal.get("signal_type") or "buy"

    # Prefer closes strictly after the signal date when dates are available.
    after: list[float] = []
    if dates and len(dates) == len(closes) and trade_date:
        for d, px in zip(dates, closes):
            if str(d) > trade_date:
                after.append(float(px))
        if not after:
            return None
    else:
        return None
    if not after:
        return None

    day1 = after[0]
    day3 = after[min(2, len(after) - 1)]
    peak = max(after)
    trough = min(after)
    if sig_type == "buy":
        d1 = (day1 / price - 1.0) * 100.0
        d3 = (day3 / price - 1.0) * 100.0
        mfe = (peak / price - 1.0) * 100.0
        mae = (trough / price - 1.0) * 100.0
        if d1 >= 1.0:
            label = "次日红"
        elif d1 <= -1.5:
            label = "次日绿"
        elif d3 >= 2.0:
            label = "三日红"
        elif d3 <= -2.0:
            label = "三日绿"
        else:
            label = "平淡"
    else:
        # Sell: positive means avoiding further drop (price fell after sell).
        d1 = (price / day1 - 1.0) * 100.0
        d3 = (price / day3 - 1.0) * 100.0
        mfe = (price / trough - 1.0) * 100.0
        mae = (price / peak - 1.0) * 100.0
        if d1 >= 1.0:
            label = "卖后回落"
        elif d1 <= -1.5:
            label = "卖后继续涨"
        else:
            label = "平淡"

    return {
        "outcome_day1_pct": round(d1, 2),
        "outcome_day3_pct": round(d3, 2),
        "outcome_mfe_pct": round(mfe, 2),
        "outcome_mae_pct": round(mae, 2),
        "outcome_label": label,
        "outcome_checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def summarize_signals(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> dict[str, Any]:
    """Aggregate hit-rate style stats for the review panel.

    hit_mode:
      - traded: only rows marked traded (default, cleaner feedback loop)
      - all: any non-skipped scored row (paper signals)
    """
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    if mode not in ("traded", "all"):
        mode = "traded"
    active = [r for r in rows if not int(r.get("skipped") or 0)]
    buys = [r for r in active if r.get("signal_type") == "buy"]
    sells = [r for r in active if r.get("signal_type") == "sell"]
    scored_buys_all = [r for r in buys if r.get("outcome_label")]
    scored_buys = (
        [r for r in scored_buys_all if int(r.get("traded") or 0)]
        if mode == "traded"
        else scored_buys_all
    )
    scored_sells = [r for r in sells if r.get("outcome_label")]
    if mode == "traded":
        scored_sells = [r for r in scored_sells if int(r.get("traded") or 0)]

    def _rate(items: list[dict[str, Any]], good: set[str]) -> float | None:
        if not items:
            return None
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in good)
        return round(100.0 * hit / len(items), 1)

    def _avg(items: list[dict[str, Any]], key: str) -> float | None:
        vals = [num(r.get(key)) for r in items]
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    paper = [r for r in scored_buys_all if num(r.get("outcome_day3_pct")) is not None]
    paper_pnl = _avg(paper, "outcome_day3_pct")

    return {
        "buy_total": len(buys),
        "buy_scored": len(scored_buys),
        "buy_scored_all": len(scored_buys_all),
        "sell_total": len(sells),
        "sell_scored": len(scored_sells),
        "buy_hit_rate": _rate(scored_buys, {"次日红", "三日红"}),
        "buy_hit_rate_all": _rate(scored_buys_all, {"次日红", "三日红"}),
        "sell_hit_rate": _rate(scored_sells, {"卖后回落"}),
        "buy_avg_day1": _avg(scored_buys, "outcome_day1_pct"),
        "buy_avg_day3": _avg(scored_buys, "outcome_day3_pct"),
        "paper_avg_day3": paper_pnl,
        "hit_rate_mode": mode,
        "skipped": sum(1 for r in rows if int(r.get("skipped") or 0)),
        "traded": sum(1 for r in rows if int(r.get("traded") or 0)),
        "pending": sum(1 for r in active if not r.get("outcome_label")),
    }


def build_today_digest(
    rows: list[dict[str, Any]],
    *,
    trade_date: str,
    phase: str | None = None,
    switch_count: int | None = None,
) -> dict[str, Any]:
    """Build a same-day review digest for the summary strip."""
    today_rows = [r for r in rows if str(r.get("trade_date") or "") == trade_date]
    buys = [r for r in today_rows if r.get("signal_type") == "buy" and not int(r.get("skipped") or 0)]
    sells = [r for r in today_rows if r.get("signal_type") == "sell" and not int(r.get("skipped") or 0)]
    scored = [r for r in buys if r.get("outcome_label")]
    hit = sum(1 for r in scored if (r.get("outcome_label") or "") in {"次日红", "三日红"})
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


def classify_fill_execution(row: dict[str, Any]) -> str | None:
    """Classify a traded buy fill versus the original suggest / chase band."""
    if str(row.get("signal_type") or "") != "buy":
        return None
    if not int(row.get("traded") or 0):
        return None
    fill = num(row.get("fill_price"))
    if fill is None:
        return None
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    wait = num(row.get("wait_price") if row.get("wait_price") is not None else payload.get("wait_price"))
    chase = num(row.get("chase_price") if row.get("chase_price") is not None else payload.get("chase_price"))
    suggest = num(row.get("price"))
    low = wait if wait is not None else suggest
    if chase is not None and fill >= chase:
        return "chase"
    if low is not None and fill < low:
        return "below"
    if low is not None and chase is not None and low <= fill < chase:
        return "in_band"
    if low is not None and chase is None and fill >= low:
        return "in_band"
    return "other"


def build_exec_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Score how well fills followed the original price plan."""

    def _score_group(items: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {"in_band": 0, "chase": 0, "below": 0, "other": 0}
        for row in items:
            kind = classify_fill_execution(row)
            if kind in counts:
                counts[kind] += 1
            elif kind:
                counts["other"] += 1
        n = sum(counts.values())
        points = (
            counts["in_band"] * 100
            + counts["below"] * 90
            + counts["other"] * 40
            + counts["chase"] * 0
        )
        score = None if n == 0 else round(points / n, 1)
        return {
            "score": score,
            "traded_buy_n": len(items),
            "in_band_n": counts["in_band"],
            "chase_n": counts["chase"],
            "below_n": counts["below"],
            "other_n": counts["other"],
        }

    traded_buys = [
        r
        for r in rows
        if str(r.get("signal_type") or "") == "buy" and int(r.get("traded") or 0)
    ]
    etf = [r for r in traded_buys if str(r.get("kind") or "") == "etf"]
    stock = [r for r in traded_buys if str(r.get("kind") or "") != "etf"]
    overall = _score_group(traded_buys)
    overall["by_kind"] = {
        "etf": _score_group(etf),
        "stock": _score_group(stock),
    }
    return overall


def build_missed_buys(rows: list[dict[str, Any]], *, trade_date: str) -> list[dict[str, Any]]:
    """List same-day buys that were skipped or never traded while price already ran."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("trade_date") or "") != trade_date:
            continue
        if str(row.get("signal_type") or "") != "buy":
            continue
        skipped = int(row.get("skipped") or 0)
        traded = int(row.get("traded") or 0)
        flags = row.get("price_flags") or []
        miss = "miss_pullback" in flags or "未回踩" in str(row.get("price_mark") or "")
        if not ((skipped and miss) or (not traded and not skipped and miss)):
            continue
        out.append(
            {
                "id": row.get("id"),
                "code": row.get("code"),
                "name": row.get("name"),
                "price": row.get("price"),
                "live_last": row.get("live_last"),
                "dev_pct": row.get("dev_pct"),
                "skipped": skipped,
                "traded": traded,
                "price_mark": row.get("price_mark"),
                "mainline": row.get("mainline"),
                "signaled_at": row.get("signaled_at"),
            }
        )
    return out


def build_phase_hit_rates(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate buy hit-rate by market phase label on the signal row."""
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if str(row.get("signal_type") or "") != "buy":
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        phase = str(row.get("phase") or "未标").strip() or "未标"
        buckets.setdefault(phase, []).append(row)
    out: list[dict[str, Any]] = []
    for phase, items in sorted(buckets.items(), key=lambda x: (-len(x[1]), x[0])):
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in {"次日红", "三日红"})
        out.append(
            {
                "phase": phase,
                "scored_n": len(items),
                "hit_n": hit,
                "hit_rate": round(100.0 * hit / len(items), 1) if items else None,
            }
        )
    return out


def build_kind_hit_rates(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate buy hit-rate by instrument kind (etf / stock)."""
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    buckets: dict[str, list[dict[str, Any]]] = {"etf": [], "stock": []}
    for row in rows:
        if str(row.get("signal_type") or "") != "buy":
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        kind = "etf" if str(row.get("kind") or "") == "etf" else "stock"
        buckets[kind].append(row)
    out: list[dict[str, Any]] = []
    for kind in ("etf", "stock"):
        items = buckets[kind]
        if not items:
            continue
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in {"次日红", "三日红"})
        out.append(
            {
                "kind": kind,
                "label": "ETF" if kind == "etf" else "个股",
                "scored_n": len(items),
                "hit_n": hit,
                "hit_rate": round(100.0 * hit / len(items), 1),
            }
        )
    return out


def build_phase_kind_hit_rates(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate buy hit-rate by phase × kind for the review strip."""
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if str(row.get("signal_type") or "") != "buy":
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        phase = str(row.get("phase") or "未标").strip() or "未标"
        kind = "etf" if str(row.get("kind") or "") == "etf" else "stock"
        buckets.setdefault((phase, kind), []).append(row)
    out: list[dict[str, Any]] = []
    for (phase, kind), items in sorted(
        buckets.items(), key=lambda x: (-len(x[1]), x[0][0], x[0][1])
    ):
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in {"次日红", "三日红"})
        out.append(
            {
                "phase": phase,
                "kind": kind,
                "label": "ETF" if kind == "etf" else "个股",
                "scored_n": len(items),
                "hit_n": hit,
                "hit_rate": round(100.0 * hit / len(items), 1),
            }
        )
    return out


def build_tune_hints(
    *,
    missed: list[dict[str, Any]] | None,
    phase_hits: list[dict[str, Any]] | None,
    kind_hits: list[dict[str, Any]] | None,
) -> list[str]:
    """Return short threshold-tuning hints from review buckets (not orders)."""
    hints: list[str] = []
    missed_n = len(missed or [])
    if missed_n >= 3:
        hints.append(f"漏买 {missed_n} 笔：可略放宽回撤/到位容差，或检查是否总在追不追价")
    for row in kind_hits or []:
        rate = row.get("hit_rate")
        n = int(row.get("scored_n") or 0)
        if rate is None or n < 5:
            continue
        label = row.get("label") or row.get("kind")
        if float(rate) < 35:
            hints.append(f"{label}命中 {rate}%（n={n}）偏低：该品种宜更小仓或更严 ready")
        elif float(rate) >= 55 and n >= 8:
            hints.append(f"{label}命中 {rate}%（n={n}）尚可：可维持当前回撤门槛")
    for row in phase_hits or []:
        rate = row.get("hit_rate")
        n = int(row.get("scored_n") or 0)
        phase = str(row.get("phase") or "")
        if rate is None or n < 5:
            continue
        if phase == "高潮" and float(rate) < 40:
            hints.append(f"高潮相位命中 {rate}%：继续默认降观察回踩，勿追尖")
        if phase == "恐慌" and float(rate) < 30:
            hints.append(f"恐慌相位命中 {rate}%：维持禁开仓")
    return hints[:5]


def snapshot_quote_map(snapshot: dict[str, Any] | None) -> dict[str, float]:
    """Collect last prices from the live snapshot for band checks."""
    out: dict[str, float] = {}
    if not snapshot:
        return out

    def _put(code: Any, price: Any) -> None:
        c = normalize_code(code)
        px = num(price)
        if c and px is not None:
            out[c] = float(px)

    for row in snapshot.get("etfs") or []:
        if isinstance(row, dict):
            _put(row.get("code"), row.get("price"))
    for row in snapshot.get("positions") or []:
        if isinstance(row, dict):
            _put(row.get("code"), row.get("last") or row.get("price"))
    for row in snapshot.get("watch") or []:
        if isinstance(row, dict):
            _put(row.get("code"), row.get("price") or row.get("last"))
    for row in snapshot.get("watchlist") or []:
        if isinstance(row, dict):
            _put(row.get("code"), row.get("last") or row.get("price"))
    rec = ((snapshot.get("verdict") or {}).get("recommend") or {}).get("items") or []
    for row in rec:
        if isinstance(row, dict):
            _put(row.get("code"), row.get("last") or row.get("price"))
    return out


def _entry_band_worth_alert(
    *,
    last: float,
    low: float,
    chase: float,
    pct: float | None,
) -> bool:
    """
    Return True only for a meaningful pullback entry, not a late chase inside the band.

    Suppress when the day move is already hot, or price sits in the upper half
    of [wait, chase) — those are usually already-extended prints, not buys.
    """
    if chase <= low:
        return False
    if pct is not None and float(pct) >= 5.0:
        return False
    # Upper ~40% of the band ≈ already near 不追; only alert the lower pullback zone.
    if (float(last) - float(low)) / (float(chase) - float(low)) >= 0.40:
        return False
    return True


def build_price_touch_alerts(snapshot: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    """Emit toasts when live price hits stop / chase / buy-band on today's plans."""
    if not snapshot or not snapshot.get("ok"):
        return []
    trade_date = str(snapshot.get("trade_date") or "")
    if not trade_date:
        return []
    quotes = snapshot_quote_map(snapshot)
    # Prefer live recommend plans; fall back to today's logged buy signals.
    plans: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ((snapshot.get("verdict") or {}).get("recommend") or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        code = normalize_code(item.get("code"))
        if not code or code in seen:
            continue
        seen.add(code)
        plans.append(
            {
                "code": code,
                "name": item.get("name") or code,
                "last": num(item.get("last")) or quotes.get(code),
                "pct": num(item.get("pct")),
                "buy": num(item.get("buy_price")),
                "wait": num(item.get("wait_price")),
                "stop": num(item.get("stop_price")),
                "chase": num(item.get("chase_price")),
            }
        )
    for row in load_signals_for_date(trade_date):
        if str(row.get("signal_type") or "") != "buy":
            continue
        if int(row.get("skipped") or 0):
            continue
        code = normalize_code(row.get("code"))
        if not code or code in seen:
            continue
        seen.add(code)
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        plans.append(
            {
                "code": code,
                "name": row.get("name") or code,
                "last": quotes.get(code) or num(row.get("last")),
                "pct": None,
                "buy": num(row.get("price")),
                "wait": num(payload.get("wait_price")),
                "stop": num(payload.get("stop_price")),
                "chase": num(payload.get("chase_price")),
            }
        )

    alerts: list[tuple[str, str, str]] = []
    day_rows = load_signals_for_date(trade_date)
    traded_codes = {
        normalize_code(r.get("code"))
        for r in day_rows
        if int(r.get("traded") or 0)
    }
    open_codes = {
        normalize_code(r.get("code"))
        for r in (snapshot.get("positions") or [])
        if isinstance(r, dict) and int(r.get("qty") or 0) > 0
    }
    # Already bought → entry / chase bands are noise; keep stop only.
    owned_codes = {c for c in (traded_codes | open_codes) if c}

    for p in plans:
        code = p["code"]
        last = p.get("last")
        if last is None:
            continue
        name = p.get("name") or code
        stop = p.get("stop")
        chase = p.get("chase")
        wait = p.get("wait")
        buy = p.get("buy")
        low = wait if wait is not None else buy
        pct = p.get("pct")
        owned = code in owned_codes
        if stop is not None and last <= stop:
            alerts.append(
                (
                    f"band:stop:{code}",
                    "触及止损",
                    f"{name} {code} 现价 {last} ≤ 止损 {stop}",
                )
            )
        elif owned:
            continue
        elif chase is not None and last >= chase:
            alerts.append(
                (
                    f"band:chase:{code}",
                    "触及不追",
                    f"{name} {code} 现价 {last} ≥ 不追 {chase}，不宜追高",
                )
            )
        elif (
            low is not None
            and chase is not None
            and low <= last < chase
            and _entry_band_worth_alert(last=float(last), low=float(low), chase=float(chase), pct=pct)
        ):
            alerts.append(
                (
                    f"band:entry:{code}",
                    "进入可买带",
                    f"{name} {code} 现价 {last} · 建议/回踩 {low} · 不追 {chase}",
                )
            )
    for row in snapshot.get("watchlist") or []:
        if not isinstance(row, dict):
            continue
        code = normalize_code(row.get("code"))
        if not code:
            continue
        last = num(row.get("last")) or quotes.get(code)
        if last is None:
            continue
        name = row.get("name") or code
        stop = num(row.get("stop_price"))
        chase = num(row.get("chase_price"))
        suggest = num(row.get("suggest_price"))
        owned = code in owned_codes
        if stop is not None and last <= stop:
            alerts.append(
                (
                    f"wl:stop:{code}",
                    "自选触及止损",
                    f"{name} {code} 现价 {last} ≤ 止损 {stop}",
                )
            )
        elif owned:
            continue
        elif chase is not None and last >= chase:
            alerts.append(
                (
                    f"wl:chase:{code}",
                    "自选触及不追",
                    f"{name} {code} 现价 {last} ≥ 不追 {chase}",
                )
            )
        elif suggest is not None and abs(last - suggest) / max(suggest, 1e-9) <= 0.008:
            alerts.append(
                (
                    f"wl:suggest:{code}",
                    "自选靠近建议价",
                    f"{name} {code} 现价 {last} ≈ 建议 {suggest}",
                )
            )
    # Alert scope: all plans, or only traded / watchlist to cut toast noise.
    mode = str(setting("alert_mode", "traded_watch") or "traded_watch")
    if mode == "off":
        return []
    watch_codes = {
        normalize_code(r.get("code"))
        for r in (snapshot.get("watchlist") or [])
        if isinstance(r, dict)
    }
    filtered: list[tuple[str, str, str]] = []
    for key, title, body in alerts:
        code = key.rsplit(":", 1)[-1]
        is_wl = key.startswith("wl:")
        if mode == "watch_only" and not is_wl:
            continue
        if mode == "traded_watch" and not (is_wl or code in traded_codes or code in watch_codes):
            continue
        filtered.append((key, title, body))
    return filtered


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
            str(item.get("signal_type") or "") == "buy"
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
        item["live_last"] = last
        item["live_pct"] = num(q.get("pct"))
        item.update(live_price_slope(code, last))
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
        # Buying caution for same-day signals.
        if str(item.get("signal_type") or "") == "buy":
            if "stop_hit" in flags:
                item["buy_caution"] = "现价已到止损带，当日不宜再按原计划买"
            elif "chase_hit" in flags:
                item["buy_caution"] = "现价已过不追价，当日不宜追高"
            elif "miss_pullback" in flags:
                item["buy_caution"] = "未回踩建议价已上行，勿死等；可对照不追价决定是否放弃"
            elif "near_wait" in flags:
                item["buy_caution"] = "现价在回踩带，可观察是否站稳"
            else:
                item["buy_caution"] = ""
        else:
            item["buy_caution"] = ""
        out.append(item)
    return out


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
) -> dict[str, Any]:
    """Load one trade-date's signals plus global summary for the review tab."""
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

    global_rows = [_flatten_signal_prices(r) for r in load_signals(limit=limit)]
    day_rows = [_flatten_signal_prices(r) for r in load_signals_for_date(day)]
    if quotes:
        day_rows = enrich_signals_with_live_marks(day_rows, quotes)
    day_rows = enrich_signals_with_boards(
        day_rows, boards, live_mainline=compare_ml
    )
    if holders:
        day_rows = enrich_signals_with_holders(day_rows, holders)
    day_phase = phase
    if not day_phase and day_rows:
        day_phase = str(day_rows[0].get("phase") or "") or None
    digest = build_today_digest(day_rows, trade_date=day, phase=day_phase)
    if day == calendar_today or day_rows:
        try:
            save_review_digest(day, digest)
        except Exception:
            pass
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
    summary["phase_hits"] = build_phase_hit_rates(global_rows)
    summary["kind_hits"] = build_kind_hit_rates(global_rows)
    summary["phase_kind_hits"] = build_phase_kind_hit_rates(global_rows)
    summary["missed_buys"] = build_missed_buys(day_rows, trade_date=day)
    summary["tune_hints"] = build_tune_hints(
        missed=summary["missed_buys"],
        phase_hits=summary["phase_hits"],
        kind_hits=summary["kind_hits"],
    )
    return {
        "ok": True,
        "signals": day_rows,
        "summary": summary,
        "view_date": day,
        "calendar_today": calendar_today,
        "dates": dates,
    }


def _flatten_signal_prices(row: dict[str, Any]) -> dict[str, Any]:
    """Copy plan prices from payload onto the top-level signal row."""
    item = dict(row)
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    if item.get("chase_price") is None:
        item["chase_price"] = num(payload.get("chase_price"))
    if item.get("wait_price") is None:
        item["wait_price"] = num(payload.get("wait_price"))
    if item.get("stop_price") is None:
        item["stop_price"] = num(payload.get("stop_price"))
    return item


def apply_outcomes(
    rows: list[dict[str, Any]],
    closes_map: dict[str, tuple[list[str], list[float]]],
) -> int:
    """Write scored outcomes for signals that have forward closes. Return update count."""
    n = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for row in rows:
        if row.get("outcome_label"):
            continue
        if str(row.get("trade_date") or "") >= today:
            continue
        code = normalize_code(row.get("code"))
        packed = closes_map.get(code)
        if not packed:
            continue
        dates, closes = packed
        outcome = score_signal_with_closes(row, closes, dates)
        if not outcome:
            continue
        if mark_signal_outcome(int(row["id"]), outcome):
            n += 1
    return n
