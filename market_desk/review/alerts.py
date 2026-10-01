"""Price-touch alerts, chase cost, ready monitor, and session hint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from market_desk.db import load_signals_for_date
from market_desk.filters import normalize_code
from market_desk.numbers import num
from market_desk.settings import setting

from market_desk.review.signals import is_buy_signal

try:
    from zoneinfo import ZoneInfo

    _CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    _CN_TZ = timezone(timedelta(hours=8))


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


def _above_plan_pct(
    sig_type: Any,
    last: float | None,
    plan: float | None,
    flags: list[str],
) -> float | None:
    """Return the live premium over plan (%) when a buy row is being chased.

    Only buy signals whose live price sits at least ``REVIEW_ABOVE_PLAN_WARN_PCT``
    above plan qualify; stop / chase-cap hits keep their own stronger caution.
    """
    from market_desk.config import REVIEW_ABOVE_PLAN_WARN_PCT

    if not is_buy_signal(sig_type) or last is None or plan is None or plan <= 0:
        return None
    if "stop_hit" in flags or "chase_hit" in flags:
        return None
    pct = (float(last) / float(plan) - 1.0) * 100.0
    if pct < float(REVIEW_ABOVE_PLAN_WARN_PCT):
        return None
    return round(pct, 2)


def signal_plan_price(row: dict[str, Any]) -> float | None:
    """Return the locked buy plan price (payload plan_price, else row price)."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    plan = num(row.get("plan_price") if row.get("plan_price") is not None else payload.get("plan_price"))
    if plan is None:
        plan = num(row.get("price"))
    return plan if plan is not None and plan > 0 else None


def chase_pct(plan: Any, fill: Any) -> float | None:
    """Return fill vs plan in percent (positive = paid above plan), or None."""
    p, f = num(plan), num(fill)
    if p is None or f is None or p <= 0 or f <= 0:
        return None
    return round((f / p - 1.0) * 100.0, 2)


def build_chase_cost(
    rows: list[dict[str, Any]],
    *,
    days: int | None = None,
) -> dict[str, Any]:
    """Summarize how far the user's buy fills sat above the plan price.

    Uses rows already overlaid with the viewer's ``signal_user_meta`` (traded +
    fill_price). ``cost_d3`` is the D+3 gap between plan-based and fill-based
    returns, i.e. what the chase cost in percentage points.
    """
    from market_desk.config import CHASE_COST_DAYS, CHASE_WARN_PCT

    n_days = int(days or CHASE_COST_DAYS)
    fills: list[dict[str, Any]] = []
    for r in rows or []:
        if not is_buy_signal(r.get("signal_type")) or not int(r.get("traded") or 0):
            continue
        plan = signal_plan_price(r)
        fill = num(r.get("fill_price"))
        pct = chase_pct(plan, fill)
        if pct is None:
            continue
        fills.append({"row": r, "plan": plan, "fill": fill, "chase": pct})
    day_keys = sorted({str(x["row"].get("trade_date") or "")[:10] for x in fills}, reverse=True)[:n_days]
    keep = set(day_keys)
    fills = [x for x in fills if str(x["row"].get("trade_date") or "")[:10] in keep]
    if not fills:
        return {"ok": False, "n": 0, "items": [], "note": "暂无带成交价的买入记录"}
    chases = sorted(x["chase"] for x in fills)
    mid = len(chases) // 2
    median = chases[mid] if len(chases) % 2 else (chases[mid - 1] + chases[mid]) / 2.0
    warn = float(CHASE_WARN_PCT)
    costs: list[float] = []
    for x in fills:
        d3 = num(x["row"].get("outcome_day3_pct"))
        if d3 is None:
            continue
        plan_d3 = ((1.0 + d3 / 100.0) * (float(x["fill"]) / float(x["plan"])) - 1.0) * 100.0
        costs.append(plan_d3 - d3)
    items = sorted(
        fills,
        key=lambda x: (str(x["row"].get("trade_date") or ""), x["chase"]),
        reverse=True,
    )[:12]
    mean = sum(chases) / len(chases)
    n_warn = sum(1 for c in chases if c >= warn)
    return {
        "ok": True,
        "n": len(chases),
        "days": len(day_keys),
        "mean": round(mean, 2),
        "median": round(median, 2),
        "warn_pct": warn,
        "n_warn": n_warn,
        "cost_d3": round(sum(costs) / len(costs), 2) if costs else None,
        "cost_n": len(costs),
        "tone": "bad" if mean >= warn else ("mid" if mean >= 0.5 else "good"),
        "items": [
            {
                "trade_date": str(x["row"].get("trade_date") or "")[:10],
                "code": x["row"].get("code"),
                "name": x["row"].get("name"),
                "signal_type": x["row"].get("signal_type"),
                "plan": round(float(x["plan"]), 3),
                "fill": round(float(x["fill"]), 3),
                "chase": x["chase"],
            }
            for x in items
        ],
        "note": f"近 {len(day_keys)} 个交易日 {len(chases)} 笔成交：平均高于计划价 {mean:+.2f}%，"
        f"≥{warn:g}% 的 {n_warn} 笔",
    }


