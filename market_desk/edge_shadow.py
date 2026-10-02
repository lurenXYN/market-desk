"""Edge shadow ledger: three entry rules tracked out of sample (display only).

- reclaim: after a card touches its plan price, buy only once it reclaims the
  minute average (priced at that print) instead of buying on touch at plan;
  cards that never reclaim count as skipped (zero return);
- breaker: stop lighting new cards once the cards that lit earlier today are
  underwater on average (judged at several levels from the frozen day book);
- reweight: demote dragon cards, daily uptrends and 13:00-14:00 cards, promote
  daily downtrends and 10:00-11:30 cards.

All three come from the September 2026 replay, so only trade dates on or after
``EDGE_SHADOW_SINCE`` count as evidence. The reweight section also reports the
discovery window, which is in-sample by construction. Nothing here feeds back
into live cards.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from market_desk.numbers import num

_BUY_TYPES = frozenset({"buy", "buy_side", "buy_link", "buy_trial", "buy_indep", "buy_dragon"})


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row.get("payload")
    return p if isinstance(p, dict) else {}


def _lit(row: dict[str, Any]) -> bool:
    return bool(_payload(row).get("ever_ready") or row.get("ready"))


def _day(row: dict[str, Any]) -> str:
    return str(row.get("trade_date") or "")[:10]


def _d3(row: dict[str, Any]) -> float:
    return float(num(row.get("outcome_day3_pct")) or 0.0)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _r2(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _win(values: list[float]) -> float | None:
    return round(100.0 * sum(1 for v in values if v > 0) / len(values), 1) if values else None


def reweight_parts(row: dict[str, Any]) -> list[tuple[str, int]]:
    """List the shadow reweight components ``(label, delta)`` one buy card carries.

    Args:
        row: Decoded signal row (``payload`` as dict).

    Returns:
        Components in a fixed order; deltas are -1 (demote) or +1 (promote).
    """
    payload = _payload(row)
    hhmm = str(row.get("signaled_at") or "")[11:16]
    parts: list[tuple[str, int]] = []
    if str(row.get("signal_type") or "") == "buy_dragon":
        parts.append(("龙头卡", -1))
    if payload.get("trend_ok"):
        parts.append(("日线上升", -1))
    if payload.get("trend_down"):
        parts.append(("日线下降", 1))
    if "13:00" <= hhmm < "14:00":
        parts.append(("13–14点出卡", -1))
    elif "10:00" <= hhmm <= "11:30":
        parts.append(("10:00–11:30出卡", 1))
    return parts


def reweight_score(row: dict[str, Any]) -> int:
    """Return the summed shadow reweight delta for one buy card."""
    return sum(delta for _, delta in reweight_parts(row))


def _scored_buys(rows: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return scored paper buys on trading days, one per (date, code).

    When several desks carried the same code that day, the lit row wins, then
    the earliest signal.
    """
    from market_desk.calendar import is_trading_day

    best: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows or []:
        if str(r.get("signal_type") or "") not in _BUY_TYPES or int(r.get("skipped") or 0):
            continue
        day = _day(r)
        if num(r.get("outcome_day3_pct")) is None or len(day) < 10:
            continue
        try:
            if not is_trading_day(day):
                continue
        except ValueError:
            continue
        key = (day, str(r.get("code") or ""))
        prev = best.get(key)
        if prev is None:
            best[key] = r
            continue
        rank = (not _lit(r), str(r.get("signaled_at") or ""))
        if rank < (not _lit(prev), str(prev.get("signaled_at") or "")):
            best[key] = r
    return list(best.values())


def _judge(metric: float | None, polarity: int, n: int, days: int) -> tuple[str, str]:
    """Return ``(tone, verdict)``: good / bad / mid / low (insufficient sample)."""
    from market_desk.config import GATE_LEDGER_EDGE_PCT, GATE_LEDGER_MIN_DAYS, GATE_LEDGER_MIN_N

    if n < int(GATE_LEDGER_MIN_N) or days < int(GATE_LEDGER_MIN_DAYS):
        return "low", "样本不足"
    if metric is None:
        return "low", "无同日对照"
    signed = metric * polarity
    if signed >= float(GATE_LEDGER_EDGE_PCT):
        return "good", "有效" if polarity > 0 else "挡对了"
    if signed <= -float(GATE_LEDGER_EDGE_PCT):
        return "bad", "反向" if polarity > 0 else "误伤"
    return "mid", "无差异"


