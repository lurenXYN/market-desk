"""Signal kinds, live ticks, board lookup, and signal enrichment."""

from __future__ import annotations

import re
import time
from datetime import timedelta, timezone
from typing import Any
from market_desk.db import (
    delete_signal,
    load_mainline_switches,
    load_session_segments,
    load_signals,
    load_signals_for_date,
)
from market_desk.filters import is_limit_up, normalize_code
from market_desk.numbers import num

try:
    from zoneinfo import ZoneInfo

    _CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    _CN_TZ = timezone(timedelta(hours=8))


# In-memory last-price ticks for short-horizon ↑/↓ on the review panel.
_PRICE_TICKS: dict[str, list[tuple[float, float]]] = {}


_LIVE_LOOKBACK_SEC = 60.0


_LIVE_KEEP_SEC = 240.0


_LIVE_FLAT_PCT = 0.08  # treat |Δ| below this as flat


# Buy-family signal_type values (UNIQUE key includes type so sources coexist).
BUY_SIGNAL_TYPES = frozenset(
    {"buy", "buy_side", "buy_link", "buy_trial", "buy_indep", "buy_dragon"}
)


def _dragon_hide_from_review(row: dict[str, Any]) -> bool:
    """Return True when a dragon signal was not actionable (limit-up / sealed observe).

    Such rows are omitted from review logging and the review table—they do not
    represent a missed buy, only same-day「买不进」观察。
    """
    st = str(row.get("signal_type") or "")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    desk = str(row.get("desk_source") or payload.get("desk_source") or "").strip().lower()
    if st != "buy_dragon" and desk not in (
        "dragon",
        "emotion_dragon",
        "mid_army_dragon",
    ):
        return False
    name = str(row.get("name") or payload.get("name") or "")
    code = row.get("code") or payload.get("code")
    pct = num(row.get("pct"))
    if pct is None:
        pct = num(payload.get("pct"))
    if is_limit_up(name, pct, code):
        return True
    reason = str(row.get("reason") or payload.get("reason") or "")
    if "已封板" in reason:
        return True
    return False


def purge_unactionable_dragon_signals(trade_date: str | None = None) -> int:
    """Hard-delete sealed / limit-up dragon rows that should not count as missed buys.

    Prefer calling with a trade_date so only that day's junk is cleaned. When
    omitted, scans a short recent window from ``load_signals``.
    """
    if trade_date:
        raw = load_signals_for_date(str(trade_date).strip()[:10])
    else:
        raw = load_signals(limit=400)
    n = 0
    for row in raw:
        if not _dragon_hide_from_review(row):
            continue
        sid = row.get("id")
        if sid is None:
            continue
        try:
            if delete_signal(int(sid)):
                n += 1
        except Exception:
            continue
    return n


def is_buy_signal(sig_type: Any) -> bool:
    """Return True for main / side / link / trial buy signal types."""
    t = str(sig_type or "").strip().lower()
    return t in BUY_SIGNAL_TYPES or (t.startswith("buy_") and t != "buy_sell")


def is_sell_signal(sig_type: Any) -> bool:
    """Return True for sell review signals."""
    return str(sig_type or "").strip().lower() == "sell"


# Ordered (keyword, rule) pairs; first hit wins, so specific labels precede generic ones.
_SELL_RULE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("跳空破保本", "跳空破保本"),
    ("保本防守", "保本防守"),
    ("回落防守", "保本防守"),
    ("板块塌陷", "板块塌陷"),
    ("站上MA20", "止损带减半"),
    ("止损", "止损清仓"),
    ("昨买今弱", "昨买今弱"),
    ("载体走弱", "载体走弱"),
    ("退潮兑现", "退潮兑现"),
    ("衰退", "衰退防守"),
    ("冲高回落", "冲高回落"),
    ("落袋", "落袋止盈"),
    ("换防", "换防护栏"),
)


def sell_rule_of(label: Any) -> str:
    """Map a sell ``role_label`` (possibly prefixed by open-buffer tips) to its exit rule.

    Args:
        label: Stored sell ``action`` / ``role_label`` text.

    Returns:
        Canonical rule name used to bucket sell outcomes, or ``其他`` when no
        keyword matches.
    """
    # Open-buffer prefixes such as 「开盘必卖（止损/清仓…）」 carry rule words in parentheses.
    text = re.sub(r"[（(][^（()）]*[)）]", "", str(label or ""))
    for key, rule in _SELL_RULE_KEYWORDS:
        if key in text:
            return rule
    return "其他"


def sell_atr_bucket(row: dict[str, Any]) -> str:
    """Return the volatility band a sell was judged under (``高波动`` / ``低波动`` / ``标准``)."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    mode = str(payload.get("atr_band_mode") or "")
    if not mode:
        zh = str(payload.get("band_mode_zh") or "")
        mode = "high_beta" if "高波动" in zh else "low_beta" if "低波动" in zh else "normal"
    return {"high_beta": "高波动", "low_beta": "低波动"}.get(mode, "标准")


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


PRE_MATCH_END_HHMM = "09:25"


def is_pre_match_stamp(stamp: str | None) -> bool:
    """True when ``YYYY-MM-DD HH:MM[:SS]`` falls before the 09:25 auction match.

    Nothing can be filled before the call auction matches, so buy/sell signals
    stamped earlier are not recorded.
    """
    s = str(stamp or "")
    if len(s) < 16:
        return False
    return s[11:16] < PRE_MATCH_END_HHMM


def board_stage_lookup(snapshot: dict[str, Any] | None) -> dict[str, str]:
    """Map board name → lifecycle stage key from the snapshot's frozen lifecycle."""
    snap = snapshot or {}
    life = snap.get("mainline_lifecycle") if isinstance(snap.get("mainline_lifecycle"), dict) else {}
    out: dict[str, str] = {}
    for col in ("starting", "ongoing", "ending"):
        for row in life.get(col) or []:
            name = str((row or {}).get("name") or "").strip()
            if name:
                out.setdefault(name, col)
    stage_map = life.get("stage_map") if isinstance(life.get("stage_map"), dict) else {}
    if stage_map:
        for card in list(snap.get("hot_boards") or []) + list(snap.get("pin_boards") or []):
            name = str((card or {}).get("name") or "").strip()
            stage = stage_map.get(str((card or {}).get("bk") or ""))
            if name and stage:
                out.setdefault(name, str(stage))
    return out
