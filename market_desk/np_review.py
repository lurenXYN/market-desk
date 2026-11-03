"""No-pullback run-up (NP) observation: freeze day bars, compare NP vs touched cards.

NP card = shared buy whose daily low *and* close stayed above the plan price, so
the suggested pullback never filled and the stock ran. TP card = same-day buy
whose low reached the plan price. The module is read-only: it never changes plan
price, ready or hard gates. Evidence counts only from ``NP_SHADOW_SINCE``.

Entries used for outcomes (never the plan price, which NP cards could not fill):
  - E1: first-seen last at signal time (``payload.first_last``) — executable then.
  - E2: signal-day close.
The index leg is the same CSI window as ``review.bench`` (close(D) → close(D+3)).
"""

from __future__ import annotations

from typing import Any

from market_desk.filters import normalize_code
from market_desk.numbers import num
from market_desk.stats_tools import day_means, safe_t

NP_KEY = "np"


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row.get("payload")
    return p if isinstance(p, dict) else {}


def plan_price_of(row: dict[str, Any]) -> float | None:
    """Return the frozen plan price of a signal row (plan_price → suggested price)."""
    p = _payload(row)
    plan = num(row.get("plan_price") if row.get("plan_price") is not None else p.get("plan_price"))
    if plan is None:
        plan = num(row.get("price"))
    return float(plan) if plan is not None and plan > 0 else None


def np_from_bar(
    plan: float | None,
    low: float | None,
    close: float | None,
    high: float | None = None,
) -> dict[str, Any] | None:
    """Classify one signal day against its plan price.

    Args:
        plan: Plan (suggested pullback) price.
        low: Signal-day low.
        close: Signal-day close.
        high: Signal-day high (stored for later range analysis).

    Returns:
        ``{"np": bool, "day_low", "day_close", "day_high", "plan"}`` or None when
        plan / low / close is missing.
    """
    if plan is None or low is None or close is None or plan <= 0:
        return None
    return {
        "np": bool(float(low) > float(plan) and float(close) > float(plan)),
        "day_low": round(float(low), 4),
        "day_close": round(float(close), 4),
        "day_high": None if high is None else round(float(high), 4),
        "plan": round(float(plan), 4),
    }


def _is_shared_buy(row: dict[str, Any]) -> bool:
    from market_desk.review.signals import is_buy_signal

    owner = row.get("owner_user_id")
    return is_buy_signal(row.get("signal_type")) and int(owner or 0) == 0


def freeze_np_values(
    rows: list[dict[str, Any]],
    closes_map: dict[str, Any],
    *,
    today: str,
) -> dict[int, tuple[str, Any]]:
    """Build ``{signal_id: ("np", dict)}`` for shared buys still lacking the key.

    Args:
        rows: Signal rows (with ``payload`` dicts).
        closes_map: ``{code: (dates, closes, ohlc)}`` as returned by the kline fetch.
        today: Current CN date; same-day rows are skipped (bar not settled).
    """
    out: dict[int, tuple[str, Any]] = {}
    for row in rows:
        if not _is_shared_buy(row) or row.get("id") is None:
            continue
        day = str(row.get("trade_date") or "")[:10]
        if len(day) != 10 or day >= today:
            continue
        if _payload(row).get(NP_KEY) is not None:
            continue
        packed = closes_map.get(normalize_code(row.get("code")))
        if not packed or len(packed) < 3:
            continue
        dates, closes, ohlc = packed[0], packed[1], packed[2] or {}
        dates = [str(d)[:10] for d in dates]
        if day not in dates:
            continue
        i = dates.index(day)
        lows = list(ohlc.get("low") or [])
        highs = list(ohlc.get("high") or [])
        low = lows[i] if i < len(lows) else None
        high = highs[i] if i < len(highs) else None
        close = closes[i] if i < len(closes) else None
        frozen = np_from_bar(plan_price_of(row), num(low), num(close), num(high))
        if frozen is not None:
            out[int(row["id"])] = (NP_KEY, frozen)
    return out


def freeze_np_for_rows(
    rows: list[dict[str, Any]],
    closes_map: dict[str, Any],
    *,
    today: str,
) -> int:
    """Persist frozen ``payload.np`` for rows missing it. Return rows written."""
    from market_desk.db.signals import set_signal_payload_once

    values = freeze_np_values(rows, closes_map, today=today)
    return set_signal_payload_once(values) if values else 0


