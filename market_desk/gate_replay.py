"""As-of replay of buy-side confirm gates (minute / off_high / thin).

Recomputes gate pass/fail from frozen card fields (or daily-bar fallback) under
alternate multiplier scenarios. Does **not** re-pick boards or rewrite ready in
the live desk — research / ops only.
"""

from __future__ import annotations

from typing import Any

from market_desk.numbers import num
from market_desk.review.signals import is_buy_signal

GATE_KEYS = ("minute", "off_high", "thin")


def eval_off_high(
    *,
    last: float | None,
    high: float | None,
    kind: str = "stock",
    mult: float = 1.0,
) -> dict[str, Any]:
    """Recompute the day-high distance gate under a multiplier."""
    from market_desk.config import ETF_OFF_HIGH_MIN, STOCK_OFF_HIGH_MIN

    if last is None or high is None or float(high) <= 0:
        return {"ok": None, "replayable": False, "fail": None, "dist": None, "need": None}
    dist = (float(high) - float(last)) / float(high) * 100.0
    base = float(ETF_OFF_HIGH_MIN if str(kind).lower() == "etf" else STOCK_OFF_HIGH_MIN)
    need = base * max(0.75, min(1.25, float(mult)))
    ok = dist + 1e-9 >= need
    return {
        "ok": ok,
        "replayable": True,
        "fail": None if ok else "离日高过近",
        "dist": round(dist, 3),
        "need": round(need, 3),
        "mult": round(float(mult), 3),
    }


def eval_thin_confirm(
    *,
    kind: str,
    thin_confirm: bool,
    cross_n: int | None,
    theme_n: int | None,
    mult: float = 1.0,
) -> dict[str, Any]:
    """Recompute thin cross-board / theme resonance under a multiplier."""
    from market_desk.config import THIN_CROSS_BOARD_MIN, THIN_CROSS_THEME_MIN

    if str(kind).lower() == "etf":
        return {"ok": True, "replayable": True, "fail": None, "note": "ETF跳过薄确认"}
    if not thin_confirm:
        return {"ok": True, "replayable": True, "fail": None, "note": "当日非薄确认市"}
    if cross_n is None and theme_n is None:
        return {"ok": None, "replayable": False, "fail": None, "note": "缺 cross_n/theme_n"}
    thin_m = max(0.75, min(1.25, float(mult)))
    cross_need = max(1, int(round(float(THIN_CROSS_BOARD_MIN) * thin_m)))
    theme_need = max(1, int(round(float(THIN_CROSS_THEME_MIN) * thin_m)))
    if thin_m < 1.0:
        cross_need = max(1, int(THIN_CROSS_BOARD_MIN) - 1)
        theme_need = max(0, int(THIN_CROSS_THEME_MIN) - 1)
    elif thin_m > 1.0:
        cross_need = max(cross_need, int(THIN_CROSS_BOARD_MIN) + 1)
        theme_need = max(theme_need, int(THIN_CROSS_THEME_MIN) + 1)
    cn = int(cross_n or 0)
    tn = int(theme_n or 0)
    ok = cn >= cross_need or tn >= theme_need
    return {
        "ok": ok,
        "replayable": True,
        "fail": None if ok else "薄确认缺跨板块共振",
        "cross_n": cn,
        "theme_n": tn,
        "cross_need": cross_need,
        "theme_need": theme_need,
        "mult": round(thin_m, 3),
    }


