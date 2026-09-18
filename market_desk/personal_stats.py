"""Personal P&L calendar and display-only win-rate stats (not adapt heat)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def build_personal_pnl_calendar(
    *,
    user_id: int,
    days: int = 20,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Build a compact day/week P&L calendar from exec diary + EOD snapshots.

    Display-only: never feeds ``build_size_heat``. Days with diary activity or
    a stored ``eod:{date}`` brief contribute; missing days are skipped.
    """
    from market_desk.db import load_exec_diary, load_setting

    uid = int(user_id)
    today = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    lim = max(5, min(60, int(days)))
    # Walk back ~2× calendar days to cover weekends/holidays.
    start = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=lim * 2 + 5)
    ).strftime("%Y-%m-%d")
    diary = load_exec_diary(user_id=uid, trade_date=None, limit=400)
    by_day: dict[str, dict[str, Any]] = {}

    def _ensure(day: str) -> dict[str, Any]:
        d = str(day)[:10]
        if d not in by_day:
            by_day[d] = {
                "date": d,
                "buy_n": 0,
                "sell_n": 0,
                "diary_n": 0,
                "day_pnl": None,
                "notes": [],
            }
        return by_day[d]

    for row in diary or []:
        d = str(row.get("trade_date") or "")[:10]
        if not d or d < start or d > today:
            continue
        slot = _ensure(d)
        slot["diary_n"] += 1
        side = str(row.get("side") or "").lower()
        if side == "buy":
            slot["buy_n"] += 1
        elif side in ("sell", "trim", "clear", "half"):
            slot["sell_n"] += 1
        name = row.get("name") or row.get("code") or ""
        px = row.get("price")
        qty = row.get("qty")
        bit = f"{side} {name}"
        if qty:
            bit += f"×{qty}"
        if px is not None:
            bit += f"@{px}"
        if len(slot["notes"]) < 4:
            slot["notes"].append(bit)

    # Prefer archived EOD brief P&L when present.
    for i in range(lim * 2 + 5):
        d = (
            datetime.strptime(today, "%Y-%m-%d") - timedelta(days=i)
        ).strftime("%Y-%m-%d")
        if d < start:
            break
        raw = load_setting(f"eod:{d}")
        if not isinstance(raw, dict):
            continue
        pnl = raw.get("day_pnl")
        if pnl is None:
            continue
        slot = _ensure(d)
        try:
            slot["day_pnl"] = round(float(pnl), 2)
        except (TypeError, ValueError):
            pass

    items = sorted(by_day.values(), key=lambda x: x["date"], reverse=True)[:lim]
    # Win rate: day with day_pnl>0 counts as win; days without pnl skipped.
    scored = [x for x in items if x.get("day_pnl") is not None]
    wins = [x for x in scored if float(x.get("day_pnl") or 0) > 0]
    losses = [x for x in scored if float(x.get("day_pnl") or 0) < 0]
    week = items[:5]
    week_scored = [x for x in week if x.get("day_pnl") is not None]
    week_wins = [x for x in week_scored if float(x.get("day_pnl") or 0) > 0]

    def _rate(w: list, n: list) -> float | None:
        if not n:
            return None
        return round(100.0 * len(w) / len(n), 1)

    return {
        "ok": True,
        "trade_date": today,
        "items": items,
        "day_win_rate": _rate(wins, scored),
        "day_n": len(scored),
        "day_wins": len(wins),
        "day_losses": len(losses),
        "week_win_rate": _rate(week_wins, week_scored),
        "week_n": len(week_scored),
        "week_wins": len(week_wins),
        "note": "展示向：按日盈亏>0计胜；不进入仓位热度/adapt。",
    }