async def backfill_np_freeze(client: Any, *, limit: int = 300, lookback: int = 60) -> int:
    """Fetch daily bars for recent shared buys lacking ``payload.np`` and freeze them.

    Best effort and idempotent (``set_signal_payload_once``); safe to call often.
    """
    from market_desk.db import load_signals
    from market_desk.eastmoney.bars import fetch_daily_klines_many
    from market_desk.review.outcome import _cn_now

    today = _cn_now().strftime("%Y-%m-%d")
    pending = [
        r
        for r in load_signals(limit=4000)
        if _is_shared_buy(r)
        and str(r.get("trade_date") or "") < today
        and _payload(r).get(NP_KEY) is None
    ][: max(1, int(limit))]
    if not pending:
        return 0
    codes = list(dict.fromkeys(normalize_code(r.get("code")) for r in pending))
    packed = await fetch_daily_klines_many(client, codes, limit=int(lookback))
    return freeze_np_for_rows(pending, packed, today=today)


# --------------------------------------------------------------------------- report


def _hhmm(row: dict[str, Any]) -> str:
    stamp = str(row.get("signaled_at") or "")
    return stamp[11:16] if len(stamp) >= 16 else ""


def gap_bucket(gap_pct: float | None) -> str:
    """Bucket first-seen last vs plan price (%): the plan-too-low vs signal-too-late split."""
    if gap_pct is None:
        return "unknown"
    if gap_pct <= 0:
        return "gap<=0"
    if gap_pct <= 1.0:
        return "gap0-1"
    if gap_pct <= 3.0:
        return "gap1-3"
    return "gap>3"


def time_bucket(hhmm: str) -> str:
    """Bucket signal clock time."""
    if not hhmm:
        return "unknown"
    if hhmm < "10:00":
        return "<10:00"
    if hhmm < "11:30":
        return "10:00-11:30"
    if hhmm < "14:00":
        return "11:30-14:00"
    return ">=14:00"


def _trend_bucket(p: dict[str, Any]) -> str:
    if p.get("trend_down"):
        return "down"
    if p.get("trend_ok"):
        return "up"
    return "unknown"


def _regime_bucket(p: dict[str, Any]) -> str:
    mr = p.get("market_regime")
    side = mr.get("side") if isinstance(mr, dict) else None
    return str(side) if side in ("above", "below") else "unknown"


def _dims(row: dict[str, Any], first_last: float | None, plan: float | None) -> dict[str, str]:
    """Return the six preregistered dimension buckets for one card."""
    p = _payload(row)
    gap = None
    if first_last and plan:
        gap = (float(first_last) / float(plan) - 1.0) * 100.0
    return {
        "gap": gap_bucket(gap),
        "time": time_bucket(_hhmm(row)),
        "stage": str(p.get("board_stage") or "unknown"),
        "regime": _regime_bucket(p),
        "deep_v": "deep_v" if p.get("deep_v_day") else "normal",
        "source": str(p.get("desk_source") or "unknown"),
        "trend": _trend_bucket(p),
    }


