"""Paper trading book: ever-ready buy → hold → system sell signal exit.

Cash account starts at ``SIM_INITIAL_CASH``; each buy/sell pays ``TRADE_FEE_CNY``.
Start gate: at least one sell fill and equity / initial - 1 > 0.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from market_desk.config import (
    SIM_BOOK_SINCE,
    SIM_INITIAL_CASH,
    SIM_MAX_POS_FRAC,
    TRADE_FEE_CNY,
)
from market_desk.db.sim_desk import (
    apply_sim_buy_fill,
    apply_sim_sell_fill,
    count_sim_sell_fills,
    get_sim_account,
    insert_sim_lot,
    list_open_sim_codes,
    load_open_sim_lots,
    load_sim_fills,
    load_sim_fills_for_lot,
    load_sim_lots,
    set_sim_cash,
    sim_lot_exists,
    sum_sim_realized_pnl_cny,
    update_sim_lot_marks,
    wipe_sim_book,
)
from market_desk.filters import normalize_code

log = logging.getLogger(__name__)
FEE = float(TRADE_FEE_CNY)

BUY_TYPES = (
    "buy",
    "buy_side",
    "buy_link",
    "buy_trial",
    "buy_indep",
    "buy_dragon",
)

# A-share continuous auction (local clock). Call auction 09:15–09:25 is not fillable.
SESSION_AM = (9 * 60 + 30, 11 * 60 + 30)  # inclusive minutes
SESSION_PM = (13 * 60, 15 * 60)
# Sealed-board heuristic: one-tick range + large day move.
_SEAL_PCT = 4.5


def _f(value: Any) -> float | None:
    """Coerce to float; None when missing."""
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def stamp_minutes(stamp: str | None) -> int | None:
    """Return hour*60+minute from ``YYYY-MM-DD HH:MM[:SS]``, else None."""
    s = str(stamp or "").strip()
    if len(s) < 16:
        return None
    try:
        hh = int(s[11:13])
        mm = int(s[14:16])
        return hh * 60 + mm
    except (TypeError, ValueError):
        return None


def is_continuous_session_minutes(minutes: int | None) -> bool:
    """Return True inside 09:30–11:30 or 13:00–15:00."""
    if minutes is None:
        return False
    am0, am1 = SESSION_AM
    pm0, pm1 = SESSION_PM
    return (am0 <= minutes <= am1) or (pm0 <= minutes <= pm1)


def is_continuous_session_stamp(stamp: str | None) -> bool:
    """Return True when a datetime stamp sits in continuous auction."""
    return is_continuous_session_minutes(stamp_minutes(stamp))


def clamp_buy_fill_stamp(stamp: str | None, trade_date: str) -> str | None:
    """Move a pre-09:30 ready stamp to 09:30:00 same day; reject outside sessions.

    Ready may light in call auction, but a marketable buy only starts at 09:30.
    Afternoon / lunch stamps outside the continuous windows stay rejected.
    """
    day = str(trade_date or "")[:10]
    if not day:
        return None
    mins = stamp_minutes(stamp)
    if mins is None:
        # Unknown clock: only allow if we invent open print at 09:30.
        return f"{day} 09:30:00"
    if mins < SESSION_AM[0]:
        # Lit in auction / open mute before continuous — fill at open.
        return f"{day} 09:30:00"
    if is_continuous_session_minutes(mins):
        s = str(stamp or "").strip()
        return s[:19] if len(s) >= 19 else f"{day} {s[11:16]}:00"
    return None


def quote_sealed_limit_up(quote: dict[str, Any] | None) -> bool:
    """Heuristic: sealed limit-up (cannot buy). Prefer OHLC one-word, else pct."""
    q = quote if isinstance(quote, dict) else {}
    high = _f(q.get("high"))
    low = _f(q.get("low"))
    last = _f(q.get("price") if q.get("price") is not None else q.get("last"))
    pct = _f(q.get("pct") if q.get("pct") is not None else q.get("last_pct"))
    if high is not None and low is not None and abs(high - low) <= 1e-6:
        if pct is not None and pct >= _SEAL_PCT:
            return True
        if last is not None and high > 0 and abs(last - high) <= 1e-6 and (pct or 0) >= _SEAL_PCT:
            return True
    if pct is not None and pct >= 9.5 and high is not None and last is not None:
        if abs(last - high) / max(high, 1e-9) < 0.001:
            return True
    return False


def quote_sealed_limit_down(quote: dict[str, Any] | None) -> bool:
    """Heuristic: sealed limit-down (cannot sell)."""
    q = quote if isinstance(quote, dict) else {}
    high = _f(q.get("high"))
    low = _f(q.get("low"))
    last = _f(q.get("price") if q.get("price") is not None else q.get("last"))
    pct = _f(q.get("pct") if q.get("pct") is not None else q.get("last_pct"))
    if high is not None and low is not None and abs(high - low) <= 1e-6:
        if pct is not None and pct <= -_SEAL_PCT:
            return True
        if last is not None and low > 0 and abs(last - low) <= 1e-6 and (pct or 0) <= -_SEAL_PCT:
            return True
    if pct is not None and pct <= -9.5 and low is not None and last is not None:
        if abs(last - low) / max(low, 1e-9) < 0.001:
            return True
    return False


def buy_fill_evidence(row: dict[str, Any]) -> tuple[bool, str]:
    """Return whether the card shows a marketable buy (not paper-ready alone).

    Accepts touched plan / near entry / cf touch / first-ready mark in band.
    """
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    cf = payload.get("cf") if isinstance(payload.get("cf"), dict) else {}
    if int(cf.get("touch_n") or 0) > 0 and _f(cf.get("touch_px")):
        return True, "cf_touch"
    if payload.get("touched_plan") or payload.get("near_entry"):
        return True, "touched_plan"
    # Ready with a live print at or below plan / first-ready px.
    plan = _f(payload.get("plan_price")) or _f(row.get("price"))
    ready_px = _f(payload.get("first_ready_px"))
    last = _f(row.get("last")) or _f(payload.get("first_last"))
    if payload.get("ever_ready") or row.get("ready"):
        if ready_px and ready_px > 0:
            return True, "first_ready_px"
        if plan and last and last <= plan * 1.002:
            return True, "last_le_plan"
    if payload.get("probe_ok") or payload.get("ever_probe"):
        if ready_px or (plan and last and last <= plan * 1.002):
            return True, "probe_touch"
    return False, "no_touch"


def assess_buy_fill(
    row: dict[str, Any],
    *,
    quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide if a lit buy can open a sim lot under real fill rules."""
    day = str(row.get("trade_date") or "")[:10]
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    raw_at = str(payload.get("first_ready_at") or row.get("signaled_at") or "")[:19] or None
    fill_at = clamp_buy_fill_stamp(raw_at, day)
    if not fill_at:
        return {"ok": False, "reason": "outside_session", "fill_at": None, "fill_px": None}
    ok_ev, ev = buy_fill_evidence(row)
    if not ok_ev:
        return {"ok": False, "reason": ev, "fill_at": fill_at, "fill_px": None}
    if quote_sealed_limit_up(quote):
        return {"ok": False, "reason": "limit_up_sealed", "fill_at": fill_at, "fill_px": None}
    px = entry_px_from_signal(row)
    if px is None or px <= 0:
        return {"ok": False, "reason": "no_px", "fill_at": fill_at, "fill_px": None}
    # Live quote: prefer mark if still marketable and not chasing far above plan.
    q_last = None
    if isinstance(quote, dict):
        q_last = _f(quote.get("price") if quote.get("price") is not None else quote.get("last"))
    fill_px = px
    if q_last and q_last > 0:
        # Do not fill more than ~1.5% through the ready/plan print (chase).
        if q_last <= px * 1.015:
            fill_px = q_last if q_last <= px else px
        else:
            return {"ok": False, "reason": "chase_too_far", "fill_at": fill_at, "fill_px": None}
    return {"ok": True, "reason": ev, "fill_at": fill_at, "fill_px": float(fill_px)}


