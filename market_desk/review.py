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
    load_signals_for_code,
    load_signals_for_date,
    mark_signal_outcome,
    apply_signal_user_meta,
    save_review_digest,
    upsert_signal,
)
from market_desk.filters import normalize_code
from market_desk.zt_stats import enrich_signals_with_zt_ytd
from market_desk.numbers import num
from market_desk.settings import setting

# In-memory last-price ticks for short-horizon ↑/↓ on the review panel.
_PRICE_TICKS: dict[str, list[tuple[float, float]]] = {}
_LIVE_LOOKBACK_SEC = 60.0
_LIVE_KEEP_SEC = 240.0
_LIVE_FLAT_PCT = 0.08  # treat |Δ| below this as flat

# Buy-family signal_type values (UNIQUE key includes type so sources coexist).
BUY_SIGNAL_TYPES = frozenset(
    {"buy", "buy_side", "buy_link", "buy_trial", "buy_indep", "buy_dragon"}
)


def is_buy_signal(sig_type: Any) -> bool:
    """Return True for main / side / link / trial buy signal types."""
    t = str(sig_type or "").strip().lower()
    return t in BUY_SIGNAL_TYPES or (t.startswith("buy_") and t != "buy_sell")


def is_sell_signal(sig_type: Any) -> bool:
    """Return True for sell review signals."""
    return str(sig_type or "").strip().lower() == "sell"


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
    *,
    role: str = "main",
) -> dict[str, Any]:
    """Classify whether listed boards align with a compare target.

    ``role`` selects display labels for sticky main / side / link targets.
    Soft match prefers substring, then ``same_theme`` (e.g. 煤炭↔动力煤).
    """
    boards = [str(x).strip() for x in (board_names or []) if str(x).strip()]
    ml = str(mainline or "").strip()
    role_key = str(role or "main").strip().lower()
    if role_key not in ("main", "side", "link"):
        role_key = "main"
    labels = {
        "main": {
            "empty": "未归类",
            "unknown": "主线未明",
            "belong": "属于主线",
            "near": "接近主线",
            "theme": "同题材",
            "off": "偏离主线",
        },
        "side": {
            "empty": "未归类",
            "unknown": "支线未明",
            "belong": "贴支线",
            "near": "接近支线",
            "theme": "同题材·支线",
            "off": "偏离支线",
        },
        "link": {
            "empty": "未归类",
            "unknown": "联动未明",
            "belong": "贴联动",
            "near": "接近联动",
            "theme": "同题材·联动",
            "off": "偏离联动",
        },
    }[role_key]

    if not boards:
        return {
            "boards": [],
            "board_text": labels["empty"],
            "vs_mainline": labels["empty"],
            "match": None,
            "align": "empty",
            "role": role_key,
        }
    board_text = " / ".join(boards)
    if not ml or ml in ("未明", "—"):
        return {
            "boards": boards,
            "board_text": board_text,
            "vs_mainline": labels["unknown"],
            "match": None,
            "align": "unknown",
            "role": role_key,
        }
    if ml in boards:
        return {
            "boards": boards,
            "board_text": board_text,
            "vs_mainline": labels["belong"],
            "match": True,
            "align": "belong",
            "role": role_key,
        }
    soft = any(ml in b or b in ml for b in boards)
    if soft:
        return {
            "boards": boards,
            "board_text": board_text,
            "vs_mainline": labels["near"],
            "match": True,
            "align": "near",
            "role": role_key,
        }
    try:
        from market_desk.mainline import same_theme

        themed = any(same_theme(ml, b) for b in boards)
    except Exception:
        themed = False
    if themed:
        return {
            "boards": boards,
            "board_text": board_text,
            "vs_mainline": labels["theme"],
            "match": True,
            "align": "theme",
            "role": role_key,
        }
    return {
        "boards": boards,
        "board_text": board_text,
        "vs_mainline": labels["off"],
        "match": False,
        "align": "off",
        "role": role_key,
    }


def _infer_source_board(item: dict[str, Any], payload: dict[str, Any] | None = None) -> str:
    """Resolve the origin board for side/link signals (payload or action text)."""
    blob = payload if isinstance(payload, dict) else {}
    sb = str(blob.get("source_board") or item.get("source_board") or "").strip()
    if sb and sb not in ("未明", "—"):
        return sb
    action = str(item.get("action") or "")
    for prefix in ("观察支线·", "板块联动·"):
        if action.startswith(prefix):
            name = action[len(prefix) :].strip()
            if name and name not in ("未明", "—"):
                return name
    return ""