def build_np_cards(
    buy_rows: list[dict[str, Any]] | None,
    bench_closes: list[tuple[str, float]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return per-card NP/TP records with E1/E2 excess, plus coverage counters.

    Cards are deduped per (day, code) keeping the earliest signal; only shared
    buys with frozen ``payload.np`` and a fully settled 3-session outcome count.
    """
    from market_desk.review.bench import bench_window_pct
    from market_desk.review.outcome import outcome_is_final

    cov = {"buys": 0, "with_np": 0, "scored": 0}
    seen: set[tuple[str, str]] = set()
    memo: dict[str, tuple[float | None, int]] = {}
    cards: list[dict[str, Any]] = []
    ordered = sorted(
        [r for r in (buy_rows or []) if _is_shared_buy(r)],
        key=lambda r: (str(r.get("trade_date") or ""), str(r.get("signaled_at") or "")),
    )
    for row in ordered:
        day = str(row.get("trade_date") or "")[:10]
        key = (day, normalize_code(row.get("code")))
        if key in seen:
            continue
        seen.add(key)
        cov["buys"] += 1
        p = _payload(row)
        frozen = p.get(NP_KEY)
        if not isinstance(frozen, dict) or frozen.get("np") is None:
            continue
        cov["with_np"] += 1
        d3 = num(row.get("outcome_day3_pct"))
        if d3 is None or not outcome_is_final(row):
            continue
        plan = plan_price_of(row)
        fill = num(row.get("fill_price"))
        entry = float(fill) if fill is not None and fill > 0 else plan
        if entry is None:
            continue
        if day not in memo:
            memo[day] = bench_window_pct(bench_closes, day)
        bench, used = memo[day]
        if bench is None or used < 3:
            continue
        cov["scored"] += 1
        close3 = entry * (1.0 + float(d3) / 100.0)
        first_last = num(p.get("first_last")) or num(row.get("last"))
        day_close = num(frozen.get("day_close"))
        e1 = (close3 / first_last - 1.0) * 100.0 - bench if first_last else None
        e2 = (close3 / day_close - 1.0) * 100.0 - bench if day_close else None
        cards.append(
            {
                "day": day,
                "code": key[1],
                "np": bool(frozen.get("np")),
                "xs_e1": None if e1 is None else round(e1, 2),
                "xs_e2": None if e2 is None else round(e2, 2),
                "dims": _dims(row, first_last, plan),
            }
        )
    return cards, cov


def _mean(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def _paired(cards: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Day-paired NP − TP excess with conservative t and half-sample sign check."""
    by_day: dict[str, dict[bool, list[float]]] = {}
    for c in cards:
        v = c.get(field)
        if v is None:
            continue
        by_day.setdefault(c["day"], {True: [], False: []})[bool(c["np"])].append(float(v))
    diffs: list[tuple[str, float]] = []
    for day in sorted(by_day):
        a, b = by_day[day][True], by_day[day][False]
        if a and b:
            diffs.append((day, sum(a) / len(a) - sum(b) / len(b)))
    series = [d for _, d in diffs]
    half = len(series) // 2
    h1, h2 = series[:half], series[half:]
    m1 = sum(h1) / len(h1) if h1 else None
    m2 = sum(h2) / len(h2) if h2 else None
    same = bool(m1 is not None and m2 is not None and m1 * m2 > 0)
    t = safe_t(series)
    return {
        "days": len(series),
        "diff": _mean(series),
        "t": None if t is None else round(t, 2),
        "half1": None if m1 is None else round(m1, 2),
        "half2": None if m2 is None else round(m2, 2),
        "halves_same_sign": same,
    }


def _bucket_rows(cards: list[dict[str, Any]], dim: str, min_n: int) -> list[dict[str, Any]]:
    """Descriptive NP/TP split of one dimension (pooled, not day-clustered)."""
    acc: dict[str, dict[str, list[float]]] = {}
    for c in cards:
        b = c["dims"].get(dim, "unknown")
        slot = acc.setdefault(b, {"np": [], "tp": [], "np_n": [], "tp_n": []})
        slot["np_n" if c["np"] else "tp_n"].append(1.0)
        v = c.get("xs_e1")
        if v is not None:
            slot["np" if c["np"] else "tp"].append(float(v))
    out: list[dict[str, Any]] = []
    for b, s in sorted(acc.items()):
        n_np, n_tp = len(s["np_n"]), len(s["tp_n"])
        tot = n_np + n_tp
        out.append(
            {
                "bucket": b,
                "np_n": n_np,
                "tp_n": n_tp,
                "np_rate": round(n_np / tot, 3) if tot else None,
                "np_xs": _mean(s["np"]),
                "tp_xs": _mean(s["tp"]),
                "thin": n_np < min_n,
            }
        )
    return out


def _block(cards: list[dict[str, Any]]) -> dict[str, Any]:
    from market_desk.config import NP_BUCKET_MIN_N

    np_cards = [c for c in cards if c["np"]]
    tp_cards = [c for c in cards if not c["np"]]
    np_days = len({c["day"] for c in np_cards})
    return {
        "np_n": len(np_cards),
        "tp_n": len(tp_cards),
        "np_days": np_days,
        "e1": {
            "np_xs": _mean([c["xs_e1"] for c in np_cards if c["xs_e1"] is not None]),
            "tp_xs": _mean([c["xs_e1"] for c in tp_cards if c["xs_e1"] is not None]),
            **_paired(cards, "xs_e1"),
        },
        "e2": {
            "np_xs": _mean([c["xs_e2"] for c in np_cards if c["xs_e2"] is not None]),
            "tp_xs": _mean([c["xs_e2"] for c in tp_cards if c["xs_e2"] is not None]),
            **_paired(cards, "xs_e2"),
        },
        "dims": {
            dim: _bucket_rows(cards, dim, NP_BUCKET_MIN_N)
            for dim in ("gap", "time", "stage", "regime", "deep_v", "source", "trend")
        },
    }


def np_verdict(oos: dict[str, Any]) -> dict[str, Any]:
    """Apply the preregistered gate to the out-of-sample block (E1 primary)."""
    from market_desk.config import (
        NP_GATE_MIN_DAYS,
        NP_GATE_MIN_DIFF,
        NP_GATE_MIN_N,
        NP_GATE_MIN_T,
    )

    e1 = oos.get("e1") or {}
    n, days = int(oos.get("np_n") or 0), int(e1.get("days") or 0)
    if n < NP_GATE_MIN_N or days < NP_GATE_MIN_DAYS:
        return {
            "verdict": "insufficient",
            "note": f"样本不足：NP {n}/{NP_GATE_MIN_N} 张，配对日 {days}/{NP_GATE_MIN_DAYS}；维持计划价，只记账。",
        }
    diff, t = e1.get("diff"), e1.get("t")
    ok = (
        diff is not None
        and t is not None
        and diff >= NP_GATE_MIN_DIFF
        and abs(t) >= NP_GATE_MIN_T
        and t > 0
        and bool(e1.get("halves_same_sign"))
        and (e1.get("half1") or 0) > 0
    )
    if ok:
        return {
            "verdict": "soft_candidate",
            "note": "过预登记线：NP 不弱于 TP。仅可议软改（提示半仓/回踩带小幅放宽，影子≥2周）；不碰 ready 硬闸。",
        }
    return {
        "verdict": "hold",
        "note": "未过线：维持计划价，不为追 NP 放宽；继续记账。",
    }


def build_np_missed(
    rows: list[dict[str, Any]] | None,
    *,
    trade_date: str | None = None,
) -> list[dict[str, Any]]:
    """List unfilled shared buys frozen as NP (day low & close above plan).

    ``没买就是没买``: any card without a fill counts, regardless of skipped /
    traded flags. Live ``price_flags`` are ignored — adapt learning must not
    depend on review-page enrich that never persists.

    Args:
        rows: Signal rows with ``payload`` dicts.
        trade_date: Optional YYYY-MM-DD filter; ``None`` keeps the full window.
    """
    from market_desk.adapt.context import context_from_row
    from market_desk.review.signals import is_buy_signal

    day = str(trade_date or "")[:10]
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("owner_user_id") or 0) != 0:
            continue
        d = str(row.get("trade_date") or "")[:10]
        if day and d != day:
            continue
        fill = num(row.get("fill_price"))
        if fill is not None and fill > 0:
            continue
        frozen = _payload(row).get(NP_KEY)
        if not isinstance(frozen, dict) or not frozen.get("np"):
            continue
        ctx = context_from_row(row)
        out.append(
            {
                "id": row.get("id"),
                "code": row.get("code"),
                "name": row.get("name"),
                "price": row.get("price"),
                "trade_date": d,
                "signaled_at": row.get("signaled_at"),
                "mainline": row.get("mainline"),
                "context": ctx,
                "context_label": ctx.get("label"),
                "miss_kind": "never_touched",
                "miss_kind_label": "未回踩·已上行(NP)",
                "np": frozen,
            }
        )
    return out


def np_miss_loosen_allowed(buy_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return whether the miss→pullback channel may loosen (NP oos soft_candidate).

    Uses offline bench closes only so the live adapt path never blocks on HTTP.
    """
    try:
        closes: list[tuple[str, float]] = []
        try:
            from market_desk.dual_score import load_bench_closes

            closes = load_bench_closes(limit=240, allow_http=False)
        except Exception:
            closes = []
        pack = build_np_review(buy_rows, bench_closes=closes)
        verdict = str(pack.get("verdict") or "")
        ok = verdict == "soft_candidate"
        return {
            "ok": ok,
            "verdict": verdict,
            "note": pack.get("verdict_note") or "",
        }
    except Exception as exc:
        return {"ok": False, "verdict": "error", "note": f"{type(exc).__name__}: {exc}"[:120]}


def signal_bar_np_coverage(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Report how many shared buys have a same-day bar and a frozen ``payload.np``.

    Args:
        rows: Signal rows; loads recent buys when omitted.
    """
    from market_desk.db.bars_cache import load_daily_bars
    from market_desk.review.signals import is_buy_signal

    if rows is None:
        try:
            from market_desk.db import load_signals

            rows = [
                r
                for r in load_signals(limit=2000)
                if is_buy_signal(r.get("signal_type")) and int(r.get("owner_user_id") or 0) == 0
            ]
        except Exception:
            rows = []
    buys = [
        r
        for r in (rows or [])
        if is_buy_signal(r.get("signal_type")) and int(r.get("owner_user_id") or 0) == 0
    ]
    n = len(buys)
    with_bar = 0
    with_np = 0
    # Cache one load per code to avoid N identical queries.
    bar_days: dict[str, set[str]] = {}
    for r in buys:
        code = normalize_code(r.get("code"))
        day = str(r.get("trade_date") or "")[:10]
        if code and day:
            if code not in bar_days:
                try:
                    bars = load_daily_bars(code, limit=120)
                    bar_days[code] = {str(b.get("trade_date") or b.get("date") or "")[:10] for b in bars}
                except Exception:
                    bar_days[code] = set()
            if day in bar_days[code]:
                with_bar += 1
        frozen = _payload(r).get(NP_KEY)
        if isinstance(frozen, dict) and frozen.get("np") is not None:
            with_np += 1
    bar_rate = round(with_bar / n, 3) if n else None
    np_rate = round(with_np / n, 3) if n else None
    ok = bool(n) and (bar_rate or 0) >= 0.8 and (np_rate or 0) >= 0.8
    return {
        "ok": ok,
        "buys": n,
        "with_bar": with_bar,
        "with_np": with_np,
        "bar_rate": bar_rate,
        "np_rate": np_rate,
        "note": "共享买卡中有当日K线 / 已冻 payload.np 的占比；<80% 时 NP 与离线重放偏软",
    }


async def backfill_signal_daily_bars(
    client: Any,
    *,
    limit_codes: int = 80,
    lookback: int = 60,
) -> dict[str, Any]:
    """Fetch daily bars for recent shared-buy codes so ``market_bar_daily`` fills in.

    Best effort; relies on ``fetch_daily_bars`` upserting on every successful path.
    """
    from market_desk.db import load_signals
    from market_desk.eastmoney.bars import fetch_daily_klines_many
    from market_desk.review.signals import is_buy_signal

    codes: list[str] = []
    seen: set[str] = set()
    for r in load_signals(limit=4000):
        if not is_buy_signal(r.get("signal_type")):
            continue
        if int(r.get("owner_user_id") or 0) != 0:
            continue
        c = normalize_code(r.get("code"))
        if not c or c in seen:
            continue
        seen.add(c)
        codes.append(c)
        if len(codes) >= int(limit_codes):
            break
    if not codes:
        return {"ok": True, "codes": 0, "fetched": 0}
    packed = await fetch_daily_klines_many(client, codes, limit=int(lookback))
    return {"ok": True, "codes": len(codes), "fetched": len(packed)}


def build_np_review(
    buy_rows: list[dict[str, Any]] | None,
    *,
    bench_closes: list[tuple[str, float]] | None = None,
) -> dict[str, Any]:
    """Assemble the NP observation block for agent-pack.

    Args:
        buy_rows: Recent signal rows (payload dicts, outcomes attached).
        bench_closes: Index ``[(date, close)]``; loaded offline when omitted.
    """
    from market_desk.config import NP_SHADOW_SINCE

    closes = bench_closes
    if closes is None:
        try:
            from market_desk.dual_score import load_bench_closes

            closes = load_bench_closes(limit=240, allow_http=True)
        except Exception:
            closes = []
    cards, cov = build_np_cards(buy_rows, closes or [])
    since = str(NP_SHADOW_SINCE)[:10]
    oos = _block([c for c in cards if c["day"] >= since])
    disc = _block([c for c in cards if c["day"] < since])
    verdict = np_verdict(oos)
    return {
        "ok": bool(cards),
        "since": since,
        "coverage": cov,
        "verdict": verdict["verdict"],
        "verdict_note": verdict["note"],
        "oos": oos,
        "discovery": disc,
        "note": (
            "NP=日线最低与收盘都高于计划价（没回踩就上去）；TP=同日触到计划价。"
            "E1=出卡时 last 进场，E2=当日收盘进场，均扣同窗中证1000；不用计划价（NP 吃不到）。"
            "判定只看 oos（自 since 起，按日配对 NP−TP，E1 为主）；discovery 仅供找共性，不得据此改参。"
            "dims 为描述性分桶（np_rate=该桶 NP 占比），n 薄只参考。"
        ),
    }