def assess_sell_fill(
    item: dict[str, Any],
    *,
    lot: dict[str, Any],
    trade_date: str,
    signaled_at: str | None,
    quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide if a ready sell can flatten a sim lot under real fill rules."""
    day = str(trade_date or "")[:10]
    open_day = str(lot.get("open_date") or "")[:10]
    if open_day and day and open_day == day:
        return {"ok": False, "reason": "t1_locked"}
    if item.get("t1_locked"):
        return {"ok": False, "reason": "t1_locked"}
    if not is_continuous_session_stamp(signaled_at):
        # After-hours / lunch snapshots must not invent fills.
        return {"ok": False, "reason": "outside_session"}
    if quote_sealed_limit_down(quote):
        return {"ok": False, "reason": "limit_down_sealed"}
    mode = str(item.get("exit_mode") or "")
    if mode not in ("half", "clear"):
        return {"ok": False, "reason": "not_exit"}
    if not item.get("ready"):
        return {"ok": False, "reason": "not_ready"}
    exit_px = _f(item.get("sell_price")) or _f(item.get("last"))
    if exit_px is None or exit_px <= 0:
        return {"ok": False, "reason": "no_px"}
    return {"ok": True, "reason": "ok", "fill_px": float(exit_px)}


def size_from_payload(payload: dict[str, Any] | None, *, ready: bool = True) -> tuple[float, str]:
    """Infer open weight and tag from a buy-signal payload.

    Full ready → 1.0 ``full``. Early-half / soft half / probe-only → 0.5.
    """
    p = payload if isinstance(payload, dict) else {}
    if isinstance(p.get("early_half"), dict):
        return 0.5, "half"
    try:
        sm = float(p.get("size_mult") or 1.0)
    except (TypeError, ValueError):
        sm = 1.0
    if sm <= 0.55:
        return 0.5, "half"
    if p.get("probe_ok") and not ready and not p.get("ever_ready"):
        return 0.5, "probe"
    # Lit via probe path only (ever_probe without ever treating as full).
    if p.get("ever_probe") and not p.get("ever_ready") and not ready:
        return 0.5, "probe"
    return 1.0, "full"


def entry_px_from_signal(row: dict[str, Any]) -> float | None:
    """Prefer first-ready mark, else plan / signal price."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for cand in (
        payload.get("first_ready_px"),
        payload.get("plan_price"),
        row.get("price"),
        row.get("last"),
        payload.get("first_last"),
    ):
        px = _f(cand)
        if px is not None and px > 0:
            return px
    return None