def _same_day(samples: list[dict[str, Any]], pred: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """Compare cards matching ``pred`` with same-day peers that do not.

    Returns:
        ``n`` / ``days`` / ``d3`` / ``win3`` of the matching cards and ``excess``:
        mean same-day gap weighted by matching count (None without peers).
    """
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in samples:
        by_day.setdefault(_day(r), []).append(r)
    hit: list[float] = []
    hit_days: set[str] = set()
    wsum = 0.0
    wn = 0
    for day, items in by_day.items():
        g = [_d3(r) for r in items if pred(r)]
        p = [_d3(r) for r in items if not pred(r)]
        if not g:
            continue
        hit.extend(g)
        hit_days.add(day)
        if p:
            wsum += len(g) * ((_mean(g) or 0.0) - (_mean(p) or 0.0))
            wn += len(g)
    return {
        "n": len(hit),
        "days": len(hit_days),
        "d3": _r2(_mean(hit)),
        "win3": _win(hit),
        "excess": round(wsum / wn, 2) if wn else None,
    }


def _reclaim_rows(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Touch-at-plan versus reclaim-the-minute-average entries on touched cards."""
    out: list[dict[str, Any]] = []
    for label, pick in (("全部触价卡", lambda r: True), ("亮过可买的触价卡", _lit)):
        touch: list[float] = []
        reclaim: list[float] = []
        skipped: list[float] = []
        deltas: list[float] = []
        days: set[str] = set()
        for r in samples:
            payload = _payload(r)
            if not payload.get("touched_plan") or not pick(r):
                continue
            plan = num(payload.get("plan_price")) or num(r.get("price"))
            if not plan or plan <= 0:
                continue
            fill = num(r.get("fill_price"))
            base = fill if fill and fill > 0 else plan
            close3 = base * (1.0 + _d3(r) / 100.0)
            t_ret = (close3 / plan - 1.0) * 100.0
            px = num(payload.get("shadow_reclaim_px"))
            if px and px > 0:
                r_ret = (close3 / px - 1.0) * 100.0
                reclaim.append(r_ret)
                deltas.append(r_ret - t_ret)
            else:
                skipped.append(t_ret)
                deltas.append(-t_ret)
            touch.append(t_ret)
            days.add(_day(r))
        delta = _r2(_mean(deltas))
        tone, verdict = _judge(delta, 1, len(touch), len(days))
        verdict = {"有效": "站回更优", "反向": "触价更优"}.get(verdict, verdict)
        out.append({
            "key": label,
            "n": len(touch),
            "days": len(days),
            "touch_d3": _r2(_mean(touch)),
            "touch_win": _win(touch),
            "reclaim_n": len(reclaim),
            "reclaim_d3": _r2(_mean(reclaim)),
            "reclaim_win": _win(reclaim),
            "skip_n": len(skipped),
            "skip_d3": _r2(_mean(skipped)),
            "delta": delta,
            "tone": tone,
            "verdict": verdict,
        })
    return out


def _book_for(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return the day book a breaker would have seen for this card.

    Lit cards use the book frozen at first light; others the book at first sight.
    """
    payload = _payload(row)
    book = payload.get("book_ready") if _lit(row) else None
    book = book or payload.get("book_open")
    return book if isinstance(book, dict) else None


def _breaker_rows(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Judge each breaker level by blocked-vs-kept raw 3-day return."""
    from market_desk.config import EDGE_BREAKER_LEVELS, EDGE_BREAKER_MIN_LIT

    judged = [(r, b) for r in samples if (b := _book_for(r)) is not None]
    out: list[dict[str, Any]] = []
    for level in EDGE_BREAKER_LEVELS:
        blocked: list[dict[str, Any]] = []
        kept: list[float] = []
        for r, book in judged:
            avg = num(book.get("avg_pct"))
            if int(book.get("n") or 0) >= int(EDGE_BREAKER_MIN_LIT) and avg is not None and avg <= level:
                blocked.append(r)
            else:
                kept.append(_d3(r))
        b_d3 = [_d3(r) for r in blocked]
        b_mean = _mean(b_d3)
        k_mean = _mean(kept)
        diff = _r2(b_mean - k_mean) if b_mean is not None and k_mean is not None else None
        tone, verdict = _judge(diff, -1, len(blocked), len({_day(r) for r in blocked}))
        if tone == "good" and (b_mean or 0.0) >= 0:
            tone, verdict = "mid", "拦下仍赚"
        out.append({
            "level": level,
            "n": len(blocked),
            "days": len({_day(r) for r in blocked}),
            "lit_n": sum(1 for r in blocked if _lit(r)),
            "blocked_d3": _r2(b_mean),
            "blocked_win": _win(b_d3),
            "kept_n": len(kept),
            "kept_d3": _r2(k_mean),
            "diff": diff,
            "tone": tone,
            "verdict": verdict,
        })
    return out


def _reweight_rows(oos: list[dict[str, Any]], disc: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same-day excess of each reweight component, out of sample vs discovery."""
    specs: list[tuple[str, int, Callable[[dict[str, Any]], bool]]] = [
        ("合计降权", -1, lambda r: reweight_score(r) <= -1),
        ("合计加权", 1, lambda r: reweight_score(r) >= 1),
    ]
    for label, delta in (
        ("龙头卡", -1),
        ("日线上升", -1),
        ("日线下降", 1),
        ("13–14点出卡", -1),
        ("10:00–11:30出卡", 1),
    ):
        specs.append((label, delta, lambda r, lab=label: any(k == lab for k, _ in reweight_parts(r))))
    out: list[dict[str, Any]] = []
    for label, pol, pred in specs:
        o = _same_day(oos, pred)
        d = _same_day(disc, pred)
        tone, verdict = _judge(o["excess"], pol, o["n"], o["days"])
        out.append({
            "key": label,
            "polarity": pol,
            "n": o["n"],
            "days": o["days"],
            "d3": o["d3"],
            "excess": o["excess"],
            "tone": tone,
            "verdict": verdict,
            "disc_n": d["n"],
            "disc_excess": d["excess"],
        })
    return out


def build_edge_shadow(
    rows: list[dict[str, Any]] | None,
    *,
    since: str | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """Build the three shadow-rule ledgers over scored paper buys.

    Args:
        rows: Signal rows (any order); non-buy, skipped, unscored and
            non-trading-day rows are ignored, one card per date and code.
        since: First out-of-sample trade date (defaults to config).
        days: Rolling out-of-sample window in scored trade dates.

    Returns:
        Dict with ``ok``, ``since``, ``oos_days`` / ``oos_n``, ``reclaim`` /
        ``breaker`` / ``reweight`` row lists and a one-line ``note``.
    """
    from market_desk.config import EDGE_SHADOW_DAYS, EDGE_SHADOW_SINCE

    start = str(since or EDGE_SHADOW_SINCE)
    window = int(days or EDGE_SHADOW_DAYS)
    samples = _scored_buys(rows)
    if not samples:
        return {"ok": False, "since": start, "note": "暂无已打分买点"}
    oos_dates = sorted({_day(r) for r in samples if _day(r) >= start})[-window:]
    keep = set(oos_dates)
    oos = [r for r in samples if _day(r) in keep]
    disc = [r for r in samples if _day(r) < start]
    if oos:
        note = f"样本外：{start} 起 {len(oos_dates)} 日 {len(oos)} 张已打分买卡"
    else:
        note = f"样本外从 {start} 起积累，目前 0 日；降权规则先看发现期（规则就是从这段数据里找的，不算证据）"
    return {
        "ok": True,
        "since": start,
        "oos_days": len(oos_dates),
        "oos_n": len(oos),
        "disc_n": len(disc),
        "reclaim": _reclaim_rows(oos),
        "breaker": _breaker_rows(oos),
        "reweight": _reweight_rows(oos, disc),
        "note": note,
    }