def _desk_source_of(item: dict[str, Any], payload: dict[str, Any] | None = None) -> str:
    """Normalize desk_source from row / payload / signal_type."""
    blob = payload if isinstance(payload, dict) else {}
    src = str(item.get("desk_source") or blob.get("desk_source") or "").strip().lower()
    if src:
        return src
    st = str(item.get("signal_type") or "")
    if st == "buy_side":
        return "side"
    if st == "buy_link":
        return "link"
    if st == "buy_trial":
        return "watch_trial"
    if st == "buy_indep":
        return "independent_pop"
    if st == "buy_dragon":
        return "dragon"
    if st == "sell":
        return "sell"
    if is_buy_signal(st):
        return "main"
    return ""


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
    """Attach board membership and multi-line compare tags.

    Sticky mainline (live/day) remains the global compare target for main and
    watch-trial rows. Side / link rows compare against ``source_board`` so
    expected branch tickets are not painted as「偏离主线」.
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
        sticky = live_ml if live_ml and live_ml not in ("未明", "—") else item.get("mainline")
        sticky_s = str(sticky or "").strip()
        sticky_cmp = compare_boards_to_mainline(names, sticky_s, role="main")
        item["boards"] = sticky_cmp["boards"] or names
        item["board_text"] = sticky_cmp["board_text"] if sticky_cmp["boards"] else (
            " / ".join(names) if names else "未归类"
        )
        item["vs_sticky"] = sticky_cmp["vs_mainline"]
        item["vs_sticky_of"] = sticky_s or None
        item["sticky_match"] = sticky_cmp["match"]

        desk = _desk_source_of(item, payload)
        source_board = _infer_source_board(item, payload)
        item["desk_source"] = desk or item.get("desk_source")
        item["source_board"] = source_board or None

        if desk in ("side", "link") and source_board:
            origin_cmp = compare_boards_to_mainline(names, source_board, role=desk)
            item["vs_mainline"] = origin_cmp["vs_mainline"]
            item["board_match"] = origin_cmp["match"]
            item["vs_mainline_of"] = source_board
            item["vs_compare_role"] = desk
            item["vs_align"] = origin_cmp.get("align")
        else:
            item["vs_mainline"] = sticky_cmp["vs_mainline"]
            item["board_match"] = sticky_cmp["match"]
            item["vs_mainline_of"] = sticky_s or None
            item["vs_compare_role"] = "main"
            item["vs_align"] = sticky_cmp.get("align")
        out.append(item)
    return out


def record_session_signals(snapshot: dict[str, Any]) -> int:
    """Persist buy/sell recommendations for the current session. Return insert/update count.

    Buy sources (all paper cards with a price):
      - recommend → signal_type ``buy`` (含观察回踩全量)
      - side_recommend → ``buy_side``
      - link_recommend → ``buy_link``
      - watch_trial_recommend → ``buy_trial``
      - independent_recommend → ``buy_indep``
      - dragon_recommend → ``buy_dragon``
    Sell: ready items from sell_advice → ``sell``.
    """
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
    try:
        from market_desk.adapt import make_trade_context

        seg_key = str((verdict.get("segment") or {}).get("key") or "")
        trade_ctx = make_trade_context(
            segment_key=seg_key,
            signaled_at=signaled_at,
            metrics=snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else None,
        )
    except Exception:
        trade_ctx = {}
    n = 0

    def _log_buy_items(
        items: list[dict[str, Any]] | None,
        *,
        signal_type: str,
        desk_source: str,
        action_label: str,
        board_fallback: str = "",
    ) -> int:
        """Upsert one buy-family recommend list; return rows written."""
        written = 0
        for item in items or []:
            code = normalize_code(item.get("code"))
            if not code:
                continue
            kind = item.get("kind") or "stock"
            price = num(item.get("buy_price"))
            if price is None:
                price = num(item.get("last"))
            if price is None:
                continue
            board_names = lookup_code_boards(code, board_cards)
            fb = board_fallback or mainline
            if not board_names and fb:
                board_names = [fb]
            sticky_cmp = compare_boards_to_mainline(board_names, mainline, role="main")
            source_board = ""
            if desk_source in ("side", "link"):
                source_board = str(board_fallback or "").strip()
                if source_board in ("未明", "—"):
                    source_board = ""
            # Dragon cards may carry their own board (main / side / link).
            scope_role = str(item.get("dragon_scope") or "").strip()
            if (
                desk_source in ("dragon", "emotion_dragon", "mid_army_dragon")
                or scope_role in ("side", "link", "main")
            ):
                sb = str(item.get("source_board") or "").strip()
                if sb and sb not in ("未明", "—"):
                    source_board = sb
            origin_cmp = None
            if desk_source in ("side", "link") and source_board:
                origin_cmp = compare_boards_to_mainline(
                    board_names, source_board, role=desk_source
                )
            elif scope_role in ("side", "link") and source_board:
                origin_cmp = compare_boards_to_mainline(
                    board_names, source_board, role=scope_role
                )
            upsert_signal(
                {
                    "trade_date": trade_date,
                    "signaled_at": signaled_at,
                    "signal_type": signal_type,
                    "action": action_label,
                    "phase": phase,
                    "mainline": mainline,
                    "code": code,
                    "name": item.get("name") or "",
                    "kind": kind,
                    "price": float(price),
                    "last": num(item.get("last")),
                    "ready": 1 if item.get("ready") else 0,
                    "payload": {
                        "desk_source": desk_source,
                        "source_board": source_board or None,
                        "role_label": item.get("role_label"),
                        "wait_price": item.get("wait_price"),
                        "stop_price": item.get("stop_price"),
                        "chase_price": item.get("chase_price"),
                        "qty": int(item.get("qty") or 0) or None,
                        "pct": item.get("pct"),
                        "trend": item.get("trend"),
                        "trend_quality": item.get("trend_quality"),
                        "trend_pending": bool(item.get("trend_pending")),
                        "trend_unknown": bool(item.get("trend_unknown")),
                        "trend_ok": bool(item.get("trend_ok")),
                        "trend_down": bool(item.get("trend_down")),
                        "confirm_fail": list(item.get("confirm_fail") or []),
                        "confirm_soft": list(item.get("confirm_soft") or []),
                        "minute": item.get("minute") if isinstance(item.get("minute"), dict) else None,
                        "reason": str(item.get("reason") or "")[:240] or None,
                        "block_ready": bool(item.get("block_ready")),
                        "near_entry": bool(item.get("near_entry")),
                        "size_cap_block": bool(item.get("size_cap_block")),
                        "link_board": bool(item.get("link_board")),
                        "watch_trial": bool(item.get("watch_trial")),
                        "dragon_scope": item.get("dragon_scope") or None,
                        "dragon_kind": item.get("dragon_kind") or None,
                        "board_names": sticky_cmp["boards"],
                        # Sticky compare kept for history / whitebox context.
                        "vs_mainline": sticky_cmp["vs_mainline"],
                        "board_match": sticky_cmp["match"],
                        "vs_source": (origin_cmp or {}).get("vs_mainline"),
                        "source_match": (origin_cmp or {}).get("match"),
                        "context": trade_ctx or None,
                    },
                }
            )
            written += 1
        return written

    rec = verdict.get("recommend") or {}
    n += _log_buy_items(
        list(rec.get("items") or []),
        signal_type="buy",
        desk_source="main",
        action_label=action or "观察回踩",
        board_fallback=mainline,
    )

    side_rec = verdict.get("side_recommend") or {}
    side_name = str((verdict.get("side_mainline") or {}).get("name") or "").strip()
    n += _log_buy_items(
        list(side_rec.get("items") or []),
        signal_type="buy_side",
        desk_source="side",
        action_label=f"观察支线·{side_name}" if side_name else "观察支线",
        board_fallback=side_name or mainline,
    )

    link_rec = verdict.get("link_recommend") or {}
    link_name = str((verdict.get("link_mainline") or {}).get("name") or "").strip()
    n += _log_buy_items(
        list(link_rec.get("items") or []),
        signal_type="buy_link",
        desk_source="link",
        action_label=f"板块联动·{link_name}" if link_name else "板块联动",
        board_fallback=link_name or mainline,
    )

    trial_rec = verdict.get("watch_trial_recommend") or {}
    n += _log_buy_items(
        list(trial_rec.get("items") or []),
        signal_type="buy_trial",
        desk_source="watch_trial",
        action_label="自选可试探",
        board_fallback=mainline,
    )

    indep_rec = verdict.get("independent_recommend") or {}
    n += _log_buy_items(
        list(indep_rec.get("items") or []),
        signal_type="buy_indep",
        desk_source="independent_pop",
        action_label="独立人气回踩",
        board_fallback=mainline,
    )

    dragon_rec = verdict.get("dragon_recommend") or {}
    n += _log_buy_items(
        list(dragon_rec.get("items") or []),
        signal_type="buy_dragon",
        desk_source="dragon",
        action_label="龙头排",
        board_fallback=mainline,
    )

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
                    "exit_mode": item.get("exit_mode"),
                    "urgency": item.get("urgency"),
                    "band_mode": item.get("band_mode"),
                    "band_mode_zh": item.get("band_mode_zh"),
                    "daily_trend": item.get("daily_trend"),
                    "board_names": board_cmp["boards"],
                    "vs_mainline": board_cmp["vs_mainline"],
                    "board_match": board_cmp["match"],
                    "context": trade_ctx or None,
                },
            }
        )
        n += 1
    return n


def score_signal_with_closes(
    signal: dict[str, Any],
    closes: list[float],
    dates: list[str] | None = None,
    *,
    opens: list[float | None] | None = None,
    lows: list[float | None] | None = None,
    highs: list[float | None] | None = None,
) -> dict[str, Any] | None:
    """Score a buy/sell signal against subsequent daily bars. Return outcome fields or None.

    Buy labels use next-session **close** vs entry (fill else plan price) — not the
    open. A close that is ≥+1% but stabbed deeply on the day-low (or opened strong
    then faded) is marked ``次日虚红`` / ``次日冲高回落`` and does not count as a hit.
    """
    from market_desk.config import (
        OUTCOME_FAKE_RED_CLOSE_MAX,
        OUTCOME_FAKE_RED_LOW_PCT,
        OUTCOME_FAKE_RED_OPEN_PCT,
    )

    price = num(signal.get("fill_price"))
    if price is None or price <= 0:
        price = num(signal.get("price"))
    if price is None or price <= 0 or not closes:
        return None
    trade_date = str(signal.get("trade_date") or "")
    sig_type = signal.get("signal_type") or "buy"

    # Prefer closes strictly after the signal date when dates are available.
    after: list[float] = []
    after_opens: list[float | None] = []
    after_lows: list[float | None] = []
    after_highs: list[float | None] = []
    if dates and len(dates) == len(closes) and trade_date:
        for i, (d, px) in enumerate(zip(dates, closes)):
            if str(d) > trade_date:
                after.append(float(px))
                if opens and i < len(opens):
                    after_opens.append(opens[i])
                if lows and i < len(lows):
                    after_lows.append(lows[i])
                if highs and i < len(highs):
                    after_highs.append(highs[i])
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
    day1_open = after_opens[0] if after_opens else None
    day1_low = after_lows[0] if after_lows else None
    if is_sell_signal(sig_type):
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
    else:
        d1 = (day1 / price - 1.0) * 100.0
        d3 = (day3 / price - 1.0) * 100.0
        mfe = (peak / price - 1.0) * 100.0
        mae = (trough / price - 1.0) * 100.0
        # Prefer intraday low for MAE when available (T+1 path you could feel).
        if day1_low is not None and float(day1_low) > 0:
            try:
                day_mae = (float(day1_low) / price - 1.0) * 100.0
                mae = min(mae, day_mae)
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        if d1 >= 1.0:
            label = "次日红"
            if day1_low is not None and float(day1_low) > 0:
                try:
                    low_pct = (float(day1_low) / price - 1.0) * 100.0
                    if low_pct <= float(OUTCOME_FAKE_RED_LOW_PCT):
                        label = "次日虚红"
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        elif (
            day1_open is not None
            and float(day1_open) > 0
            and d1 < float(OUTCOME_FAKE_RED_CLOSE_MAX)
        ):
            try:
                open_pct = (float(day1_open) / price - 1.0) * 100.0
                if open_pct >= float(OUTCOME_FAKE_RED_OPEN_PCT):
                    label = "次日冲高回落"
                elif d1 <= -1.5:
                    label = "次日绿"
                elif d3 >= 2.0:
                    label = "三日红"
                elif d3 <= -2.0:
                    label = "三日绿"
                else:
                    label = "平淡"
            except (TypeError, ValueError, ZeroDivisionError):
                if d1 <= -1.5:
                    label = "次日绿"
                elif d3 >= 2.0:
                    label = "三日红"
                elif d3 <= -2.0:
                    label = "三日绿"
                else:
                    label = "平淡"
        elif d1 <= -1.5:
            label = "次日绿"
        elif d3 >= 2.0:
            label = "三日红"
        elif d3 <= -2.0:
            label = "三日绿"
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


# Labels that count as a buy "hit" for rate / soft feedback.
BUY_HIT_LABELS = frozenset({"次日红", "三日红"})


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
    buys = [r for r in active if is_buy_signal(r.get("signal_type"))]
    sells = [r for r in active if is_sell_signal(r.get("signal_type"))]
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
        "buy_hit_rate": _rate(scored_buys, BUY_HIT_LABELS),
        "buy_hit_rate_all": _rate(scored_buys_all, BUY_HIT_LABELS),
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


def classify_fill_execution(row: dict[str, Any]) -> str | None:
    """Classify a traded buy fill versus the original suggest / chase band."""
    if not is_buy_signal(row.get("signal_type")):
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
        if is_buy_signal(r.get("signal_type")) and int(r.get("traded") or 0)
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
    from market_desk.adapt import context_from_row

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
        ctx = context_from_row(row)
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
                "context": ctx,
                "context_label": ctx.get("label"),
            }
        )
    return out


def build_desk_source_hit_rates(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate buy hit-rate by desk source (main / side / link / watch_trial)."""
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    label_map = {
        "main": "主线",
        "side": "支线",
        "link": "联动",
        "watch_trial": "自选试探",
        "independent_pop": "独立人气",
        "dragon": "龙头排",
    }
    type_to_src = {
        "buy": "main",
        "buy_side": "side",
        "buy_link": "link",
        "buy_trial": "watch_trial",
        "buy_indep": "independent_pop",
        "buy_dragon": "dragon",
    }
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in label_map}
    for row in rows:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        src = str(row.get("desk_source") or "").strip()
        if not src:
            src = type_to_src.get(str(row.get("signal_type") or ""), "main")
        if src in ("emotion_dragon", "mid_army_dragon"):
            src = "dragon"
        if src not in buckets:
            src = "main"
        buckets[src].append(row)
    out: list[dict[str, Any]] = []
    for key in ("main", "dragon", "side", "link", "watch_trial", "independent_pop"):
        items = buckets[key]
        if not items:
            continue
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
        out.append(
            {
                "source": key,
                "label": label_map[key],
                "scored_n": len(items),
                "hit_n": hit,
                "hit_rate": round(100.0 * hit / len(items), 1),
            }
        )
    return out