def round_lot(shares: float) -> int:
    """Floor to a 100-share board lot."""
    try:
        n = int(shares)
    except (TypeError, ValueError):
        return 0
    return max(0, (n // 100) * 100)


def net_pnl_pct(entry: float, exit_px: float, *, fee_buy: float = 0.0, fee_sell: float = 0.0, qty: int = 0) -> float:
    """Return sell-side percent PnL after explicit CNY fees allocated on notional."""
    if entry <= 0 or exit_px <= 0:
        return 0.0
    gross = (exit_px / entry - 1.0) * 100.0
    if qty > 0 and entry > 0:
        notional = entry * int(qty)
        fee_pct = (float(fee_buy) + float(fee_sell)) / notional * 100.0
        return gross - fee_pct
    return gross


def mark_to_market(lots: list[dict[str, Any]] | None = None) -> float:
    """Sum open market value using last marks."""
    total = 0.0
    for lot in lots if lots is not None else load_open_sim_lots():
        qty = int(lot.get("qty") or 0)
        px = _f(lot.get("last_px")) or _f(lot.get("open_px")) or 0.0
        if qty > 0 and px > 0:
            total += qty * px
    return total


def ensure_sim_book_epoch() -> bool:
    """Wipe pre-epoch backfill once when ``SIM_BOOK_SINCE`` advances. Return True if wiped."""
    from market_desk.db.sim_desk import get_sim_account_epoch, set_sim_account_epoch

    epoch = str(SIM_BOOK_SINCE or "")[:10]
    if not epoch:
        return False
    # Ensure row exists first without wiping.
    get_sim_account(SIM_INITIAL_CASH)
    prev = get_sim_account_epoch()
    if prev == epoch:
        return False
    wipe_sim_book(initial_cash=SIM_INITIAL_CASH, epoch=epoch)
    set_sim_account_epoch(epoch)
    log.info("sim book epoch reset to %s (was %s)", epoch, prev or "—")
    return True


def account_snapshot() -> dict[str, Any]:
    """Cash + MTM equity for the paper account."""
    ensure_sim_book_epoch()
    acct = get_sim_account(SIM_INITIAL_CASH)
    lots = load_open_sim_lots()
    mtm = mark_to_market(lots)
    cash = float(acct["cash"])
    initial = float(acct["initial_cash"])
    equity = cash + mtm
    ret = ((equity / initial) - 1.0) * 100.0 if initial > 0 else None
    return {
        **acct,
        "mtm": round(mtm, 2),
        "equity": round(equity, 2),
        "return_pct": round(ret, 4) if ret is not None else None,
        "fee_cny": FEE,
        "max_pos_frac": float(SIM_MAX_POS_FRAC),
    }


def portfolio_stats(fills: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Aggregate account equity into the start-gate summary."""
    del fills  # equity-based gate; fills still drive closed_n via count
    open_lots = load_open_sim_lots()
    closed_n = count_sim_sell_fills()
    acct = account_snapshot()
    ret = acct.get("return_pct")
    gate_ok = bool(closed_n >= 1 and ret is not None and ret > 0)
    realized = sum_sim_realized_pnl_cny()
    return {
        "closed_n": closed_n,
        "open_n": len(open_lots),
        "initial_cash": acct["initial_cash"],
        "cash": acct["cash"],
        "mtm": acct["mtm"],
        "equity": acct["equity"],
        "cum_pnl_pct": ret,
        "realized_pnl_cny": round(realized, 2),
        "gate_ok": gate_ok,
        "gate_label": (
            "可开干（权益相对初始>0）"
            if gate_ok
            else (
                "未过线·尚无平仓"
                if closed_n < 1
                else "未过线·权益未转正"
            )
        ),
    }


def plan_buy_qty(
    *,
    px: float,
    open_weight: float,
    cash: float,
    equity: float,
    fee: float = FEE,
) -> int:
    """Shares to buy for one card: min(cash-fee, equity×frac×weight), 100-lot."""
    if px <= 0 or cash <= fee:
        return 0
    w = max(0.05, min(1.0, float(open_weight or 1.0)))
    target = min(cash - fee, max(0.0, float(equity) * float(SIM_MAX_POS_FRAC) * w))
    if target <= 0:
        return 0
    return round_lot(target / px)


def recent_closed(limit: int = 8) -> list[dict[str, Any]]:
    """Compact recent sell fills for the start-gate gauge."""
    out: list[dict[str, Any]] = []
    for r in load_sim_fills(limit=max(1, min(int(limit), 40)), side="sell"):
        out.append(
            {
                "code": r.get("code"),
                "name": r.get("name"),
                "open_date": r.get("open_date"),
                "trade_date": r.get("trade_date"),
                "pnl_pct": r.get("pnl_pct"),
                "pnl_cny": r.get("pnl_cny"),
                "qty": r.get("qty"),
                "fee": r.get("fee"),
                "weight": r.get("weight"),
                "exit_mode": r.get("exit_mode"),
                "role_label": r.get("role_label"),
                "size_tag": r.get("size_tag"),
                "open_px": r.get("open_px"),
                "px": r.get("px"),
            }
        )
    return out


def recent_trades(limit: int = 80) -> list[dict[str, Any]]:
    """Full trade log (buys + sells) newest first."""
    backfill_missing_buy_fills()
    return load_sim_fills(limit=max(1, min(int(limit), 300)))


def backfill_missing_buy_fills(*, limit: int = 5000) -> int:
    """Insert opening buy rows for lots that lack a buy fill (legacy / test opens)."""
    added = 0
    for lot in load_sim_lots(limit=max(1, min(int(limit), 8000))):
        lid = int(lot.get("id") or 0)
        if lid <= 0:
            continue
        fills = load_sim_fills_for_lot(lid)
        if any(str(f.get("side") or "") == "buy" for f in fills):
            continue
        qty = int(lot.get("qty_open") or 0) or int(lot.get("qty") or 0)
        px = _f(lot.get("open_px")) or 0.0
        day = str(lot.get("open_date") or "")[:10]
        if qty <= 0 or px <= 0 or not day:
            continue
        fee = _f(lot.get("buy_fee"))
        if fee is None:
            fee = FEE
        ok = apply_sim_buy_fill(
            lot_id=lid,
            trade_date=day,
            filled_at=str(lot.get("open_at") or "")[:19] or None,
            px=float(px),
            qty=qty,
            fee=float(fee),
            cash_after=0.0,
            weight=float(lot.get("open_weight") or 1.0),
            note="backfill_buy_log",
        )
        if ok:
            added += 1
    return added


def start_gate_snapshot() -> dict[str, Any]:
    """Ops / review one-liner for the auto-order start gate."""
    stats = portfolio_stats()
    return {
        **stats,
        "account": account_snapshot(),
        "recent": recent_closed(8),
        "rule": (
            "初始2万纸面资金；亮过可买且可成交→扣款+手续费开仓；"
            "卖点且可卖出→回款+手续费平仓；权益相对初始>0 且已有平仓才允许券商阶段1–3"
        ),
    }


def open_lots_from_signals(
    signals: list[dict[str, Any]],
    *,
    include_probe: bool = True,
    quotes: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Open sim lots for lit buy cards that are fillable and affordable."""
    opened = 0
    qmap = quotes or {}
    acct = account_snapshot()
    cash = float(acct["cash"])
    equity = float(acct["equity"])
    for row in signals or []:
        sig_type = str(row.get("signal_type") or "")
        if sig_type not in BUY_TYPES:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        ready_flag = bool(row.get("ready"))
        lit = bool(payload.get("ever_ready") or payload.get("first_ready_at") or ready_flag)
        probe = bool(payload.get("probe_ok") or payload.get("ever_probe"))
        if not lit and not (include_probe and probe):
            continue
        code = normalize_code(row.get("code"))
        day = str(row.get("trade_date") or "")[:10]
        if not code or not day:
            continue
        since = str(SIM_BOOK_SINCE or "")[:10]
        if since and day < since:
            continue
        if sim_lot_exists(day, code, sig_type):
            continue
        verdict = assess_buy_fill(row, quote=qmap.get(code))
        if not verdict.get("ok"):
            continue
        px = float(verdict["fill_px"])
        open_at = str(verdict.get("fill_at") or "")[:19] or None
        weight, tag = size_from_payload(payload, ready=lit or ready_flag)
        if not lit and probe:
            weight, tag = 0.5, "probe"
        qty = plan_buy_qty(px=px, open_weight=weight, cash=cash, equity=equity, fee=FEE)
        if qty <= 0:
            continue
        notional = qty * px
        if cash < notional + FEE:
            continue
        cash_after = cash - notional - FEE
        lot_id = insert_sim_lot(
            {
                "code": code,
                "name": row.get("name") or "",
                "kind": row.get("kind") or "stock",
                "signal_type": sig_type,
                "buy_signal_id": row.get("id"),
                "open_date": day,
                "open_at": open_at,
                "open_px": px,
                "open_weight": weight,
                "size_tag": tag,
                "qty": qty,
                "qty_open": qty,
                "buy_fee": FEE,
                "peak_px": px,
                "last_px": px,
                "payload": {
                    "source": "signal",
                    "probe": tag == "probe",
                    "mainline": row.get("mainline"),
                    "fill_reason": verdict.get("reason"),
                    "raw_ready_at": str(
                        payload.get("first_ready_at") or row.get("signaled_at") or ""
                    )[:19]
                    or None,
                },
            }
        )
        if not lot_id:
            continue
        set_sim_cash(cash_after, initial_cash=float(acct["initial_cash"]))
        apply_sim_buy_fill(
            lot_id=lot_id,
            trade_date=day,
            filled_at=open_at,
            px=px,
            qty=qty,
            fee=FEE,
            cash_after=cash_after,
            weight=weight,
            note=str(verdict.get("reason") or ""),
        )
        cash = cash_after
        equity = cash + mark_to_market()
        opened += 1
    return opened


def backfill_open_from_db(*, limit: int = 2000) -> int:
    """Scan stored buy signals and open missing lots. Return new count."""
    from market_desk.db import _connect
    from market_desk.db.signals import _SIGNAL_SELECT, _decode_signal_rows

    get_sim_account(SIM_INITIAL_CASH)
    marks = ",".join("?" for _ in BUY_TYPES)
    with _connect() as conn:
        rows = conn.execute(
            _SIGNAL_SELECT
            + f"""
            WHERE signal_type IN ({marks})
            ORDER BY trade_date DESC, id DESC
            LIMIT ?
            """,
            (*BUY_TYPES, max(1, min(int(limit), 5000))),
        ).fetchall()
    signals = _decode_signal_rows(rows)
    signals.sort(key=lambda r: (str(r.get("trade_date") or ""), int(r.get("id") or 0)))
    return open_lots_from_signals(signals)


def replay_exits_from_sell_signals(*, limit: int = 3000) -> int:
    """Close open lots using stored system sell signals after the open date.

    Uses the earliest ready sell per code after open (clear preferred over half
    when both exist same day). Respects T+1 and continuous-session stamps.
    """
    from market_desk.db import _connect
    from market_desk.db.signals import _SIGNAL_SELECT, _decode_signal_rows

    lots = load_open_sim_lots()
    if not lots:
        return 0
    with _connect() as conn:
        rows = conn.execute(
            _SIGNAL_SELECT
            + """
            WHERE signal_type = 'sell' AND ready = 1
            ORDER BY trade_date ASC, id ASC
            LIMIT ?
            """,
            (max(1, min(int(limit), 8000)),),
        ).fetchall()
    sells = _decode_signal_rows(rows)
    by_code: dict[str, list[dict[str, Any]]] = {}
    for s in sells:
        code = normalize_code(s.get("code"))
        if not code:
            continue
        by_code.setdefault(code, []).append(s)

    filled = 0
    acct = account_snapshot()
    cash = float(acct["cash"])
    lot_by_id = {int(l["id"]): l for l in lots}
    for lot in list(lots):
        code = normalize_code(lot.get("code"))
        open_day = str(lot.get("open_date") or "")[:10]
        if not code or not open_day:
            continue
        candidates = by_code.get(code) or []
        picked = None
        for s in candidates:
            day = str(s.get("trade_date") or "")[:10]
            if not day or day <= open_day:
                continue
            at = str(s.get("signaled_at") or f"{day} 14:00:00")[:19]
            if not is_continuous_session_stamp(at):
                # Clamp afternoon sells recorded oddly — allow 09:30–15:00 wall.
                mins = stamp_minutes(at)
                if mins is None or mins < SESSION_AM[0] or mins > SESSION_PM[1]:
                    continue
                if SESSION_AM[1] < mins < SESSION_PM[0]:
                    continue
            payload = s.get("payload") if isinstance(s.get("payload"), dict) else {}
            mode = str(payload.get("exit_mode") or "")
            if mode not in ("half", "clear"):
                # Infer from action / role.
                action = str(s.get("action") or "")
                mode = "half" if "减半" in action else "clear"
            item = {
                "ready": True,
                "exit_mode": mode,
                "sell_price": _f(s.get("price")) or _f(s.get("last")),
                "last": _f(s.get("last")) or _f(s.get("price")),
                "role_label": s.get("action") or "建议卖出",
                "t1_locked": False,
            }
            verdict = assess_sell_fill(
                item, lot=lot, trade_date=day, signaled_at=at, quote=None
            )
            if not verdict.get("ok"):
                continue
            picked = (s, item, verdict, day, at, mode)
            break
        if not picked:
            continue
        _s, item, verdict, day, at, mode = picked
        held = int(lot.get("qty") or 0)
        if held <= 0:
            continue
        exit_px = float(verdict["fill_px"])
        entry = float(lot.get("open_px") or 0)
        if mode == "half" and held >= 200:
            sell_qty = round_lot(held / 2) or 100
            if sell_qty >= held:
                sell_qty = held
                mode = "clear"
        else:
            sell_qty = held
            mode = "clear"
        proceeds = sell_qty * exit_px
        if proceeds <= FEE:
            continue
        cash_after = cash + proceeds - FEE
        buy_fee_alloc = float(lot.get("buy_fee") or 0) * (
            sell_qty / float(lot.get("qty_open") or held or sell_qty)
        )
        pnl_cny = proceeds - FEE - entry * sell_qty - buy_fee_alloc
        pnl = net_pnl_pct(entry, exit_px, fee_buy=buy_fee_alloc, fee_sell=FEE, qty=sell_qty)
        frac = sell_qty / float(lot.get("qty_open") or held or sell_qty)
        ok = apply_sim_sell_fill(
            int(lot["id"]),
            trade_date=day,
            filled_at=at,
            px=exit_px,
            qty=sell_qty,
            fee=FEE,
            cash_after=cash_after,
            frac=frac,
            weight=float(lot.get("open_weight") or 1.0) * frac,
            pnl_pct=pnl,
            pnl_cny=pnl_cny,
            exit_mode=mode,
            role_label=str(item.get("role_label") or "回放卖出"),
            note="replay_sell_signal",
        )
        if ok:
            set_sim_cash(cash_after, initial_cash=float(acct["initial_cash"]))
            cash = cash_after
            filled += 1
            lot_by_id[int(lot["id"])]["qty"] = max(0, held - sell_qty)
    return filled


def _quote_map_from_snapshot(
    snapshot: dict[str, Any] | None,
    book_quotes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build code → mark dict from engine marks and snapshot pools."""
    out: dict[str, dict[str, Any]] = {}
    for code, q in (book_quotes or {}).items():
        c = normalize_code(code) or str(code or "").zfill(6)
        if c and isinstance(q, dict):
            out[c] = dict(q)
            out[c]["code"] = c
    snap = snapshot or {}
    for pool in ("etfs", "hot_boards", "pin_boards", "recommend_flat"):
        for row in snap.get(pool) or []:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("code") or row.get("leader_code"))
            if not code:
                continue
            price = row.get("price") if row.get("price") is not None else row.get("last")
            if price is None:
                continue
            if code not in out or out[code].get("price") is None:
                out[code] = {
                    "code": code,
                    "name": row.get("name") or row.get("leader_name") or "",
                    "price": price,
                    "pct": row.get("pct") or row.get("last_pct"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                }
    # Flatten recommend trees from verdict.
    verdict = snap.get("verdict") or {}
    for key in (
        "recommend",
        "side_recommend",
        "link_recommend",
        "watch_trial_recommend",
        "independent_recommend",
        "dragon_recommend",
    ):
        block = verdict.get(key) or {}
        for item in block.get("items") or []:
            code = normalize_code(item.get("code"))
            if not code:
                continue
            price = item.get("last") if item.get("last") is not None else item.get("buy_price")
            if price is None:
                continue
            if code not in out or out[code].get("price") is None:
                out[code] = {
                    "code": code,
                    "name": item.get("name") or "",
                    "price": price,
                    "pct": item.get("pct") or item.get("last_pct"),
                    "high": item.get("high"),
                    "low": item.get("low"),
                }
    return out


def lots_as_positions(lots: list[dict[str, Any]], quotes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Map open sim lots to fake position rows for ``build_sell_advice``."""
    positions: list[dict[str, Any]] = []
    for lot in lots:
        code = normalize_code(lot.get("code"))
        if not code:
            continue
        qty = int(lot.get("qty") or 0)
        rem = float(lot.get("remaining") or 0)
        if qty <= 0 and rem <= 0.001:
            continue
        if qty <= 0:
            # Legacy lot without share count: synthesize from weight.
            base = 200 if float(lot.get("open_weight") or 1) >= 0.99 else 100
            qty = max(100, round_lot(base * rem))
        q = quotes.get(code) or {}
        last = _f(q.get("price") if q.get("price") is not None else q.get("last"))
        if last is None:
            last = _f(lot.get("last_px"))
        buy = float(lot.get("open_px") or 0)
        if buy <= 0:
            continue
        peak = _f(lot.get("peak_px")) or buy
        if last is not None and last > peak:
            peak = last
        high = _f(q.get("high"))
        low = _f(q.get("low"))
        pct = _f(q.get("pct") if q.get("pct") is not None else q.get("last_pct"))
        pnl = ((last / buy) - 1.0) * 100.0 if last is not None else None
        meta = lot.get("payload") if isinstance(lot.get("payload"), dict) else {}
        last_sell = _f(meta.get("last_sell_price"))
        day_sold = int(meta.get("day_sold_qty") or 0)
        positions.append(
            {
                "id": -int(lot["id"]),
                "code": code,
                "name": lot.get("name") or q.get("name") or code,
                "kind": lot.get("kind") or "stock",
                "qty": qty,
                "buy_price": buy,
                "cost": buy,
                "last": last,
                "high": high,
                "low": low,
                "last_pct": pct,
                "pct": pct,
                "pnl_pct": pnl,
                "peak_price": peak,
                "last_buy_date": str(lot.get("open_date") or "")[:10],
                "created_at": str(lot.get("open_at") or lot.get("open_date") or ""),
                "last_sell_price": last_sell,
                "day_sold_qty": day_sold,
                "sim_lot_id": int(lot["id"]),
                "sim_remaining": rem if rem > 0 else 1.0,
                "sim_open_weight": float(lot.get("open_weight") or 1.0),
            }
        )
    return positions


def _apply_ready_sells(
    advice_items: list[dict[str, Any]],
    lot_by_id: dict[int, dict[str, Any]],
    *,
    trade_date: str,
    signaled_at: str | None,
    quotes: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Apply ready sell cards onto matching open lots. Return fill count."""
    filled = 0
    day = str(trade_date or "")[:10]
    at = (signaled_at or "")[:19] or None
    qmap = quotes or {}
    acct = account_snapshot()
    cash = float(acct["cash"])
    for item in advice_items or []:
        mode = str(item.get("exit_mode") or "")
        if mode not in ("half", "clear"):
            continue
        lot_id = item.get("sim_lot_id")
        if lot_id is None:
            try:
                pid = int(item.get("id") or 0)
                if pid < 0:
                    lot_id = -pid
            except (TypeError, ValueError):
                lot_id = None
        if not lot_id:
            continue
        lot = lot_by_id.get(int(lot_id))
        if not lot:
            continue
        held = int(lot.get("qty") or 0)
        rem = float(lot.get("remaining") or 0)
        if held <= 0 and rem <= 0.001:
            continue
        code = normalize_code(lot.get("code") or item.get("code"))
        verdict = assess_sell_fill(
            item,
            lot=lot,
            trade_date=day,
            signaled_at=at,
            quote=qmap.get(code or ""),
        )
        if not verdict.get("ok"):
            continue
        entry = float(lot.get("open_px") or 0)
        exit_px = float(verdict["fill_px"])
        if entry <= 0:
            continue
        if held <= 0:
            base = 200 if float(lot.get("open_weight") or 1) >= 0.99 else 100
            held = max(100, round_lot(base * max(rem, 1.0)))
        if mode == "half" and held >= 200:
            sell_qty = round_lot(held / 2) or 100
            if sell_qty >= held:
                sell_qty = held
                mode = "clear"
        else:
            sell_qty = held
            mode = "clear"
        if sell_qty <= 0:
            continue
        proceeds = sell_qty * exit_px
        if proceeds <= FEE:
            continue
        cash_after = cash + proceeds - FEE
        buy_fee_alloc = float(lot.get("buy_fee") or 0) * (sell_qty / float(lot.get("qty_open") or held or sell_qty))
        pnl_cny = proceeds - FEE - (entry * sell_qty) - buy_fee_alloc
        pnl = net_pnl_pct(
            entry, exit_px, fee_buy=buy_fee_alloc, fee_sell=FEE, qty=sell_qty
        )
        frac = sell_qty / float(lot.get("qty_open") or held or sell_qty)
        open_w = float(lot.get("open_weight") or 1.0)
        weight = open_w * frac
        ok = apply_sim_sell_fill(
            int(lot_id),
            trade_date=day,
            filled_at=at,
            px=float(exit_px),
            qty=sell_qty,
            fee=FEE,
            cash_after=cash_after,
            frac=frac,
            weight=weight,
            pnl_pct=pnl,
            pnl_cny=pnl_cny,
            exit_mode=mode,
            role_label=str(item.get("role_label") or ""),
            note=str(item.get("role_label") or mode),
        )
        if ok:
            set_sim_cash(cash_after, initial_cash=float(acct["initial_cash"]))
            cash = cash_after
            filled += 1
            lot["qty"] = max(0, held - sell_qty)
            if lot["qty"] <= 0:
                lot["status"] = "closed"
                lot["remaining"] = 0.0
    return filled


def sync_exits_from_snapshot(
    snapshot: dict[str, Any],
    *,
    book_quotes: dict[str, dict[str, Any]] | None = None,
    trends_by_code: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mark open lots and apply system sell advice. Return sync stats."""
    trade_date = str(snapshot.get("trade_date") or "")
    if len(trade_date) == 8:
        trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    trade_date = trade_date[:10]
    if not trade_date:
        return {"ok": False, "reason": "no_trade_date", "opened": 0, "filled": 0}

    quotes = _quote_map_from_snapshot(snapshot, book_quotes)
    signaled_at = str(snapshot.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # Open any newly lit cards that are fillable under continuous-auction rules.
    opened = 0
    try:
        from market_desk.db import load_signals_for_date

        opened = open_lots_from_signals(
            load_signals_for_date(trade_date), quotes=quotes
        )
    except Exception:
        log.exception("sim_desk open from today signals failed")

    lots = load_open_sim_lots()

    # Update marks / peaks (allowed anytime; fills still session-gated).
    for lot in lots:
        code = normalize_code(lot.get("code"))
        q = quotes.get(code or "") or {}
        last = _f(q.get("price") if q.get("price") is not None else q.get("last"))
        if last is None:
            continue
        peak = _f(lot.get("peak_px")) or float(lot.get("open_px") or last)
        if last > peak:
            peak = last
        update_sim_lot_marks(
            int(lot["id"]),
            last_px=last,
            peak_px=peak,
            last_mark_at=signaled_at[:19],
        )
        lot["last_px"] = last
        lot["peak_px"] = peak

    lots = load_open_sim_lots()
    positions = lots_as_positions(lots, quotes)
    filled = 0
    session_ok = is_continuous_session_stamp(signaled_at)
    if positions and session_ok:
        try:
            from market_desk.verdict.sell_advice import build_sell_advice
            from market_desk.sell_open_buffer import apply_sell_open_buffer

            clock = _parse_snap_now(signaled_at)
            advice = build_sell_advice(
                positions,
                snapshot.get("verdict") or {},
                snapshot.get("phase") or "",
                trade_date=trade_date,
                trends_by_code=trends_by_code or {},
                similar=snapshot.get("similar_days"),
                metrics=snapshot.get("metrics"),
                now=clock,
            )
            # Soft sells wait for open buffer (same as personal desk); hard stops pass.
            try:
                from market_desk.config import SELL_OPEN_WATCH_MINUTES
                from market_desk.settings import setting

                watch_m = int(setting("sell_open_watch_minutes", SELL_OPEN_WATCH_MINUTES))
            except Exception:
                watch_m = 15
            advice = apply_sell_open_buffer(
                advice,
                now=clock,
                watch_minutes=watch_m,
                minutes_by_code={},
            )
            by_code: dict[str, dict[str, Any]] = {
                normalize_code(p.get("code")) or "": p for p in positions
            }
            items = list(advice.get("all_items") or advice.get("items") or [])
            for it in items:
                code = normalize_code(it.get("code"))
                pos = by_code.get(code or "")
                if pos:
                    it["sim_lot_id"] = pos.get("sim_lot_id")
                    it["id"] = pos.get("id")
            lot_by_id = {int(l["id"]): l for l in lots}
            filled = _apply_ready_sells(
                items,
                lot_by_id,
                trade_date=trade_date,
                signaled_at=signaled_at,
                quotes=quotes,
            )
        except Exception:
            log.exception("sim_desk sell advice failed")

    stats = portfolio_stats()
    return {
        "ok": True,
        "opened": opened,
        "filled": filled,
        "session_ok": session_ok,
        "open_n": stats["open_n"],
        "closed_n": stats["closed_n"],
        "cum_pnl_pct": stats["cum_pnl_pct"],
        "gate_ok": stats["gate_ok"],
    }


def _parse_snap_now(stamp: str | None) -> datetime | None:
    """Parse snapshot ``updated_at`` for sell advice clock."""
    s = str(stamp or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def sync_from_engine(
    snapshot: dict[str, Any],
    *,
    book_quotes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Engine-refresh entry: open lit cards + try exits. Never raises."""
    try:
        ensure_sim_book_epoch()
        get_sim_account(SIM_INITIAL_CASH)
        backfill_missing_buy_fills()
        return sync_exits_from_snapshot(snapshot, book_quotes=book_quotes)
    except Exception as exc:
        log.exception("sim_desk sync failed")
        return {"ok": False, "reason": str(exc)[:160], "opened": 0, "filled": 0}


def day_alert_lines(*, trade_date: str | None = None) -> list[str]:
    """Stage-0 digest lines: today's buys and sells (for page toast / EOD merge)."""
    day = (trade_date or "")[:10]
    if not day:
        try:
            from market_desk.calendar import today_cn

            day = today_cn()
        except Exception:
            day = datetime.now().strftime("%Y-%m-%d")
    lines: list[str] = []
    # Newest-first fills; keep buys and sells both, never mislabel buys as 平仓.
    for fill in load_sim_fills(limit=200):
        if str(fill.get("trade_date") or "")[:10] != day:
            continue
        side = str(fill.get("side") or "").lower()
        code = fill.get("code") or ""
        name = fill.get("name") or ""
        qty = fill.get("qty") or "—"
        px = fill.get("px")
        if side == "buy":
            tag = fill.get("size_tag") or "买入"
            lines.append(f"模拟买入 {code} {name} · {tag} · {qty}股 · {px}")
        elif side == "sell":
            mode = fill.get("exit_mode") or "clear"
            mode_zh = "减半" if mode == "half" else "清仓"
            pnl = fill.get("pnl_pct")
            pnl_s = f"{float(pnl):+.2f}%" if pnl is not None else "—"
            lines.append(f"模拟卖出 {code} {name} · {mode_zh} · {qty}股 · {pnl_s}")
    return lines[:20]


def format_sim_digest_bullets(
    *,
    trade_date: str | None = None,
    fill_cap: int = 2,
) -> list[str]:
    """Compact sim-book bullets for the 15:05 EOD WeChat (merged, not a 2nd push).

    Returns a gate headline plus up to ``fill_cap`` today's fills. Empty when the
    paper book has never been used (no opens/closes and equity still at initial).
    """
    day = (trade_date or "")[:10]
    if not day:
        try:
            from market_desk.calendar import today_cn

            day = today_cn()
        except Exception:
            day = datetime.now().strftime("%Y-%m-%d")
    try:
        gate = start_gate_snapshot()
    except Exception:
        return []
    closed_n = int(gate.get("closed_n") or 0)
    open_n = int(gate.get("open_n") or 0)
    fills = day_alert_lines(trade_date=day)
    if closed_n <= 0 and open_n <= 0 and not fills:
        return []
    cum = gate.get("cum_pnl_pct")
    cum_s = f"{float(cum):+.2f}%" if cum is not None else "—"
    head = (
        f"模拟账本 · 平仓{closed_n} · 持仓{open_n} · 累计{cum_s} · "
        f"{gate.get('gate_label') or '—'}"
    )
    out = [head]
    for line in fills[: max(0, int(fill_cap))]:
        out.append(line)
    return out


# Re-export quote helper list for engine refresh.
open_sim_codes = list_open_sim_codes