def _ever_ready(row: dict[str, Any]) -> bool:
    """Return True when the buy lit the ready flag at any refresh that day."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return bool(payload.get("ever_ready") or payload.get("first_ready_at") or int(row.get("ready") or 0))


def build_ready_monitor(
    rows: list[dict[str, Any]],
    *,
    days: int | None = None,
) -> dict[str, Any]:
    """Compare ever-ready buys against all scored buys of the same trade day.

    Paper 3-day outcomes over the most recent ``days`` scored trade dates.
    Same-day baselining removes the market-day effect, which otherwise dominates
    any raw ready-vs-not comparison. Display only; never gates signals.

    Args:
        rows: Signal rows (any order); non-buy or unscored rows are ignored.
        days: Rolling window in scored trade dates (defaults to config).

    Returns:
        Dict with ``ok``, sample sizes, ``win_diff`` (pp), ``excess`` (%),
        ``tone`` (good | bad | mid | low), a one-line ``note`` and ``by_day``.
    """
    from market_desk.config import (
        REVIEW_READY_MONITOR_DAYS,
        REVIEW_READY_MONITOR_EXCESS_PCT,
        REVIEW_READY_MONITOR_MIN_N,
    )

    window = int(days or REVIEW_READY_MONITOR_DAYS)
    by_day: dict[str, list[tuple[float, bool]]] = {}
    for r in rows or []:
        if not is_buy_signal(r.get("signal_type")):
            continue
        d3 = num(r.get("outcome_day3_pct"))
        day = str(r.get("trade_date") or "")
        if d3 is None or not day:
            continue
        by_day.setdefault(day, []).append((float(d3), _ever_ready(r)))
    picked = sorted(by_day)[-window:]
    lit_d3: list[float] = []
    ex: list[float] = []
    wex: list[float] = []
    peer_n = 0
    day_rows: list[dict[str, Any]] = []
    for day in picked:
        items = by_day[day]
        day_mean = sum(v for v, _ in items) / len(items)
        day_win = sum(1 for v, _ in items if v > 0) / len(items)
        lit = [v for v, on in items if on]
        peer_n += len(items) - len(lit)
        if not lit:
            continue
        for v in lit:
            lit_d3.append(v)
            ex.append(v - day_mean)
            wex.append((1.0 if v > 0 else 0.0) - day_win)
        day_rows.append({
            "date": day,
            "n": len(lit),
            "win": sum(1 for v in lit if v > 0),
            "d3": round(sum(lit) / len(lit), 2),
            "day_n": len(items),
            "day_d3": round(day_mean, 2),
        })
    n = len(lit_d3)
    out: dict[str, Any] = {
        "ok": n > 0,
        "days": len(picked),
        "n": n,
        "lit_days": len(day_rows),
        "peer_n": peer_n,
        "win3": round(100.0 * sum(1 for v in lit_d3 if v > 0) / n, 1) if n else None,
        "d3": round(sum(lit_d3) / n, 2) if n else None,
        "win_diff": round(100.0 * sum(wex) / n, 1) if n else None,
        "excess": round(sum(ex) / n, 2) if n else None,
        "by_day": day_rows[-10:],
    }
    if not n:
        out.update(tone="low", note=f"近 {len(picked)} 个有三日结果的交易日没有亮过可买的买点")
        return out
    head = (
        f"近 {len(picked)} 日亮过可买 {n} 只（{len(day_rows)} 天）"
        f" · 同日胜率差 {out['win_diff']:+.1f}pp · 同日超额 {out['excess']:+.2f}%"
    )
    thr = float(REVIEW_READY_MONITOR_EXCESS_PCT)
    if n < int(REVIEW_READY_MONITOR_MIN_N):
        tone, verdict = "low", "样本不足，先看不下结论"
    elif out["excess"] <= -thr and out["win_diff"] < 0:
        tone, verdict = "bad", "偏弱：亮灯后买没跑赢同日其他买点"
    elif out["excess"] >= thr and out["win_diff"] > 0:
        tone, verdict = "good", "有效：亮灯后买跑赢同日其他买点"
    else:
        tone, verdict = "mid", "持平：和同日其他买点差不多"
    out.update(tone=tone, note=f"{head} · {verdict}")
    return out


def build_review_session_hint(
    day_rows: list[dict[str, Any]],
    *,
    is_today: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the review-page banner: session window, today's buy counts, cautions.

    Display only; the banner never blocks or rewrites any signal.
    """
    from market_desk.calendar import is_trading_day
    from market_desk.config import REVIEW_ABOVE_PLAN_WARN_PCT

    if not is_today:
        return {"ok": False}
    now = now or datetime.now(_CN_TZ)
    hhmm = now.strftime("%H:%M")
    trading = is_trading_day(now)
    if not trading:
        session, level = "非交易日", "info"
    elif hhmm < "09:15":
        session, level = "盘前", "info"
    elif hhmm < "11:30":
        session, level = "上午盘", "info"
    elif hhmm < "13:00":
        session, level = "午休", "info"
    elif hhmm < "14:00":
        session, level = "午后盘", "info"
    elif hhmm < "15:00":
        session, level = "尾盘段", "info"
    else:
        session, level = "已收盘", "info"
    buys = [r for r in day_rows or [] if is_buy_signal(r.get("signal_type"))]
    above_n = sum(1 for r in buys if "above_plan" in (r.get("price_flags") or []))
    tips: list[str] = []
    if above_n:
        tips.append(
            f"{above_n} 条买点现价已高于计划价 {REVIEW_ABOVE_PLAN_WARN_PCT:g}% 以上，只按计划价挂单"
        )
    return {
        "ok": True,
        "now": hhmm,
        "session": session,
        "level": level,
        "buy_n": len(buys),
        "above_plan_n": above_n,
        "tips": tips,
    }