def eval_minute_asof(
    minute: dict[str, Any] | None,
    *,
    mult: float = 1.0,
) -> dict[str, Any]:
    """Re-threshold a frozen minute snapshot when structural fields exist.

    Without pullback / tip fields, falls back to the frozen ``ok`` flag
    (not multiplier-sensitive).
    """
    m = minute if isinstance(minute, dict) else None
    if not m:
        return {"ok": None, "replayable": False, "fail": None, "note": "无分时快照"}
    pullback = num(m.get("pullback_pct"))
    # Prefer re-threshold when we have a numeric pullback.
    if pullback is not None:
        from market_desk.config import (
            MINUTE_GRIND_PULLBACK,
            MINUTE_SHALLOW,
            MINUTE_TIP_THR_NARROW,
            MINUTE_TIP_THR_WIDE,
        )

        gate = max(0.75, min(1.25, float(mult)))
        tip_thr = float(MINUTE_TIP_THR_WIDE) * gate
        # If span was stored, pick narrow/wide; else use wide.
        span = num(m.get("span_pct"))
        if span is not None and float(span) < 1.0:
            tip_thr = float(MINUTE_TIP_THR_NARROW) * gate
        shallow_thr = float(MINUTE_SHALLOW) * gate
        grind_thr = float(MINUTE_GRIND_PULLBACK) * gate
        fails: list[str] = []
        at_tip = float(pullback) < tip_thr
        if at_tip:
            fails.append("分时贴近近期高点")
        if bool(m.get("grinding_high")) and float(pullback) < grind_thr:
            fails.append("分时仍在抬高点")
        if m.get("above_ma") is False:
            fails.append(str(m.get("label") or "分时仍在均线下方"))
        if float(pullback) < shallow_thr and not at_tip:
            fails.append("分时回撤过浅")
        # Volume cool: if frozen vol_ok is False and tip/shallow, keep fail.
        if m.get("vol_ok") is False and (at_tip or float(pullback) < shallow_thr):
            fails.append("分时回踩量能未缩")
        ok = len(fails) == 0
        return {
            "ok": ok,
            "replayable": True,
            "fail": fails[0] if fails else None,
            "fails": fails,
            "pullback_pct": float(pullback),
            "mult": round(gate, 3),
            "note": "按冻结回撤重阈",
        }
    # Frozen boolean only.
    frozen_ok = m.get("ok")
    if frozen_ok is True:
        return {"ok": True, "replayable": "frozen", "fail": None, "note": "冻结ok，倍率未改判"}
    if frozen_ok is False:
        return {
            "ok": False,
            "replayable": "frozen",
            "fail": str(m.get("label") or "分时未过"),
            "note": "冻结未过，缺回撤字段无法重阈",
        }
    return {"ok": None, "replayable": False, "fail": None, "note": "分时未验"}


def _row_kind(row: dict[str, Any], payload: dict[str, Any]) -> str:
    return "etf" if str(row.get("kind") or payload.get("kind") or "") == "etf" else "stock"


