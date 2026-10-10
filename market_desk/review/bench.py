"""Buy-card returns relative to a broad index over the same 3-session window.

A card's stored ``outcome_day3_pct`` runs from the plan / fill price on day D to
the close of the third later session. The benchmark leg is the index close(D) →
close(D+k) with the same k settled sessions, so excess = card − index. The
intraday leg of day D (plan → close) has no index counterpart; it is small next
to the 3-day move and the same for every card of that day.
"""

from __future__ import annotations

from typing import Any

from market_desk.filters import normalize_code
from market_desk.numbers import num
from market_desk.review.outcome import OUTCOME_HORIZON_SESSIONS, forward_session_ready
from market_desk.review.signals import is_buy_signal
from market_desk.stats_tools import day_means, safe_t


def bench_window_pct(
    closes: list[tuple[str, float]],
    trade_date: str,
    sessions: int = OUTCOME_HORIZON_SESSIONS,
) -> tuple[float | None, int]:
    """Return the index % from close(trade_date) over up to ``sessions`` settled sessions.

    Args:
        closes: ``[(date, close)]`` oldest first.
        trade_date: Signal day D.
        sessions: Horizon cap (3 for the stored 三日%).

    Returns:
        ``(pct rounded to 2 dp, sessions used)``; ``(None, 0)`` when D is missing
        or no later session has settled.
    """
    day = str(trade_date or "")[:10]
    by_day = {str(d)[:10]: float(c) for d, c in closes if c}
    base = by_day.get(day)
    if not base:
        return None, 0
    after = [d for d in sorted(by_day) if d > day and forward_session_ready(d)][: int(sessions)]
    if not after:
        return None, 0
    return round((by_day[after[-1]] / base - 1.0) * 100.0, 2), len(after)


def attach_bench_excess(
    rows: list[dict[str, Any]],
    closes: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    """Return row copies with ``bench_d3_pct`` / ``excess_d3_pct`` on scored buys.

    ``bench_sessions`` < 3 marks a horizon still in progress (the card's 三日% is
    partial too). Sells and unscored rows pass through unchanged.
    """
    memo: dict[str, tuple[float | None, int]] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        d3 = num(item.get("outcome_day3_pct"))
        if d3 is not None and is_buy_signal(item.get("signal_type")):
            day = str(item.get("trade_date") or "")[:10]
            if day not in memo:
                memo[day] = bench_window_pct(closes, day)
            pct, used = memo[day]
            if pct is not None:
                item["bench_d3_pct"] = pct
                item["excess_d3_pct"] = round(float(d3) - pct, 2)
                item["bench_sessions"] = used
        out.append(item)
    return out


def build_bench_excess_summary(
    rows: list[dict[str, Any]],
    closes: list[tuple[str, float]],
    *,
    label: str,
    max_days: int,
    view_day: str | None = None,
) -> dict[str, Any]:
    """Summarize buy-card excess over the index, one sample per trading day.

    Cards are deduped per (day, code) and skipped cards dropped; only full
    3-session horizons count toward the multi-day line.

    Args:
        rows: Signal rows with stored outcomes (any order).
        closes: Index ``[(date, close)]`` oldest first.
        label: Index display name.
        max_days: Most recent settled trading days to include.
        view_day: Day whose own line (n, card mean, index, excess) is added.

    Returns:
        ``ok``, ``label``, ``days``, ``pos_days``, ``mean_excess`` (mean of daily
        means), ``t`` (``safe_t`` over daily means), ``n``, ``mean_card``,
        ``mean_bench``, ``traded_n`` / ``traded_excess``, ``day`` and ``note``.
    """
    seen: set[tuple[str, str]] = set()
    cards: list[dict[str, Any]] = []
    for r in attach_bench_excess(rows, closes):
        if r.get("excess_d3_pct") is None or int(r.get("skipped") or 0):
            continue
        key = (str(r.get("trade_date") or "")[:10], normalize_code(r.get("code")))
        if key in seen:
            continue
        seen.add(key)
        cards.append(r)

    def _day_line(day: str) -> dict[str, Any] | None:
        items = [c for c in cards if str(c.get("trade_date") or "")[:10] == day]
        if not items:
            return None
        n = len(items)
        return {
            "date": day,
            "n": n,
            "mean_card": round(sum(float(c["outcome_day3_pct"]) for c in items) / n, 2),
            "bench": items[0]["bench_d3_pct"],
            "excess": round(sum(float(c["excess_d3_pct"]) for c in items) / n, 2),
            "partial": int(items[0].get("bench_sessions") or 0) < OUTCOME_HORIZON_SESSIONS,
        }

    full = [c for c in cards if int(c.get("bench_sessions") or 0) >= OUTCOME_HORIZON_SESSIONS]
    keep_days = sorted({str(c.get("trade_date") or "")[:10] for c in full})[-int(max_days):]
    keep = set(keep_days)
    full = [c for c in full if str(c.get("trade_date") or "")[:10] in keep]
    out: dict[str, Any] = {
        "ok": bool(full),
        "label": label,
        "day": _day_line(str(view_day)[:10]) if view_day else None,
    }
    if not full:
        out["note"] = f"还没有满三个交易日的买卡，或{label}日线暂缺"
        return out
    daily = [m for _, m in day_means(full, lambda c: str(c.get("trade_date"))[:10], lambda c: c["excess_d3_pct"])]
    tv = safe_t(daily)
    traded = [c for c in full if int(c.get("traded") or 0)]
    n = len(full)
    out.update(
        {
            "days": len(daily),
            "pos_days": sum(1 for m in daily if m > 0),
            "mean_excess": round(sum(daily) / len(daily), 2),
            "t": None if tv is None else round(tv, 1),
            "n": n,
            "mean_card": round(sum(float(c["outcome_day3_pct"]) for c in full) / n, 2),
            "mean_bench": round(sum(float(c["bench_d3_pct"]) for c in full) / n, 2),
            "traded_n": len(traded),
            "traded_excess": (
                round(sum(float(c["excess_d3_pct"]) for c in traded) / len(traded), 2) if traded else None
            ),
            "since": keep_days[0],
            "note": (
                f"近 {len(daily)} 个满三日的交易日，买卡同日同代码一次；超额 = 三日% − {label} 同窗口；"
                "按天平均（同日卡共享大盘），t 取普通与 Newey–West 较小者"
            ),
        }
    )
    return out