def build_theme_hit_rates(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Aggregate buy hit-rate by signal mainline / theme label."""
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        theme = str(row.get("mainline") or "").strip() or "未标主线"
        buckets.setdefault(theme, []).append(row)
    out: list[dict[str, Any]] = []
    for theme, items in sorted(buckets.items(), key=lambda x: (-len(x[1]), x[0])):
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
        out.append(
            {
                "theme": theme,
                "label": theme,
                "scored_n": len(items),
                "hit_n": hit,
                "hit_rate": round(100.0 * hit / len(items), 1),
            }
        )
        if len(out) >= max(3, int(limit or 8)):
            break
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
        if not is_buy_signal(row.get("signal_type")):
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
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
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
        if not is_buy_signal(row.get("signal_type")):
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
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
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
        if not is_buy_signal(row.get("signal_type")):
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
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
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
    gate_kills: list[dict[str, Any]] | None = None,
    sell_bias: dict[str, Any] | None = None,
    current_context: dict[str, Any] | None = None,
) -> list[str]:
    """Return short threshold-tuning hints from review buckets (not orders)."""
    from market_desk.adapt import count_missed_by_context
    from market_desk.config import ADAPT_MISSED_MIN_N, ADAPT_TUNE_CLAMP

    hints: list[str] = []
    missed_n = len(missed or [])
    by_ctx = count_missed_by_context(missed)
    clamp_pct = int(float(ADAPT_TUNE_CLAMP) * 100)
    ctx = current_context or {}
    if ctx.get("tagged") and ctx.get("key"):
        bucket_n = int(by_ctx.get(str(ctx["key"])) or 0)
        if bucket_n >= int(ADAPT_MISSED_MIN_N):
            hints.append(
                f"{ctx.get('label')}漏买 {bucket_n} 笔：auto_tune 略放宽回踩"
                f"（共用夹紧±{clamp_pct}%·不串桶）"
            )
        elif missed_n >= 3:
            # Show other buckets as info only.
            top = sorted(by_ctx.items(), key=lambda kv: -kv[1])[:2]
            extra = "、".join(f"{k}×{v}" for k, v in top) if top else "他桶不足"
            hints.append(
                f"漏买合计 {missed_n}，当前桶 {ctx.get('label')} 仅 {bucket_n}："
                f"不调参（{extra}）"
            )
    elif missed_n >= 3:
        hints.append(
            f"漏买 {missed_n} 笔缺情景标签：只提示不调参"
            f"（需时段×波动；夹紧±{clamp_pct}%）"
        )
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
    for row in (gate_kills or [])[:3]:
        fk = int(row.get("false_kill_n") or 0)
        kn = int(row.get("kill_n") or 0)
        gate = row.get("gate") or ""
        if fk >= 3 and kn >= 5:
            hints.append(
                f"闸门「{gate}」误杀偏多（假杀 {fk}/{kn}）：已自动略放宽该确认门槛"
                f"（夹紧±{clamp_pct}%）"
            )
    sb = sell_bias or {}
    if sb.get("widen") and int(sb.get("n") or 0) >= 6:
        hints.append(
            f"卖点偏早（卖后回落命中 {sb.get('hit_rate')}%，n={sb.get('n')}）："
            f"已自动放宽回撤/落袋阈值"
        )
    elif sb.get("tighten") and int(sb.get("n") or 0) >= 6:
        hints.append(
            f"卖点偏准（卖后回落命中 {sb.get('hit_rate')}%，n={sb.get('n')}）："
            f"已略收紧止盈回撤"
        )
    return hints[:6]


def _gate_bucket(flag: str) -> str:
    """Map a confirm_fail string to a stable attribution bucket."""
    text = str(flag or "").strip()
    if not text:
        return "其它"
    if text.startswith("日线") or "日线" in text:
        return "日线"
    if text.startswith("分时") or "分时" in text:
        return "分时"
    if "薄确认" in text or "跨板块" in text:
        return "薄确认/共振"
    if "总仓" in text or "相位上限" in text:
        return "总仓上限"
    if "离日高" in text:
        return "离日高"
    if "弱于" in text:
        return "相对强弱"
    if "量能" in text:
        return "ETF量能"
    if "指数" in text:
        return "指数弱"
    return text[:12]


def build_gate_kill_stats(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Attribute ready kills to confirm_fail history; flag false kills via outcomes.

    Kill counts use confirm_fail_hist (same-day gate evolution). False/true kill
    only when the final same-day state stayed ready=0.
    """
    del hit_mode  # Paper outcomes OK for gated cards; final ready state still required.
    kills: dict[str, int] = {}
    false_kills: dict[str, int] = {}
    true_kills: dict[str, int] = {}
    for row in rows or []:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        fails = [
            str(x)
            for x in (
                payload.get("confirm_fail_hist")
                or payload.get("final_fail")
                or payload.get("confirm_fail")
                or []
            )
            if str(x).strip()
        ]
        if not fails:
            continue
        label = str(row.get("outcome_label") or "")
        ready_now = int(row.get("ready") or 0)
        buckets = {_gate_bucket(f) for f in fails}
        for bucket in buckets:
            kills[bucket] = kills.get(bucket, 0) + 1
            if ready_now or not label:
                continue
            if label in BUY_HIT_LABELS:
                false_kills[bucket] = false_kills.get(bucket, 0) + 1
            elif label in {"次日绿", "三日绿"}:
                true_kills[bucket] = true_kills.get(bucket, 0) + 1
    out: list[dict[str, Any]] = []
    for gate, kn in sorted(kills.items(), key=lambda x: (-x[1], x[0])):
        fk = int(false_kills.get(gate) or 0)
        tk = int(true_kills.get(gate) or 0)
        scored_n = fk + tk
        false_rate = round(100.0 * fk / scored_n, 1) if scored_n else None
        out.append(
            {
                "gate": gate,
                "kill_n": kn,
                "false_kill_n": fk,
                "true_kill_n": tk,
                "false_kill_rate": false_rate,
                "note": (
                    f"误杀偏多" if fk >= 3 and (false_rate or 0) >= 50 else
                    ("挡得住" if tk >= 3 and fk == 0 else "")
                ),
            }
        )
    return out[:8]


def build_sell_review_bias(
    rows: list[dict[str, Any]] | None = None,
    *,
    hit_mode: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Derive sell-band widen/tighten from historical sell outcomes.

    ``kind`` = etf | stock filters the sample. Low 卖后回落 hit-rate → widen;
    high hit-rate → slightly tighten take-profit pullback.
    """
    from market_desk.config import (
        SELL_REVIEW_KIND_MIN_N,
        SELL_REVIEW_MIN_N,
        SELL_REVIEW_TIGHTEN_ABOVE,
        SELL_REVIEW_TIGHTEN_MULT,
        SELL_REVIEW_WIDEN_BELOW,
        SELL_REVIEW_WIDEN_MULT,
    )

    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    kind_f = str(kind or "").strip().lower() or None
    try:
        src = rows if rows is not None else load_signals(limit=240)
    except Exception:
        src = []
    sells = [
        r for r in src
        if str(r.get("signal_type") or "") == "sell"
        and not int(r.get("skipped") or 0)
        and r.get("outcome_label")
    ]
    if mode == "traded":
        sells = [r for r in sells if int(r.get("traded") or 0)]
    if kind_f in ("etf", "stock"):
        sells = [r for r in sells if str(r.get("kind") or "stock") == kind_f]
    n = len(sells)
    label = {"etf": "ETF", "stock": "个股"}.get(kind_f or "", "合计")
    min_n = int(SELL_REVIEW_KIND_MIN_N) if kind_f in ("etf", "stock") else int(SELL_REVIEW_MIN_N)
    if n < min_n:
        return {
            "ok": False,
            "kind": kind_f,
            "n": n,
            "hit_rate": None,
            "widen": False,
            "tighten": False,
            "mult": 1.0,
            "note": f"{label}卖点样本不足（n={n}，需≥{min_n}）",
        }
    hit = sum(1 for r in sells if (r.get("outcome_label") or "") == "卖后回落")
    early = sum(1 for r in sells if (r.get("outcome_label") or "") == "卖后继续涨")
    rate = round(100.0 * hit / n, 1)
    widen = rate < float(SELL_REVIEW_WIDEN_BELOW)
    tighten = rate >= float(SELL_REVIEW_TIGHTEN_ABOVE)
    mult = 1.0
    note = f"{label}卖后回落命中 {rate}%（n={n}，继续涨 {early}）"
    if widen:
        mult = float(SELL_REVIEW_WIDEN_MULT)
        note += "·偏早→放宽回撤/落袋"
    elif tighten:
        mult = float(SELL_REVIEW_TIGHTEN_MULT)
        note += "·偏准→略收紧止盈回撤"
    return {
        "ok": True,
        "kind": kind_f,
        "n": n,
        "hit_rate": rate,
        "early_n": early,
        "hit_n": hit,
        "widen": widen,
        "tighten": tighten,
        "mult": mult,
        "note": note,
    }


def resolve_sell_kind_bias(
    bundle: dict[str, Any] | None,
    *,
    etf: bool,
) -> dict[str, Any]:
    """Pick etf/stock sell bias with conflict damping vs the all-sample row."""
    box = bundle or {}
    kind = dict((box.get("etf") if etf else box.get("stock")) or {})
    overall = dict(box.get("all") or {})
    if kind.get("ok"):
        if overall.get("ok"):
            if bool(kind.get("widen")) and bool(overall.get("tighten")):
                kind = {
                    **kind,
                    "mult": 1.0,
                    "widen": False,
                    "tighten": False,
                    "note": (kind.get("note") or "") + "·与合计冲突取中",
                }
            elif bool(kind.get("tighten")) and bool(overall.get("widen")):
                kind = {
                    **kind,
                    "mult": 1.0,
                    "widen": False,
                    "tighten": False,
                    "note": (kind.get("note") or "") + "·与合计冲突取中",
                }
        return kind
    if overall.get("ok"):
        out = dict(overall)
        note = str(out.get("note") or "")
        tag = "·分品种不足用合计"
        if tag not in note:
            out["note"] = note + tag if note else "分品种不足用合计"
        return out
    return kind or overall or {"ok": False, "mult": 1.0, "widen": False, "tighten": False}


def build_sell_review_bias_bundle(
    rows: list[dict[str, Any]] | None = None,
    *,
    hit_mode: str | None = None,
) -> dict[str, Any]:
    """Return all / etf / stock sell biases for review UI and exit bands."""
    try:
        src = rows if rows is not None else load_signals(limit=240)
    except Exception:
        src = []
    overall = build_sell_review_bias(src, hit_mode=hit_mode, kind=None)
    etf = build_sell_review_bias(src, hit_mode=hit_mode, kind="etf")
    stock = build_sell_review_bias(src, hit_mode=hit_mode, kind="stock")
    return {
        "all": overall,
        "etf": etf,
        "stock": stock,
        "note": " · ".join(
            n for n in (etf.get("note"), stock.get("note"), overall.get("note")) if n
        ),
        "hit_rate": overall.get("hit_rate"),
        "n": overall.get("n"),
        "widen": overall.get("widen"),
        "tighten": overall.get("tighten"),
    }


_REVIEW_BIAS_CACHE: dict[str, Any] = {"day": "", "sell": None, "buy_gate": None}


def cached_sell_bias_bundle() -> dict[str, Any]:
    """Day-scoped cache for sell-review bias (avoid per-refresh full scans)."""
    day = datetime.now().strftime("%Y-%m-%d")
    if _REVIEW_BIAS_CACHE.get("day") != day or _REVIEW_BIAS_CACHE.get("sell") is None:
        _REVIEW_BIAS_CACHE["day"] = day
        try:
            _REVIEW_BIAS_CACHE["sell"] = build_sell_review_bias_bundle()
        except Exception:
            _REVIEW_BIAS_CACHE["sell"] = {
                "all": {"ok": False, "mult": 1.0, "widen": False, "tighten": False},
                "etf": {"ok": False, "mult": 1.0, "widen": False, "tighten": False},
                "stock": {"ok": False, "mult": 1.0, "widen": False, "tighten": False},
                "note": "卖点闭环暂不可用",
            }
        _REVIEW_BIAS_CACHE["buy_gate"] = None
    if _REVIEW_BIAS_CACHE.get("buy_gate") is None:
        try:
            _REVIEW_BIAS_CACHE["buy_gate"] = build_buy_gate_bias()
        except Exception:
            _REVIEW_BIAS_CACHE["buy_gate"] = {
                "ok": False,
                "mult": 1.0,
                "loosen": False,
                "gates": {},
                "note": "买侧闸门闭环暂不可用",
            }
    return dict(_REVIEW_BIAS_CACHE["sell"] or {})

def cached_buy_gate_bias() -> dict[str, Any]:
    """Day-scoped buy-gate loosen multipliers from false-kill stats."""
    cached_sell_bias_bundle()  # ensure day cache warm
    return dict(_REVIEW_BIAS_CACHE.get("buy_gate") or {"ok": False, "mult": 1.0, "loosen": False, "gates": {}})


def build_buy_gate_bias() -> dict[str, Any]:
    """Tune tip / minute / thin-confirm thresholds from review kill stats.

    False-kills loosen (mult < 1); true-kills tighten (mult > 1). Returns
    per-gate multipliers and a global mult for shared knobs.
    """
    from market_desk.config import (
        BUY_GATE_FALSE_KILL_MIN,
        BUY_GATE_KILL_MIN,
        BUY_GATE_LOOSEN_MULT,
        BUY_GATE_TIGHTEN_MULT,
        BUY_GATE_TRUE_KILL_MIN,
    )
    from market_desk.db import load_signals

    kills = build_gate_kill_stats(load_signals(limit=240))
    gates: dict[str, dict[str, Any]] = {}
    loosen_any = False
    tighten_any = False
    mult = 1.0
    note_parts: list[str] = []
    target = {
        "分时": "minute",
        "离日高": "off_high",
        "薄确认/共振": "thin",
        "相对强弱": "rel_strength",
    }
    for row in kills or []:
        gate = str(row.get("gate") or "")
        key = target.get(gate)
        if not key:
            continue
        fk = int(row.get("false_kill_n") or 0)
        tk = int(row.get("true_kill_n") or 0)
        kn = int(row.get("kill_n") or 0)
        if fk >= int(BUY_GATE_FALSE_KILL_MIN) and kn >= int(BUY_GATE_KILL_MIN):
            g_mult = float(BUY_GATE_LOOSEN_MULT)
            gates[key] = {
                "gate": gate,
                "mult": g_mult,
                "false_kill_n": fk,
                "true_kill_n": tk,
                "kill_n": kn,
                "mode": "loosen",
            }
            loosen_any = True
            mult = min(mult, g_mult)
            note_parts.append(f"{gate}假杀{fk}/{kn}→×{g_mult}")
        elif tk >= int(BUY_GATE_TRUE_KILL_MIN) and kn >= int(BUY_GATE_KILL_MIN) and fk <= max(1, tk // 3):
            g_mult = float(BUY_GATE_TIGHTEN_MULT)
            gates[key] = {
                "gate": gate,
                "mult": g_mult,
                "false_kill_n": fk,
                "true_kill_n": tk,
                "kill_n": kn,
                "mode": "tighten",
            }
            tighten_any = True
            mult = max(mult, g_mult)
            note_parts.append(f"{gate}真杀{tk}/{kn}→×{g_mult}")
    return {
        "ok": True,
        "loosen": loosen_any,
        "tighten": tighten_any,
        "mult": mult,
        "gates": gates,
        "note": "；".join(note_parts) if note_parts else "买侧闸门暂不调参",
    }

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
        if not is_buy_signal(row.get("signal_type")):
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
    traded_codes: set[str] = set()
    open_codes = {
        normalize_code(r.get("code"))
        for r in (snapshot.get("positions") or [])
        if isinstance(r, dict) and int(r.get("qty") or 0) > 0
    }
    # Already bought → entry / chase bands are noise; keep stop only.
    # Multi-user: do not trust shared signals.traded; owned = open positions only.
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
        # Buying caution for same-day signals.
        if is_buy_signal(item.get("signal_type")):
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
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        boards = payload.get("board_names") or []
        if isinstance(boards, list) and boards:
            item["boards"] = [str(b) for b in boards if str(b).strip()]
            item["board_text"] = "/".join(item["boards"])
        else:
            item["boards"] = []
            item["board_text"] = ""
        # Compact row for the drawer (drop bulky payload).
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

    ``user_id`` overlays per-user traded / fill annotations. Shared digest
    history is saved without personal fills so users do not pollute each other.
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

    global_rows = [_flatten_signal_prices(r) for r in load_signals(limit=limit)]
    day_rows = [_flatten_signal_prices(r) for r in load_signals_for_date(day)]
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
    summary["phase_hits"] = build_phase_hit_rates(global_rows)
    summary["kind_hits"] = build_kind_hit_rates(global_rows)
    summary["phase_kind_hits"] = build_phase_kind_hit_rates(global_rows)
    summary["desk_hits"] = build_desk_source_hit_rates(global_rows)
    summary["theme_hits"] = build_theme_hit_rates(global_rows)
    summary["missed_buys"] = build_missed_buys(day_rows, trade_date=day)
    summary["gate_kills"] = build_gate_kill_stats(global_rows)
    summary["sell_bias"] = build_sell_review_bias_bundle(global_rows)
    summary["tune_hints"] = build_tune_hints(
        missed=summary["missed_buys"],
        phase_hits=summary["phase_hits"],
        kind_hits=summary["kind_hits"],
        gate_kills=summary["gate_kills"],
        sell_bias=summary["sell_bias"].get("all") or summary["sell_bias"],
        current_context=None,
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
    if item.get("plan_qty") is None:
        pq = num(payload.get("qty"))
        if pq is not None and float(pq) > 0:
            item["plan_qty"] = int(pq)
    if not item.get("desk_source"):
        item["desk_source"] = payload.get("desk_source") or (
            "main"
            if is_buy_signal(item.get("signal_type"))
            else ("sell" if is_sell_signal(item.get("signal_type")) else None)
        )
    if not item.get("source_board"):
        sb = str(payload.get("source_board") or "").strip()
        if not sb:
            sb = _infer_source_board(item, payload)
        item["source_board"] = sb or None
    if item.get("near_entry") is None and "near_entry" in payload:
        item["near_entry"] = bool(payload.get("near_entry"))
    if not item.get("confirm_fail") and payload.get("confirm_fail"):
        item["confirm_fail"] = list(payload.get("confirm_fail") or [])
    if not item.get("confirm_soft") and payload.get("confirm_soft"):
        item["confirm_soft"] = list(payload.get("confirm_soft") or [])
    if not item.get("reason") and payload.get("reason"):
        item["reason"] = str(payload.get("reason") or "")[:240] or None
    if not item.get("role_label") and payload.get("role_label"):
        item["role_label"] = payload.get("role_label")
    if not isinstance(item.get("minute"), dict) and isinstance(payload.get("minute"), dict):
        item["minute"] = payload.get("minute")
    return item


def apply_outcomes(
    rows: list[dict[str, Any]],
    closes_map: dict[str, tuple[list[str], list[float]]],
    *,
    overwrite: bool = False,
) -> int:
    """Write scored outcomes for signals that have forward closes. Return update count.

    When ``overwrite`` is True, re-score rows that already have a label (used by
    formula-version migrations such as OHLC fake-red labels).
    """
    n = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for row in rows:
        if row.get("outcome_label") and not overwrite:
            continue
        if str(row.get("trade_date") or "") >= today:
            continue
        code = normalize_code(row.get("code"))
        packed = closes_map.get(code)
        if not packed:
            continue
        if len(packed) >= 3:
            dates, closes, ohlc = packed[0], packed[1], packed[2] or {}
        else:
            dates, closes = packed[0], packed[1]
            ohlc = {}
        outcome = score_signal_with_closes(
            row,
            closes,
            dates,
            opens=list(ohlc.get("open") or []) or None,
            lows=list(ohlc.get("low") or []) or None,
            highs=list(ohlc.get("high") or []) or None,
        )
        if not outcome:
            continue
        if mark_signal_outcome(int(row["id"]), outcome):
            n += 1
    return n