def _extract_card(row: dict[str, Any]) -> dict[str, Any]:
    """Pull gate inputs from a stored buy signal row."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    kind = _row_kind(row, payload)
    last = num(payload.get("last") if payload.get("last") is not None else row.get("price"))
    high = num(payload.get("high"))
    minute = payload.get("minute") if isinstance(payload.get("minute"), dict) else None
    thin_confirm = bool(payload.get("thin_confirm"))
    # Infer thin day from fail hist when flag missing.
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
    if not thin_confirm and any("薄确认" in f for f in fails):
        thin_confirm = True
    cross_n = payload.get("cross_n")
    theme_n = payload.get("cross_theme_n")
    if cross_n is not None:
        cross_n = int(cross_n)
    if theme_n is not None:
        theme_n = int(theme_n)
    return {
        "id": row.get("id"),
        "trade_date": str(row.get("trade_date") or "")[:10],
        "code": row.get("code"),
        "kind": kind,
        "ready": int(row.get("ready") or 0),
        "ever_ready": bool(payload.get("ever_ready") or row.get("ready")),
        "last": last,
        "high": high,
        "minute": minute,
        "thin_confirm": thin_confirm,
        "cross_n": cross_n,
        "theme_n": theme_n,
        "orig_fails": fails,
        "outcome_day3_pct": num(row.get("outcome_day3_pct")),
        "excess_d3_pct": num(row.get("excess_d3_pct")),
        "outcome_label": str(row.get("outcome_label") or ""),
    }


def fill_high_from_bars(card: dict[str, Any]) -> dict[str, Any]:
    """Fill missing high/last from daily bar cache (EOD approx; marked)."""
    if card.get("high") is not None and card.get("last") is not None:
        return card
    code = str(card.get("code") or "")
    day = str(card.get("trade_date") or "")[:10]
    if not code or not day:
        return card
    try:
        from market_desk.db import load_daily_bars

        bars = load_daily_bars(code, start=day, end=day, limit=2)
    except Exception:
        return card
    if not bars:
        return card
    b = bars[-1]
    out = dict(card)
    if out.get("high") is None and num(b.get("high")) is not None:
        out["high"] = float(b["high"])
        out["high_source"] = "daily_bar"
    if out.get("last") is None and num(b.get("close")) is not None:
        out["last"] = float(b["close"])
        out["last_source"] = "daily_bar"
    return out


def eval_card_gates(
    card: dict[str, Any],
    *,
    mults: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate minute / off_high / thin for one card under ``mults``."""
    m = {k: 1.0 for k in GATE_KEYS}
    if mults:
        for k, v in mults.items():
            if k in m and v is not None:
                m[k] = float(v)
    off = eval_off_high(
        last=card.get("last"),
        high=card.get("high"),
        kind=str(card.get("kind") or "stock"),
        mult=m["off_high"],
    )
    thin = eval_thin_confirm(
        kind=str(card.get("kind") or "stock"),
        thin_confirm=bool(card.get("thin_confirm")),
        cross_n=card.get("cross_n"),
        theme_n=card.get("theme_n"),
        mult=m["thin"],
    )
    minute = eval_minute_asof(card.get("minute"), mult=m["minute"])
    hard_fails = [
        f
        for f in (off.get("fail"), thin.get("fail"), minute.get("fail"))
        if f
    ]
    # Soft minute (ok is None) does not hard-block in live desk.
    hard_block = bool(off.get("ok") is False or thin.get("ok") is False)
    if minute.get("ok") is False and minute.get("replayable") in (True, "frozen"):
        # Mirror live: minute fail blocks ready when evaluated false.
        hard_block = True
    return {
        "mults": {k: round(m[k], 3) for k in GATE_KEYS},
        "off_high": off,
        "thin": thin,
        "minute": minute,
        "hard_fails": hard_fails,
        "would_block": hard_block,
        "would_pass": not hard_block and off.get("ok") is not False and thin.get("ok") is not False,
    }


def _scenario_mults(name: str, kind: str = "stock") -> dict[str, float]:
    """Named multiplier packs for ops / agent-pack."""
    n = str(name or "baseline").strip().lower()
    if n in ("baseline", "flat", "1"):
        return {k: 1.0 for k in GATE_KEYS}
    if n in ("current", "live_bias"):
        try:
            from market_desk.adapt import resolve_gate_mult

            return {k: float(resolve_gate_mult(k, 1.0, kind=kind)) for k in GATE_KEYS}
        except Exception:
            return {k: 1.0 for k in GATE_KEYS}
    if n in ("env_friendly", "friendly"):
        from market_desk.env_gate import env_mult_table

        return dict(env_mult_table("friendly", kind))
    if n in ("env_hostile", "hostile"):
        from market_desk.env_gate import env_mult_table

        return dict(env_mult_table("hostile", kind))
    return {k: 1.0 for k in GATE_KEYS}


