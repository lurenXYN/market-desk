"""Buy/sell fill execution scoring and the weekly execution board."""

from __future__ import annotations

from typing import Any
from market_desk.numbers import num

from market_desk.review.signals import is_buy_signal, is_sell_signal


def classify_fill_execution(row: dict[str, Any]) -> str | None:
    """Classify a traded buy fill versus the original suggest / chase band.

    Returns ``None`` when there is no plan band (unplanned / manual-only fill)
    so callers can exclude it from hard exec scoring.
    """
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
    plan = num(
        row.get("plan_price")
        if row.get("plan_price") is not None
        else payload.get("plan_price")
    )
    suggest = plan if plan is not None else num(row.get("price"))
    low = wait if wait is not None else suggest
    if low is None and chase is None:
        return None
    if chase is not None and fill >= chase:
        return "chase"
    if low is not None and fill < low:
        return "below"
    if low is not None and chase is not None and low <= fill < chase:
        return "in_band"
    if low is not None and chase is None and fill >= low:
        return "in_band"
    return "other"


def diary_rows_as_exec_fills(
    diary: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Map buy-side exec diary rows into pseudo traded-signal shapes for scoring.

    Rows without a plan price band (wait / suggest / chase) are skipped — they
    count as unplanned manual fills and must not drag the exec score to ``other``.
    """
    out: list[dict[str, Any]] = []
    for row in diary or []:
        side = str(row.get("side") or "").strip().lower()
        if side not in ("buy",):
            continue
        fill = num(row.get("price"))
        if fill is None or fill <= 0:
            continue
        advice = row.get("advice") if isinstance(row.get("advice"), dict) else {}
        buy = advice.get("buy") if isinstance(advice.get("buy"), dict) else {}
        wait = num(buy.get("wait_price") or buy.get("buy_price") or buy.get("price"))
        chase = num(buy.get("chase_price"))
        suggest = num(buy.get("price") or buy.get("buy_price") or wait)
        # No band at all → unplanned; exclude from hard exec score.
        if wait is None and chase is None and suggest is None:
            continue
        kind = str(buy.get("kind") or row.get("kind") or "stock")
        out.append(
            {
                "id": f"diary:{row.get('id')}",
                "signal_type": "buy",
                "traded": 1,
                "code": str(row.get("code") or "").zfill(6),
                "name": row.get("name"),
                "kind": kind,
                "fill_price": fill,
                "price": suggest if suggest is not None else fill,
                "wait_price": wait,
                "chase_price": chase,
                "trade_date": str(row.get("trade_date") or "")[:10],
                "signaled_at": str(row.get("created_at") or ""),
                "payload": {
                    "wait_price": wait,
                    "chase_price": chase,
                    "exec_source": "diary",
                    "advice_source": advice.get("source") or "manual",
                },
                "exec_source": "diary",
            }
        )
    return out


def merge_exec_score_rows(
    signals: list[dict[str, Any]] | None,
    diary: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Prefer traded signals; fill gaps with diary buys (same day+code, no double count)."""
    rows = list(signals or [])
    traded_keys: set[str] = set()
    for r in rows:
        if not is_buy_signal(r.get("signal_type")):
            continue
        if not int(r.get("traded") or 0):
            continue
        day = str(r.get("trade_date") or "")[:10]
        code = str(r.get("code") or "").zfill(6)
        if day and code:
            traded_keys.add(f"{day}|{code}")
    extras: list[dict[str, Any]] = []
    for pseudo in diary_rows_as_exec_fills(diary):
        day = str(pseudo.get("trade_date") or "")[:10]
        code = str(pseudo.get("code") or "").zfill(6)
        key = f"{day}|{code}"
        if key in traded_keys:
            continue
        extras.append(pseudo)
        traded_keys.add(key)
    return rows + extras


def classify_sell_fill_execution(row: dict[str, Any]) -> str | None:
    """Classify a traded sell fill versus the suggested sell / stop band.

    Returns ``None`` when there is no plan price (unplanned manual trim).
    ``in_band`` = at/below suggested sell; ``late`` = sold well above the plan;
    ``early`` = dumped near/under stop when only stop is known.
    """
    typ = str(row.get("signal_type") or "").strip().lower()
    if typ not in ("sell", "half", "clear", "trim") and not is_sell_signal(typ):
        return None
    if not int(row.get("traded") or 0):
        return None
    fill = num(row.get("fill_price"))
    if fill is None:
        return None
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    sell_px = num(
        row.get("sell_price")
        if row.get("sell_price") is not None
        else payload.get("sell_price") or row.get("price")
    )
    stop_px = num(
        row.get("stop_price")
        if row.get("stop_price") is not None
        else payload.get("stop_price")
    )
    if sell_px is None and stop_px is None:
        return None
    if sell_px is not None:
        if fill <= sell_px * 1.005:
            return "in_band"
        if fill >= sell_px * 1.02:
            return "late"
        return "in_band"
    if stop_px is not None:
        if fill <= stop_px * 1.01:
            return "early"
        return "in_band"
    return "other"


def diary_rows_as_sell_fills(
    diary: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Map sell-side exec diary rows into pseudo traded-signal shapes for scoring."""
    out: list[dict[str, Any]] = []
    for row in diary or []:
        side = str(row.get("side") or "").strip().lower()
        if side not in ("sell", "half", "clear", "trim"):
            continue
        fill = num(row.get("price"))
        if fill is None or fill <= 0:
            continue
        advice = row.get("advice") if isinstance(row.get("advice"), dict) else {}
        sell = advice.get("sell") if isinstance(advice.get("sell"), dict) else {}
        sell_px = num(sell.get("sell_price") or sell.get("price") or sell.get("last"))
        stop_px = num(sell.get("stop_price"))
        if sell_px is None and stop_px is None:
            continue
        out.append(
            {
                "id": f"diary-sell:{row.get('id')}",
                "signal_type": "sell",
                "traded": 1,
                "code": str(row.get("code") or "").zfill(6),
                "name": row.get("name"),
                "kind": str(sell.get("kind") or row.get("kind") or "stock"),
                "fill_price": fill,
                "price": sell_px if sell_px is not None else fill,
                "sell_price": sell_px,
                "stop_price": stop_px,
                "trade_date": str(row.get("trade_date") or "")[:10],
                "signaled_at": str(row.get("created_at") or ""),
                "payload": {
                    "sell_price": sell_px,
                    "stop_price": stop_px,
                    "exec_source": "diary",
                    "advice_source": advice.get("source") or "manual",
                },
                "exec_source": "diary",
            }
        )
    return out


def merge_sell_exec_score_rows(
    signals: list[dict[str, Any]] | None,
    diary: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Prefer traded sell signals; fill gaps with diary sells (same day+code)."""
    rows = list(signals or [])
    traded_keys: set[str] = set()
    for r in rows:
        if str(r.get("signal_type") or "") != "sell":
            continue
        if not int(r.get("traded") or 0):
            continue
        day = str(r.get("trade_date") or "")[:10]
        code = str(r.get("code") or "").zfill(6)
        if day and code:
            traded_keys.add(f"{day}|{code}")
    extras: list[dict[str, Any]] = []
    for pseudo in diary_rows_as_sell_fills(diary):
        day = str(pseudo.get("trade_date") or "")[:10]
        code = str(pseudo.get("code") or "").zfill(6)
        key = f"{day}|{code}"
        if key in traded_keys:
            continue
        extras.append(pseudo)
        traded_keys.add(key)
    return rows + extras


def build_sell_exec_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Score how well sell fills followed the suggested exit plan."""
    counts = {"in_band": 0, "late": 0, "early": 0, "other": 0}
    diary_n = 0
    unplanned_n = 0
    items = [
        r
        for r in rows
        if str(r.get("signal_type") or "") == "sell" and int(r.get("traded") or 0)
    ]
    for row in items:
        if str(row.get("exec_source") or "") == "diary" or str(row.get("id") or "").startswith(
            "diary-sell:"
        ):
            diary_n += 1
        kind = classify_sell_fill_execution(row)
        if kind is None:
            unplanned_n += 1
            continue
        if kind in counts:
            counts[kind] += 1
        else:
            counts["other"] += 1
    n = sum(counts.values())
    points = (
        counts["in_band"] * 100
        + counts["early"] * 70
        + counts["other"] * 40
        + counts["late"] * 20
    )
    score = None if n == 0 else round(points / n, 1)
    return {
        "score": score,
        "traded_sell_n": len(items),
        "scored_n": n,
        "unplanned_n": unplanned_n,
        "diary_n": diary_n,
        "in_band_n": counts["in_band"],
        "late_n": counts["late"],
        "early_n": counts["early"],
        "other_n": counts["other"],
    }


def build_exec_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Score how well fills followed the original price plan."""

    def _score_group(items: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {"in_band": 0, "chase": 0, "below": 0, "other": 0}
        diary_n = 0
        unplanned_n = 0
        for row in items:
            if str(row.get("exec_source") or "") == "diary" or str(row.get("id") or "").startswith("diary:"):
                diary_n += 1
            kind = classify_fill_execution(row)
            if kind is None:
                unplanned_n += 1
                continue
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
            "scored_n": n,
            "unplanned_n": unplanned_n,
            "diary_n": diary_n,
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


MISS_KIND_LABELS = {
    "never_touched": "未触达就走",
    "touched_not_bought": "触达未买",
    "gate_blocked": "闸门卡死",
}


def classify_miss_kind(row: dict[str, Any]) -> str | None:
    """Classify why a same-day buy was missed (display / review only).

    - never_touched: plan never hit, price already higher (classic miss_pullback)
    - touched_not_bought: in band / near wait but not traded
    - gate_blocked: hard confirm fails while price ran above plan
    """
    from market_desk.numbers import num

    flags = list(row.get("price_flags") or [])
    mark = str(row.get("price_mark") or "")
    traded = int(row.get("traded") or 0)
    if traded:
        return None
    if "miss_pullback" in flags or "未回踩" in mark:
        return "never_touched"
    if "in_band" in flags or "near_wait" in flags or "建议价附近" in mark or "回踩区间" in mark:
        return "touched_not_bought"
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    fails = list(payload.get("confirm_fail") or row.get("confirm_fail") or [])
    blocked = bool(payload.get("block_ready") or row.get("block_ready"))
    ready = int(row.get("ready") or 0)
    last = num(row.get("live_last") if row.get("live_last") is not None else row.get("last"))
    plan = num(row.get("price") or row.get("plan_price") or row.get("buy_price"))
    if (fails or blocked) and not ready and last is not None and plan is not None and last > plan:
        return "gate_blocked"
    return None
