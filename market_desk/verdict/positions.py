"""Position mark-to-market, day P&L, summaries, risk overview, and deltas."""

from __future__ import annotations

from typing import Any
from market_desk.config import TRADE_FEE_CNY
from market_desk.filters import normalize_code


def quote_prev_close(
    quote: dict[str, Any] | None,
    *,
    last: float | None = None,
) -> float | None:
    """Return yesterday's close from a quote, recovering via pct when needed."""
    q = quote or {}
    prev = q.get("prev")
    try:
        if prev not in (None, "", 0):
            return float(prev)
    except (TypeError, ValueError):
        pass
    pct_q = q.get("pct")
    mark = last if last is not None else q.get("price")
    if mark is None or pct_q is None:
        return None
    try:
        pct_f = float(pct_q)
        if pct_f <= -99.999:
            return None
        return float(mark) / (1.0 + pct_f / 100.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def position_day_anchor(
    buy_price: float,
    *,
    buy_day: str | None,
    trade_day: str | None,
    prev_close: float | None,
) -> float | None:
    """Pick the session P&L anchor: buy price if bought today, else 昨收."""
    day = str(trade_day or "").strip()[:10]
    bday = str(buy_day or "").strip()[:10]
    buy = float(buy_price or 0)
    if day and bday and bday == day and buy > 0:
        return buy
    try:
        if prev_close not in (None, "") and float(prev_close) > 0:
            return float(prev_close)
    except (TypeError, ValueError):
        pass
    return buy if buy > 0 else None


def session_sell_realized(
    sell_price: float | None,
    sold_qty: int,
    *,
    buy_price: float,
    buy_day: str | None,
    trade_day: str | None,
    prev_close: float | None,
    fee: float | None = None,
) -> float:
    """Compute today's realized P&L for shares sold today (vs day anchor − sell fee).

    Overnight lots use 昨收 as the anchor so overnight gains are not counted as
    today's P&L. Lots bought today use the buy price. A flat sell commission is
    deducted once when ``sold_qty > 0`` (buy commission is applied separately).
    """
    qty = int(sold_qty or 0)
    if qty <= 0 or sell_price in (None, ""):
        return 0.0
    try:
        px = float(sell_price)
    except (TypeError, ValueError):
        return 0.0
    anchor = position_day_anchor(
        float(buy_price or 0),
        buy_day=buy_day,
        trade_day=trade_day,
        prev_close=prev_close,
    )
    if anchor is None or anchor <= 0:
        return 0.0
    fee_v = float(TRADE_FEE_CNY if fee is None else fee)
    if fee_v < 0:
        fee_v = 0.0
    return round((px - float(anchor)) * qty - fee_v, 2)


def session_trade_fees(
    *,
    bought_today: bool,
    sold_today: bool,
    fee: float | None = None,
) -> float:
    """Return flat commissions: once for today's buy and once for today's sell."""
    fee_v = float(TRADE_FEE_CNY if fee is None else fee)
    if fee_v < 0:
        fee_v = 0.0
    n = (1 if bought_today else 0) + (1 if sold_today else 0)
    return round(fee_v * n, 2)


def decorate_positions(
    rows: list[dict[str, Any]],
    quotes: dict[str, Any],
    *,
    trade_date: str | None = None,
    boards: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach mark-to-market and day-realized fields used by the position tab.

    Day MTM prefers open FIFO lots: today-bought legs vs buy price, overnight
    legs vs 昨收 — so averaging into a bag does not re-anchor the whole row.
    Multi-trim sells use ``day_sell_notional`` for a volume-weighted sell price
    when present.
    """
    from market_desk.review import lookup_code_boards

    day = str(trade_date or "").strip()[:10]
    board_pool = list(boards or [])
    fee = float(TRADE_FEE_CNY)
    # Prefetch lots so day_mtm can split today vs overnight legs.
    lots_map: dict[int, list[dict[str, Any]]] = {}
    try:
        from market_desk.db import load_lots_for_positions

        ids = [int(r["id"]) for r in rows if r.get("id") is not None]
        lots_map = load_lots_for_positions(ids)
    except Exception:
        lots_map = {}

    out: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code") or "").zfill(6)
        q = quotes.get(code) or {}
        last = q.get("price")
        buy = float(row.get("buy_price") or 0)
        qty = int(row.get("qty") or 0)
        closed_date = str(row.get("closed_date") or "").strip()[:10] or None
        closed = qty <= 0 and bool(closed_date)
        sell_px = row.get("last_sell_price")
        day_sold = int(row.get("day_sold_qty") or 0)
        sell_day = str(row.get("last_sell_date") or "")[:10]
        day_notional = float(row.get("day_sell_notional") or 0)
        if day and sell_day and sell_day != day:
            day_sold = 0
            sell_px = None
            day_notional = 0.0
        buy_day = str(row.get("last_buy_date") or row.get("created_at") or "")[:10]
        pid = int(row.get("id") or 0)
        lots = lots_map.get(pid) or []
        today_lot_qty = 0
        if lots and day:
            today_lot_qty = sum(
                int(l.get("qty") or 0)
                for l in lots
                if str(l.get("buy_date") or "")[:10] == day
            )
        # Any today lot → charge one buy fee; do not require whole-row last_buy_date.
        bought_today = bool(
            (lots and today_lot_qty > 0)
            or (not lots and day and buy_day and buy_day == day)
        )
        sold_today = day_sold > 0
        buy_fee = fee if bought_today else 0.0
        sell_fee = fee if sold_today else 0.0
        trade_fee = session_trade_fees(
            bought_today=bought_today, sold_today=sold_today, fee=fee
        )
        prev = quote_prev_close(q, last=float(last) if last is not None else None)
        # Prefer VWAP from day_sell_notional when multi-trim; else last_sell_price.
        eff_sell_px = sell_px
        if sold_today and day_sold > 0 and day_notional > 0:
            try:
                eff_sell_px = float(day_notional) / float(day_sold)
            except (TypeError, ValueError, ZeroDivisionError):
                eff_sell_px = sell_px
        sell_realized = (
            session_sell_realized(
                eff_sell_px,
                day_sold,
                buy_price=buy,
                buy_day=buy_day if not lots else (
                    day if today_lot_qty >= day_sold else "1970-01-01"
                ),
                trade_day=day,
                prev_close=prev,
                fee=fee,
            )
            if sold_today
            else 0.0
        )
        # When lots mix today+overnight and we sold, VWAP path above still uses a
        # single buy_day; prefer cumulative trim store if it looks multi-leg.
        stored_realized = float(row.get("day_realized_pnl") or 0)
        if (
            sold_today
            and not closed
            and day_sold > 0
            and day_notional > 0
            and stored_realized != 0
        ):
            # Trim path already accumulated chunk P&L (− first sell fee).
            sell_realized = stored_realized
        day_realized = (
            round(sell_realized - buy_fee, 2) if (closed and sold_today) else sell_realized
        )
        if closed:
            mark = float(eff_sell_px) if eff_sell_px not in (None, "") else (
                float(last) if last is not None else buy
            )
            sold_qty = day_sold or int(row.get("day_sold_qty") or 0)
            cost = round(buy * sold_qty, 2) if sold_qty else None
            market = round(mark * sold_qty, 2) if sold_qty and mark is not None else None
            try:
                gross_total = (float(mark) - buy) * sold_qty if sold_qty and mark is not None else None
            except (TypeError, ValueError):
                gross_total = None
            closed_fee = sell_fee + buy_fee
            pnl = (
                round(float(gross_total) - closed_fee, 2)
                if gross_total is not None
                else day_realized
            )
            pnl_pct = round((mark / buy - 1.0) * 100.0, 2) if mark and buy else None
            last = mark
            day_pnl = day_realized if sold_today else None
            # Closed day_pnl_pct vs session anchor (昨收 if overnight, else buy).
            if day_pnl is not None and sold_qty > 0:
                anchor = buy if bought_today else (float(prev) if prev not in (None, 0, "") else buy)
                basis = abs(float(anchor) * sold_qty)
                day_pnl_pct_row = (
                    round(100.0 * float(day_pnl) / basis, 2) if basis > 0 else None
                )
            else:
                day_pnl_pct_row = None
        else:
            day_pnl_pct_row = None
            cost = round(buy * qty, 2)
            market = round(last * qty, 2) if last is not None else None
            pnl = (
                round(market - cost - buy_fee, 2)
                if market is not None
                else None
            )
            pnl_pct = round((last / buy - 1.0) * 100.0, 2) if last and buy else None
            pct_q = q.get("pct")
            day_mtm = None
            try:
                if last is not None and qty > 0 and lots and day:
                    day_mtm = 0.0
                    for lot in lots:
                        lq = int(lot.get("qty") or 0)
                        if lq <= 0:
                            continue
                        lpx = float(lot.get("buy_price") or 0)
                        ld = str(lot.get("buy_date") or "")[:10]
                        if ld == day and lpx > 0:
                            day_mtm += (float(last) - lpx) * lq
                        elif prev not in (None, 0, ""):
                            day_mtm += (float(last) - float(prev)) * lq
                        elif pct_q is not None and prev not in (None, 0, ""):
                            day_mtm += float(prev) * lq * float(pct_q) / 100.0
                elif last is not None and qty > 0 and bought_today and buy > 0:
                    day_mtm = (float(last) - buy) * qty
                elif last is not None and prev not in (None, 0, "") and qty > 0:
                    day_mtm = (float(last) - float(prev)) * qty
                elif pct_q is not None and prev not in (None, 0, "") and qty > 0 and not bought_today:
                    day_mtm = float(prev) * qty * float(pct_q) / 100.0
            except (TypeError, ValueError):
                day_mtm = None
            if day_mtm is not None or sold_today or bought_today:
                # When sell_realized already came from trim store, it embeds sell fee;
                # still subtract buy_fee once for today buys.
                if sold_today and not closed and day_notional > 0 and stored_realized != 0:
                    day_pnl = round(float(day_mtm or 0.0) + sell_realized - buy_fee, 2)
                else:
                    day_pnl = round((day_mtm or 0.0) + sell_realized - buy_fee, 2)
            else:
                day_pnl = None
        item = dict(row)
        item["code"] = code
        item["name"] = row.get("name") or q.get("name") or code
        item["last"] = last
        item["last_pct"] = None if closed else q.get("pct")
        item["high"] = q.get("high")
        item["low"] = q.get("low")
        item["open"] = None if closed else q.get("open")
        item["prev"] = None if closed else prev
        item["bought_today"] = False if closed else bought_today
        item["today_lot_qty"] = today_lot_qty if not closed else 0
        item["cost"] = cost
        item["market"] = market
        item["pnl"] = pnl
        item["pnl_pct"] = pnl_pct
        item["day_pnl"] = day_pnl
        if closed and day_pnl_pct_row is not None:
            item["day_pnl_pct"] = day_pnl_pct_row
        item["closed"] = closed
        item["closed_date"] = closed_date
        item["day_sold_qty"] = day_sold
        item["day_realized_pnl"] = day_realized
        item["day_sell_notional"] = day_notional if sold_today else 0.0
        item["avg_sell_price"] = (
            round(float(eff_sell_px), 4)
            if sold_today and eff_sell_px not in (None, "")
            else None
        )
        item["trade_fee"] = trade_fee
        item["buy_fee"] = buy_fee
        item["sell_fee"] = sell_fee
        item["status"] = "今日已平" if closed else ("部分兑现" if day_sold > 0 else "持仓")
        names = lookup_code_boards(code, board_pool) if board_pool else []
        entry = str(row.get("entry_board") or "").strip()
        if entry and entry not in names:
            names = [entry] + names
        item["board_names"] = names[:4]
        item["board"] = names[0] if names else (entry or None)
        item["entry_board"] = entry or None
        if not closed and qty > 0:
            peak_vals = [buy]
            stored = row.get("peak_price")
            try:
                if stored not in (None, "") and float(stored) > 0:
                    peak_vals.append(float(stored))
            except (TypeError, ValueError):
                pass
            for raw in (q.get("high"), last):
                try:
                    if raw not in (None, "") and float(raw) > 0:
                        peak_vals.append(float(raw))
                except (TypeError, ValueError):
                    pass
            item["peak_price"] = round(max(peak_vals), 4)
            item["peak_dirty"] = (
                stored in (None, "")
                or abs(float(item["peak_price"]) - float(stored or 0)) > 1e-6
            )
        else:
            item["peak_price"] = row.get("peak_price")
            item["peak_dirty"] = False
        item["lots"] = lots
        item["lot_count"] = len(lots)
        out.append(item)
    return out


def attach_position_daily_trends(
    positions: list[dict[str, Any]] | None,
    trends_by_code: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Attach daily-trend labels onto position rows for the 仓位 tab / sell cards."""
    trends = trends_by_code or {}
    out: list[dict[str, Any]] = []
    for row in positions or []:
        item = dict(row)
        code = normalize_code(item.get("code"))
        trend = trends.get(code) if code else None
        if not trend:
            item.setdefault("daily_trend", None)
            item.setdefault("trend_ok", False)
            item.setdefault("trend_down", False)
            out.append(item)
            continue
        label = trend.get("label")
        item["daily_trend"] = label
        item["daily_trend_zh"] = label
        item["trend"] = label
        item["trend_ok"] = bool(trend.get("up")) and not bool(trend.get("down"))
        item["trend_down"] = bool(trend.get("down"))
        item["trend_pending"] = trend.get("quality") in ("fetch_fail", "thin")
        item["ma5"] = trend.get("ma5")
        item["ma10"] = trend.get("ma10")
        item["ma20"] = trend.get("ma20")
        out.append(item)
    return out


def position_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate open book and today realized P&L for the position tab."""
    from market_desk.config import (
        POSITION_MAX_NAMES,
        POSITION_MAX_SINGLE_PCT,
        POSITION_MAX_TOTAL_COST,
    )

    open_rows = [r for r in rows if int(r.get("qty") or 0) > 0]
    closed_rows = [r for r in rows if int(r.get("qty") or 0) <= 0]
    cost = sum(float(r.get("cost") or 0) for r in open_rows)
    marked = [r for r in open_rows if r.get("market") is not None]
    market = sum(float(r.get("market") or 0) for r in marked)
    floating = round(market - cost, 2) if marked else None
    floating_pct = round((market / cost - 1.0) * 100.0, 2) if marked and cost else None
    realized = round(sum(float(r.get("day_realized_pnl") or 0) for r in rows), 2)
    # Day P&L = sum of row day_pnl (vs 昨收/今日买价 + 已实现), NOT floating+realized.
    day_parts = [r for r in rows if r.get("day_pnl") is not None]
    day_total = (
        round(sum(float(r.get("day_pnl") or 0) for r in day_parts), 2) if day_parts else None
    )
    day_total_pct = None
    day_basis = 0.0
    for r in open_rows:
        qty = int(r.get("qty") or 0)
        if qty <= 0:
            continue
        buy = float(r.get("buy_price") or 0)
        prev = r.get("prev")
        if r.get("bought_today") and buy > 0:
            day_basis += buy * qty
        elif prev not in (None, 0, "") and float(prev) > 0:
            day_basis += float(prev) * qty
        elif buy > 0:
            day_basis += buy * qty
    for r in closed_rows:
        sold = int(r.get("day_sold_qty") or 0)
        buy = float(r.get("buy_price") or 0)
        if sold > 0 and buy > 0:
            day_basis += buy * sold
    if day_total is not None and day_basis > 0:
        day_total_pct = round(day_total / day_basis * 100.0, 2)
    notes: list[str] = []
    if len(open_rows) > POSITION_MAX_NAMES:
        notes.append(f"持仓只数 {len(open_rows)} 超过软上限 {POSITION_MAX_NAMES}")
    if cost > POSITION_MAX_TOTAL_COST:
        notes.append(f"总成本 {cost:.0f} 超过软上限 {POSITION_MAX_TOTAL_COST:.0f}")
    if market > 0:
        for r in marked:
            share = float(r.get("market") or 0) / market * 100.0
            if share >= POSITION_MAX_SINGLE_PCT:
                notes.append(
                    f"{r.get('name') or r.get('code')} 占比 {share:.0f}% ≥ {POSITION_MAX_SINGLE_PCT:.0f}%"
                )
    return {
        "count": len(open_rows),
        "closed_count": len(closed_rows),
        "cost": round(cost, 2),
        "market": round(market, 2) if marked else None,
        "pnl": floating,
        "pnl_pct": floating_pct,
        "floating_pnl": floating,
        "floating_pct": floating_pct,
        "realized_pnl": realized,
        "day_pnl": day_total,
        "day_pnl_pct": day_total_pct,
        "priced": len(marked),
        "risk_note": "；".join(notes) if notes else "",
    }


def build_risk_overview(
    rows: list[dict[str, Any]],
    *,
    size_cap_pct: float | None = None,
) -> dict[str, Any]:
    """Build a richer risk panel: weights, theme concentration, soft caps."""
    from market_desk.config import (
        POSITION_MAX_NAMES,
        POSITION_MAX_SINGLE_PCT,
        POSITION_MAX_THEME_PCT,
        POSITION_MAX_TOTAL_COST,
    )
    from market_desk.mainline import theme_key
    from market_desk.settings import setting

    open_rows = [r for r in rows if int(r.get("qty") or 0) > 0]
    base = position_summary(rows)
    market = float(base.get("market") or 0)
    cost = float(base.get("cost") or 0)
    target_cost = float(setting("target_total_cost", POSITION_MAX_TOTAL_COST))
    equal_w = bool(setting("equal_weight_target", True))
    loss_cap = float(setting("daily_loss_cap_pct", -3.0))
    cool_n = int(setting("cool_after_losses", 3))
    equity = float(setting("account_equity", 50000) or 0)
    equal_share = round(100.0 / len(open_rows), 1) if equal_w and open_rows else None
    items: list[dict[str, Any]] = []
    for r in open_rows:
        mkt = float(r.get("market") or 0) if r.get("market") is not None else None
        weight = round(mkt / market * 100.0, 1) if market > 0 and mkt is not None else None
        weight_dev = (
            round(weight - equal_share, 1)
            if weight is not None and equal_share is not None
            else None
        )
        board = str(r.get("board") or r.get("entry_board") or "").strip()
        items.append(
            {
                "id": r.get("id"),
                "code": r.get("code"),
                "name": r.get("name"),
                "cost": r.get("cost"),
                "market": r.get("market"),
                "pnl": r.get("pnl"),
                "pnl_pct": r.get("pnl_pct"),
                "day_realized_pnl": r.get("day_realized_pnl"),
                "weight_pct": weight,
                "target_weight_pct": equal_share,
                "weight_dev_pct": weight_dev,
                "over_weight": bool(weight is not None and weight >= POSITION_MAX_SINGLE_PCT),
                "board": board or None,
                "theme": theme_key(board) if board else "",
            }
        )
    items.sort(key=lambda x: float(x.get("weight_pct") or 0), reverse=True)

    # Theme concentration: group by theme_key, fall back to raw board / 未标.
    theme_buckets: dict[str, dict[str, Any]] = {}
    for r in open_rows:
        board = str(r.get("board") or r.get("entry_board") or "").strip()
        tk = theme_key(board) if board else ""
        key = tk or (board or "_none")
        label = tk or board or "未标题材"
        mkt = float(r.get("market") or 0) if r.get("market") is not None else 0.0
        bucket = theme_buckets.setdefault(
            key,
            {"theme": label, "market": 0.0, "names": [], "codes": []},
        )
        bucket["market"] += mkt
        nm = str(r.get("name") or r.get("code") or "")
        if nm and nm not in bucket["names"]:
            bucket["names"].append(nm)
        code = str(r.get("code") or "")
        if code and code not in bucket["codes"]:
            bucket["codes"].append(code)
    themes: list[dict[str, Any]] = []
    for bucket in theme_buckets.values():
        tw = (
            round(float(bucket["market"]) / market * 100.0, 1)
            if market > 0 and bucket["market"]
            else None
        )
        themes.append(
            {
                "theme": bucket["theme"],
                "weight_pct": tw,
                "market": round(float(bucket["market"]), 2),
                "n": len(bucket["codes"]),
                "names": bucket["names"][:4],
                "over_theme": bool(tw is not None and tw >= POSITION_MAX_THEME_PCT),
            }
        )
    themes.sort(key=lambda x: float(x.get("weight_pct") or 0), reverse=True)
    top_theme = themes[0] if themes else None
    top_single = items[0] if items else None

    winners = sum(1 for r in open_rows if (r.get("pnl_pct") or 0) > 0)
    losers = sum(1 for r in open_rows if (r.get("pnl_pct") or 0) < 0)
    day_pct = base.get("day_pnl_pct")
    if day_pct is None:
        day_pct = base.get("pnl_pct")
    target_dev = round((cost / target_cost - 1.0) * 100.0, 1) if target_cost > 0 else None
    loss_cap_hit = bool(day_pct is not None and float(day_pct) <= loss_cap)
    cool_hit = bool(losers >= cool_n)
    used_pct = (cost / equity * 100.0) if equity > 0 else None
    cap = float(size_cap_pct) if size_cap_pct is not None else None
    size_cap_hit = bool(cap is not None and used_pct is not None and used_pct >= cap)
    tips: list[str] = []
    if size_cap_hit:
        tips.append(
            f"总成本约占账户 {used_pct:.0f}% ≥ 相位仓位上限 {cap:.0f}%，建议先减不加"
        )
    if loss_cap_hit:
        tips.append(
            f"今日盈亏已触及单日亏损帽 {loss_cap:g}%（当前 {day_pct}%），建议停手、只减不加"
        )
    if cool_hit:
        tips.append(f"浮亏标的 {losers} 只 ≥ 连亏降温阈值 {cool_n}，建议手数×0.75、先冷静再开新仓")
    if target_dev is not None and abs(target_dev) >= 15:
        tips.append(f"总成本相对目标 {target_cost:.0f} 偏差 {target_dev:+.1f}%")
    if int(base.get("closed_count") or 0):
        tips.append(f"今日已平 {base['closed_count']} 只，已实现 {base.get('realized_pnl')}，次日自动移出")
    if top_theme and top_theme.get("over_theme"):
        tips.append(
            f"题材「{top_theme.get('theme')}」约占市值 {top_theme.get('weight_pct')}%"
            f"（≥{POSITION_MAX_THEME_PCT:g}%），注意别押在假主线"
        )
    if top_single and top_single.get("over_weight"):
        tips.append(
            f"单票「{top_single.get('name') or top_single.get('code')}」"
            f"约占 {top_single.get('weight_pct')}% ≥ {POSITION_MAX_SINGLE_PCT:g}%"
        )
    for it in items:
        if it.get("weight_dev_pct") is not None and abs(float(it["weight_dev_pct"])) >= 12:
            tip = (
                f"{it.get('name') or it.get('code')} 相对等权偏差 "
                f"{float(it['weight_dev_pct']):+.1f}%"
            )
            if tip not in tips:
                tips.append(tip)
            if len(tips) >= 8:
                break
    return {
        **base,
        "items": items,
        "themes": themes,
        "top_theme": top_theme,
        "top_single": {
            "name": top_single.get("name") if top_single else None,
            "code": top_single.get("code") if top_single else None,
            "weight_pct": top_single.get("weight_pct") if top_single else None,
            "over_weight": bool(top_single.get("over_weight")) if top_single else False,
        }
        if top_single
        else None,
        "winners": winners,
        "losers": losers,
        "flat": max(0, len(open_rows) - winners - losers),
        "caps": {
            "max_names": POSITION_MAX_NAMES,
            "max_single_pct": POSITION_MAX_SINGLE_PCT,
            "max_theme_pct": POSITION_MAX_THEME_PCT,
            "max_total_cost": POSITION_MAX_TOTAL_COST,
        },
        "target_total_cost": target_cost,
        "target_dev_pct": target_dev,
        "equal_weight_target": equal_w,
        "daily_loss_cap_pct": loss_cap,
        "cool_after_losses": cool_n,
        "loss_cap_hit": loss_cap_hit,
        "cool_hit": cool_hit,
        "size_cap_pct": cap,
        "size_used_pct": None if used_pct is None else round(used_pct, 1),
        "size_cap_hit": size_cap_hit,
        "tips": tips,
    }


def build_deltas(current: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Compare key gauges with the previous refresh."""
    prev_m = (previous or {}).get("metrics") or {}
    cur_m = current.get("metrics") or {}
    prev_c = ((previous or {}).get("verdict") or {}).get("carrier") or {}
    cur_c = (current.get("verdict") or {}).get("carrier") or {}
    prev_ml = ((previous or {}).get("verdict") or {}).get("mainline") or {}
    cur_ml = (current.get("verdict") or {}).get("mainline") or {}
    items = [
        _delta("温度", current.get("temperature"), previous.get("temperature") if previous else None, 0),
        _delta("涨停", cur_m.get("zt"), prev_m.get("zt"), 0),
        _delta("跌停", cur_m.get("dt"), prev_m.get("dt"), 0, invert=True),
        _delta("炸板%", cur_m.get("zb_rate"), prev_m.get("zb_rate"), 1, invert=True),
        _delta("晋级%", cur_m.get("promotion"), prev_m.get("promotion"), 1),
        _delta("1→2%", cur_m.get("promo_1_2"), prev_m.get("promo_1_2"), 1),
        _delta("2→3%", cur_m.get("promo_2_3"), prev_m.get("promo_2_3"), 1),
        _delta("溢价%", cur_m.get("premium"), prev_m.get("premium"), 2),
        _delta("主线", cur_ml.get("pct"), prev_ml.get("pct"), 2, unit="%"),
        _delta("载体", cur_c.get("pct"), prev_c.get("pct"), 2, unit="%"),
    ]
    return items


def _delta(
    label: str,
    cur: float | None,
    prev: float | None,
    digits: int,
    unit: str = "",
    invert: bool = False,
) -> dict[str, Any]:
    if cur is None:
        return {"label": label, "value": None, "delta": None, "arrow": "→", "dir": "flat", "unit": unit}
    value = round(float(cur), digits)
    if prev is None:
        return {
            "label": label,
            "value": value,
            "delta": None,
            "arrow": "→",
            "dir": "flat",
            "unit": unit,
            "text": "较上轮 —",
        }
    raw = float(cur) - float(prev)
    delta = round(raw, digits)
    if abs(raw) < 10 ** (-max(digits, 1)):
        arrow, direction = "→", "flat"
    elif raw > 0:
        arrow, direction = "↑", "up"
    else:
        arrow, direction = "↓", "down"
    better = direction == "down" if invert else direction == "up"
    tone = "flat" if direction == "flat" else ("good" if better else "bad")
    if direction == "flat":
        text = "较上轮 持平"
    else:
        sign = "+" if delta > 0 else ""
        text = f"较上轮 {arrow}{sign}{delta}{unit}"
    return {
        "label": label,
        "value": value,
        "delta": delta,
        "arrow": arrow,
        "dir": direction,
        "tone": tone,
        "unit": unit,
        "text": text,
    }