def replay_buy_gates(
    rows: list[dict[str, Any]] | None,
    *,
    scenarios: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    use_bars_fallback: bool = True,
    limit: int = 800,
) -> dict[str, Any]:
    """Replay buy confirm gates across named multiplier scenarios.

    Returns per-scenario flip counts vs the card's original ready/block state,
    plus outcome means for flipped-to-pass cohorts when labels exist.
    """
    names = scenarios or ["baseline", "current", "env_friendly", "env_hostile"]
    cards: list[dict[str, Any]] = []
    for row in rows or []:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        day = str(row.get("trade_date") or "")[:10]
        if since and day and day < str(since)[:10]:
            continue
        if until and day and day > str(until)[:10]:
            continue
        card = _extract_card(row)
        if use_bars_fallback:
            card = fill_high_from_bars(card)
        cards.append(card)
        if len(cards) >= int(limit):
            break

    by_scenario: dict[str, Any] = {}
    for name in names:
        flip_to_pass = 0
        flip_to_block = 0
        same = 0
        replayable_n = 0
        pass_abs: list[float] = []
        block_kept_abs: list[float] = []
        days: set[str] = set()
        for card in cards:
            days.add(str(card.get("trade_date") or ""))
            mults = _scenario_mults(name, kind=str(card.get("kind") or "stock"))
            ev = eval_card_gates(card, mults=mults)
            # Original blocked if confirm fails present or ready=0 with fails.
            orig_blocked = bool(card.get("orig_fails")) and not bool(card.get("ready"))
            if not orig_blocked and not card.get("ready") and card.get("orig_fails"):
                orig_blocked = True
            if card.get("ready") and not card.get("orig_fails"):
                orig_blocked = False
            # Prefer ready bit when no fail hist.
            if not card.get("orig_fails"):
                orig_blocked = not bool(card.get("ready") or card.get("ever_ready"))

            any_replay = any(
                (ev[g].get("replayable") in (True, "frozen"))
                for g in ("off_high", "thin", "minute")
                if isinstance(ev.get(g), dict)
            )
            if any_replay:
                replayable_n += 1
            new_block = bool(ev.get("would_block"))
            if orig_blocked and not new_block:
                flip_to_pass += 1
                if card.get("outcome_day3_pct") is not None:
                    pass_abs.append(float(card["outcome_day3_pct"]))
            elif (not orig_blocked) and new_block:
                flip_to_block += 1
            else:
                same += 1
                if orig_blocked and card.get("outcome_day3_pct") is not None:
                    block_kept_abs.append(float(card["outcome_day3_pct"]))

        by_scenario[name] = {
            "n": len(cards),
            "replayable_n": replayable_n,
            "day_n": len({d for d in days if d}),
            "flip_to_pass": flip_to_pass,
            "flip_to_block": flip_to_block,
            "same": same,
            "flip_to_pass_mean_abs": (
                round(sum(pass_abs) / len(pass_abs), 2) if pass_abs else None
            ),
            "flip_to_pass_n_scored": len(pass_abs),
            "blocked_kept_mean_abs": (
                round(sum(block_kept_abs) / len(block_kept_abs), 2) if block_kept_abs else None
            ),
            "note": (
                "flip_to_pass=原拦→新倍率放行；看 mean_abs 是否像假杀。"
                if flip_to_pass
                else "无翻转到放行"
            ),
        }

    return {
        "ok": True,
        "schema": "gate_replay.v1",
        "since": since,
        "until": until,
        "card_n": len(cards),
        "scenarios": by_scenario,
        "note": (
            "As-of 买闸重放（离日高/薄确认/分时）；缺 high 时用日线兜底。"
            "不重跑选板块，不改 live ready。"
        ),
    }


def build_gate_replay_from_db(
    *,
    days: int = 20,
    scenarios: list[str] | None = None,
    limit: int = 800,
) -> dict[str, Any]:
    """Load recent buy signals and run ``replay_buy_gates``."""
    from datetime import datetime, timedelta

    from market_desk.db import load_signals

    until = datetime.now().strftime("%Y-%m-%d")
    since = (datetime.now() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    try:
        rows = load_signals(limit=max(200, int(limit) * 2))
    except Exception as exc:
        return {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:160]}
    return replay_buy_gates(
        rows,
        scenarios=scenarios,
        since=since,
        until=until,
        limit=limit,
    )
