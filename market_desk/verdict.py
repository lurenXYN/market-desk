"""Live mainline verdict, recommendation, and snapshot-to-snapshot deltas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.config import CHINEXT_STAR_ETFS
from market_desk.filters import is_limit_up, is_main_board, is_st, normalize_code
from market_desk.mainline import match_mainline_etf, pick_mainline
from market_desk.session import apply_segment_bias, session_segment
from market_desk.trend import classify_daily_trend


def build_verdict(
    now: datetime,
    phase: str,
    metrics: dict[str, Any],
    etfs: list[dict[str, Any]],
    hot: list[dict[str, Any]],
    prev: dict[str, Any] | None,
    zt: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Announce the live mainline and a matching vehicle, without a fixed ticker."""
    main = pick_mainline(hot) or {}
    board_name = main.get("name") or ""
    etf = match_mainline_etf(board_name, etfs) if board_name else None
    vehicle = etf or {}
    price = vehicle.get("price")
    pct = vehicle.get("pct") if vehicle else main.get("pct")
    low = vehicle.get("low")
    bounce = None
    if price is not None and low not in (None, 0):
        bounce = (price / low - 1.0) * 100.0
    prev_px = (((prev or {}).get("verdict") or {}).get("carrier") or {}).get("price")
    falling = price is not None and prev_px is not None and price < prev_px - 1e-6
    status = main.get("status") or ""
    seg = session_segment(now)
    auction_only = seg.get("key") == "auction"

    if not board_name:
        action = "观望"
        reason = "热点板块尚未形成可识别主线"
    elif auction_only:
        action = "观望"
        reason = f"竞价阶段，实时主线看 {board_name}，9:30 后再定价"
    elif status == "退潮":
        action = "观望"
        reason = f"{board_name} 已转弱，主线身份不稳"
    elif status == "尖峰禁追":
        action = "观察回踩"
        reason = f"{board_name} 是当前主线，但已到尖峰，先等回踩再下手"
    elif (
        vehicle.get("price")
        and pct is not None
        and (bounce or 0) >= 0.35
        and not falling
        and status in ("确认中", "观察")
    ):
        action = "可买入"
        reason = f"主线 {board_name}，载体离日低回升且未继续下探"
    elif status == "确认中":
        action = "可买入"
        reason = f"主线确认：{board_name}，按你自己的仓位买对应载体"
    else:
        action = "观察回踩"
        reason = f"实时主线倾向 {board_name}，结构未完全确认"

    action, reason, size_hint = apply_segment_bias(
        action,
        reason,
        segment_key=str(seg.get("key") or "closed"),
        status=status,
        phase=phase,
    )

    headline = f"实时主线 · {board_name or '未明'}"
    meaning = {
        "观望": "主线未明或已退潮，先看不买。",
        "观察回踩": "主线已经认出来了，但过热或未站稳，等回踩。",
        "可买入": "主线确认。优先 ETF（含创业板ETF、科创50ETF）。个股只给主板回踩票，创业/科创个股不推荐。",
    }.get(action, "")
    if size_hint:
        meaning = f"{meaning}（{size_hint}）"
    if vehicle.get("code"):
        detail = (
            f"[{seg.get('label')}] {board_name} · {vehicle.get('name')} {vehicle.get('code')} "
            f"{vehicle.get('price') or '—'} {_fmt_pct(pct)}"
            f"{'，日低回升 ' + _fmt_num(bounce) + '%' if bounce is not None else ''}。{reason}"
        )
    else:
        detail = (
            f"[{seg.get('label')}] {board_name or '—'} { _fmt_pct(main.get('pct')) } · "
            f"总龙头 {main.get('leader_name') or '—'} {main.get('leader_boards') or 0}板。"
            f"{reason}"
        )
    bans = [b["name"] for b in (hot or []) if b.get("status") == "尖峰禁追"][:4]
    stocks = [] if _blocks_chi_star_stocks(board_name) else _stock_candidates(main, zt or [])
    recommend = _build_recommend(action, main, vehicle, bounce, stocks, bans)
    if size_hint and recommend.get("size_note"):
        recommend["size_note"] = f"{size_hint}；{recommend['size_note']}"
    elif size_hint:
        recommend["size_note"] = size_hint
    return {
        "action": action,
        "headline": headline,
        "meaning": meaning,
        "reason": reason,
        "detail": detail,
        "recommend": recommend,
        "mainline": {
            "name": board_name,
            "bk": main.get("bk"),
            "kind": main.get("kind"),
            "status": status,
            "pct": main.get("pct"),
            "zt_n": main.get("zt_n"),
            "leader_name": main.get("leader_name"),
            "leader_code": main.get("leader_code"),
            "leader_boards": main.get("leader_boards"),
        },
        "carrier": {
            "code": vehicle.get("code"),
            "name": vehicle.get("name"),
            "price": price,
            "pct": pct,
            "low": low,
            "high": vehicle.get("high"),
            "bounce": None if bounce is None else round(bounce, 2),
            "falling": falling,
        },
        "bans": bans,
        "auction_only": auction_only,
        "segment": seg,
        "segment_size_hint": size_hint,
        "phase": phase,
        "temperature": metrics.get("zt"),
    }


def apply_stock_daily_trends(
    recommend: dict[str, Any] | None,
    closes_by_code: dict[str, list[float]],
    fetch_ok_by_code: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Attach daily-trend flags to stock cards; keep non-uptrends visible with warnings."""
    rec = dict(recommend or {})
    items = list(rec.get("items") or [])
    if not items:
        return rec
    fetch_ok_by_code = fetch_ok_by_code or {}

    etf_items: list[dict[str, Any]] = []
    up_stocks: list[dict[str, Any]] = []
    bad_stocks: list[dict[str, Any]] = []
    for item in items:
        if item.get("kind") != "stock":
            etf_items.append(item)
            continue
        code = normalize_code(item.get("code"))
        closes = closes_by_code.get(code)
        # Missing key or explicit False → treat as fetch failure.
        if code not in closes_by_code:
            fetch_ok = False
            closes = []
        else:
            fetch_ok = fetch_ok_by_code.get(code, True)
            closes = closes or []
        trend = classify_daily_trend(closes, fetch_ok=fetch_ok)
        marked = dict(item)
        marked["trend"] = trend.get("label")
        marked["trend_ok"] = bool(trend.get("up"))
        marked["trend_down"] = bool(trend.get("down"))
        marked["trend_warn"] = trend.get("warn")
        marked["trend_quality"] = trend.get("quality")
        marked["ma5"] = trend.get("ma5")
        marked["ma10"] = trend.get("ma10")
        marked["ma20"] = trend.get("ma20")
        if trend.get("up"):
            marked["reason"] = (
                f"日线上升趋势（MA5 {trend.get('ma5')} / MA20 {trend.get('ma20')}）；"
                + str(marked.get("reason") or "")
            )
            up_stocks.append(marked)
            continue
        marked["ready"] = False
        marked["trend_warn"] = marked.get("trend_warn") or "不是上升趋势"
        if marked.get("wait_price") is not None:
            marked["buy_price"] = marked.get("wait_price")
        marked["role"] = "watch"
        marked["role_label"] = "个股·非上升 · 不建议买"
        marked["reason"] = (
            f"【不是上升趋势·{trend.get('label') or '日线偏弱'}】"
            + str(marked.get("reason") or "")
        )
        bad_stocks.append(marked)

    # Always show non-uptrend names with a loud badge; never silently drop them.
    merged = etf_items + up_stocks + bad_stocks

    for idx, item in enumerate(merged):
        if item.get("kind") == "stock" and item.get("trend_ok"):
            has_etf = any(x.get("kind") == "etf" for x in merged)
            item["role"] = "alt" if has_etf or idx > 0 else "primary"
            if item.get("ready"):
                item["role_label"] = (
                    "个股 主推" if item.get("role") == "primary" else "个股 备选"
                )

    rec["items"] = merged
    primary = next(
        (
            x
            for x in merged
            if x.get("kind") == "etf" or (x.get("kind") == "stock" and x.get("trend_ok"))
        ),
        None,
    ) or (merged[0] if merged else None)
    if primary and primary.get("kind") == "etf":
        primary["role"] = "primary"
    rec["primary"] = primary
    if primary:
        rec["code"] = primary.get("code")
        rec["name"] = primary.get("name")
        rec["price"] = primary.get("buy_price") or primary.get("last")
    if bad_stocks:
        note = rec.get("size_note") or ""
        rec["size_note"] = (
            (note + "；") if note else ""
        ) + f"含 {len(bad_stocks)} 只非上升趋势个股（已红标，不建议买）"
    if bad_stocks and not up_stocks and not etf_items:
        rec["buy"] = False
        rec["title"] = "个股非上升趋势"
        rec["text"] = "日线不是上升趋势，个股暂不建议买"
        rec["size_note"] = "下降/震荡个股仅作警示展示，不要按建议价买入。"
    elif merged:
        tradable = [
            x
            for x in merged
            if x.get("ready") and (x.get("kind") != "stock" or x.get("trend_ok"))
        ]
        if not tradable and rec.get("buy"):
            if not any(x.get("kind") == "etf" and x.get("ready") for x in merged):
                rec["buy"] = False
    return rec


def _build_recommend(
    action: str,
    main: dict[str, Any],
    vehicle: dict[str, Any],
    bounce: float | None,
    stocks: list[dict[str, Any]],
    bans: list[str],
) -> dict[str, Any]:
    """Build ETF-first plus stock-alt cards with suggested buy / wait / stop prices."""
    avoid = "、".join(bans) if bans else "高位连板个股"
    board = main.get("name") or "—"
    ready_action = action == "可买入"
    wait_action = action == "观察回踩"
    items: list[dict[str, Any]] = []

    if ready_action or wait_action:
        if vehicle.get("code"):
            items.append(
                _recommend_item(
                    vehicle,
                    kind="etf",
                    role="primary",
                    ready=ready_action,
                    reason=_etf_reason(board, vehicle, bounce, ready_action, wait_action),
                )
            )
        stock_limit = 2 if items else 3
        taken = 0
        for stock in stocks:
            if vehicle.get("code") and stock.get("code") == vehicle.get("code"):
                continue
            items.append(
                _recommend_item(
                    stock,
                    kind="stock",
                    role="alt" if items else "primary",
                    ready=ready_action and bool(stock.get("ready")),
                    reason=str(stock.get("reason") or f"主线 {board} 回踩票"),
                )
            )
            taken += 1
            if taken >= stock_limit:
                break

        if wait_action:
            for item in items:
                item["ready"] = False
                if item.get("wait_price") is not None:
                    item["buy_price"] = item["wait_price"]
                item["role_label"] = "ETF 盯回踩" if item["kind"] == "etf" else "个股盯回踩"

    primary = next((x for x in items if x.get("role") == "primary"), None) or (
        items[0] if items else None
    )
    if ready_action and items:
        title = "建议买入"
        text = _headline_text(items, board, buying=True)
        has_etf = any(x.get("kind") == "etf" for x in items)
        if _is_chi_star_etf(vehicle.get("code")):
            size_note = "创业板ETF / 科创50ETF 可以买；对应板块个股无权限，不推荐。"
        elif has_etf:
            size_note = "优先 ETF；个股只给主板未封板回踩票。创业/科创个股不推荐。"
        else:
            size_note = "该主线暂无映射 ETF，以下为主板未封板回踩票。仓位自己定。"
        stop = _stop_line(primary)
    elif wait_action and items:
        title = "盯回踩价，先不追"
        text = _headline_text(items, board, buying=False)
        size_note = "到回踩价再动手。现价靠近日高就不要追。"
        stop = _stop_line(primary)
    else:
        title = "暂不买入"
        text = f"暂不买入。实时主线 {board}。" if board != "—" else "暂不买入，主线未明。"
        size_note = f"实时主线 {board}" if board != "—" else "主线未明"
        stop = "确认前不加仓"

    return {
        "buy": bool(ready_action and items),
        "title": title,
        "code": (primary or {}).get("code"),
        "name": (primary or {}).get("name"),
        "price": (primary or {}).get("buy_price") or (primary or {}).get("last"),
        "qty": 0,
        "amount": 0,
        "size_note": size_note,
        "stop": stop,
        "avoid": (
            f"主线 {board}；不要去追：{avoid}"
            + ("；创业/科创个股无权限，不推荐" if _blocks_chi_star_stocks(board) or _is_chi_star_etf(vehicle.get("code")) else "")
        ),
        "text": text,
        "items": items,
        "primary": primary,
    }


def _stock_candidates(main: dict[str, Any], zt: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick unsealed main-board pullbacks from the live mainline constituent pool."""
    sealed = {normalize_code(x.get("code")) for x in zt}
    boards_by = {
        normalize_code(x.get("code")): int(x.get("boards") or 0) for x in zt
    }
    skip = {
        normalize_code(main.get("leader_code")),
        normalize_code(main.get("slot_code")),
    }
    pool = list(main.get("pool") or main.get("members") or [])
    scored: list[tuple[float, dict[str, Any]]] = []
    for member in pool:
        item = _score_stock(member, sealed, boards_by, skip, strict=True)
        if item:
            scored.append(item)
    if len(scored) < 2:
        for member in pool:
            code = normalize_code(member.get("code"))
            if any(code == normalize_code(x[1].get("code")) for x in scored):
                continue
            item = _score_stock(member, sealed, boards_by, skip, strict=False)
            if item:
                scored.append(item)
    scored.sort(key=lambda row: row[0], reverse=True)
    return [row[1] for row in scored[:4]]


def _score_stock(
    member: dict[str, Any],
    sealed: set[str],
    boards_by: dict[str, int],
    skip: set[str],
    *,
    strict: bool,
) -> tuple[float, dict[str, Any]] | None:
    """Score one constituent as a pullback candidate, or reject it."""
    code = normalize_code(member.get("code"))
    name = str(member.get("name") or "")
    pct = member.get("pct")
    price = member.get("price")
    high = member.get("high")
    if not code or price in (None, 0) or not is_main_board(code) or is_st(name):
        return None
    if code in skip or code in sealed:
        return None
    if is_limit_up(name, pct):
        return None
    if pct is None:
        return None
    if strict and (pct < 0 or pct > 5.5):
        return None
    if not strict and (pct < -1.5 or pct > 7.0):
        return None
    pullback = None
    if high and high > 0:
        pullback = (float(high) - float(price)) / float(high) * 100.0
    if strict and (pullback is None or pullback < 0.8 or pullback > 4.0):
        return None
    score = 20.0 - abs(float(pct) - 2.0) * 2.0
    if pullback is not None:
        if 0.8 <= pullback <= 4.0:
            score += 15.0
        elif pullback > 4.0:
            score += 4.0
        else:
            score -= 6.0
    boards = int(boards_by.get(code) or 0)
    ready = pullback is not None and pullback >= 0.8
    reason = _stock_reason(pct, pullback, ready)
    out = dict(member)
    out["code"] = code
    out["boards"] = boards
    out["pullback"] = None if pullback is None else round(pullback, 2)
    out["ready"] = ready
    out["reason"] = reason
    return score, out


def _stock_reason(pct: float, pullback: float | None, ready: bool) -> str:
    """Describe why a stock is listed as a pullback alternative."""
    parts = [f"涨幅 {_fmt_pct(pct)}"]
    if pullback is not None:
        parts.append(f"高点回撤 {pullback:.1f}%")
    parts.append("未封板")
    if ready:
        parts.append("可按建议价试")
    else:
        parts.append("仍偏高，等回踩价")
    return "，".join(parts)


def _etf_reason(
    board: str,
    vehicle: dict[str, Any],
    bounce: float | None,
    ready: bool,
    waiting: bool,
) -> str:
    """Describe why the mapped ETF is the primary vehicle."""
    bits = [f"主线 {board} 映射载体"]
    if bounce is not None:
        bits.append(f"离日低回升 {_fmt_num(bounce)}%")
    pct = vehicle.get("pct")
    if pct is not None:
        bits.append(_fmt_pct(pct))
    if waiting:
        bits.append("尖峰/未站稳，等回踩价")
    elif ready:
        bits.append("可按建议价买")
    if _is_chi_star_etf(vehicle.get("code")):
        bits.append("ETF可买，创业/科创个股无权限不推荐")
    return "，".join(bits)


def _recommend_item(
    quote: dict[str, Any],
    *,
    kind: str,
    role: str,
    ready: bool,
    reason: str,
) -> dict[str, Any]:
    """Attach a buy / wait / stop / chase plan onto a quote."""
    etf = kind == "etf" or _is_etf_code(str(quote.get("code") or ""))
    digits = 3 if etf else 2
    last = quote.get("price")
    low = quote.get("low")
    high = quote.get("high")
    near_high = bool(
        last and high and high > 0 and (float(high) - float(last)) / float(high) * 100.0 < (0.25 if etf else 0.35)
    )
    buy_now = bool(ready and last is not None and not near_high)
    wait = _wait_price(last, low, etf)
    stop = _stop_price(last, low, etf)
    chase = _chase_price(last, high, etf)
    buy = last if buy_now else wait
    if buy is None:
        buy = last
    kind_label = "ETF" if etf else "个股"
    if buy_now:
        role_label = f"{kind_label} 主推" if role == "primary" else f"{kind_label} 备选"
    else:
        role_label = f"{kind_label} 盯回踩"
    return {
        "kind": "etf" if etf else "stock",
        "kind_label": kind_label,
        "role": role,
        "role_label": role_label,
        "code": normalize_code(quote.get("code")),
        "name": quote.get("name"),
        "last": _px(last, digits),
        "pct": None if quote.get("pct") is None else round(float(quote["pct"]), 2),
        "buy_price": _px(buy, digits),
        "wait_price": _px(wait, digits),
        "stop_price": _px(stop, digits),
        "chase_price": _px(chase, digits),
        "low": _px(low, digits),
        "high": _px(high, digits),
        "ready": buy_now,
        "reason": reason,
        "qty": 100,
    }


def _wait_price(last: float | None, low: float | None, etf: bool) -> float | None:
    """Return a better pullback entry below the last price."""
    if last is None:
        return None
    gap = 0.996 if etf else 0.992
    wait = float(last) * gap
    if low not in (None, 0) and float(low) < float(last):
        mid = (float(low) + float(last)) / 2.0
        wait = min(wait, mid)
        wait = max(wait, float(low))
    return wait


def _stop_price(last: float | None, low: float | None, etf: bool) -> float | None:
    """Use the session low as the invalidation level, with a last-price fallback."""
    if low not in (None, 0):
        return float(low)
    if last is None:
        return None
    return float(last) * (0.985 if etf else 0.97)


def _chase_price(last: float | None, high: float | None, etf: bool) -> float | None:
    """Mark the price above which chasing is not allowed."""
    if last is None:
        return None
    bump = float(last) * (1.012 if etf else 1.02)
    if high not in (None, 0) and float(high) >= float(last):
        return float(high)
    return bump


def _px(value: float | None, digits: int) -> float | None:
    """Round a price to ETF or stock precision."""
    if value is None:
        return None
    return round(float(value), digits)


def _is_etf_code(code: str) -> bool:
    """Return True for common mainland ETF code prefixes."""
    c = normalize_code(code)
    return c.startswith(("15", "51", "56", "58"))


def _is_chi_star_etf(code: str | None) -> bool:
    """Return True for ChiNext / STAR ETFs that remain tradable without stock permission."""
    return normalize_code(code) in CHINEXT_STAR_ETFS


def _blocks_chi_star_stocks(board_name: str | None) -> bool:
    """Return True when the live mainline sits on ChiNext or STAR, so stocks are skipped."""
    text = board_name or ""
    return "创业板" in text or "科创" in text


def _headline_text(items: list[dict[str, Any]], board: str, *, buying: bool) -> str:
    """Build the one-line summary above the recommendation cards."""
    primary = items[0]
    verb = "建议买" if buying and primary.get("ready") else "盯回踩"
    px = primary.get("buy_price") or primary.get("last") or "—"
    extra = ""
    alts = [x for x in items[1:] if x.get("code")]
    if alts:
        extra = "；备选 " + "、".join(f"{x['name']} {x['code']}" for x in alts[:2])
    return (
        f"{verb} {primary.get('name') or ''} {primary.get('code') or ''}  "
        f"{px}  （主线 {board}）{extra}"
    )


def _stop_line(primary: dict[str, Any] | None) -> str:
    """One-line stop hint for the summary row."""
    if not primary or primary.get("stop_price") is None:
        return "按你自己的止损"
    return f"参考止损 {primary.get('stop_price')}（跌破日低视为回踩失败）"


def build_sell_advice(
    positions: list[dict[str, Any]],
    verdict: dict[str, Any] | None,
    phase: str,
) -> dict[str, Any]:
    """Build sell / hold cards for locally recorded positions."""
    verdict = verdict or {}
    items: list[dict[str, Any]] = []
    for row in positions or []:
        item = _sell_item(row, verdict, phase)
        if item:
            items.append(item)
    rank = {"stop": 0, "take": 1, "trim": 2, "hold": 3}
    items.sort(key=lambda x: (rank.get(str(x.get("urgency") or "hold"), 9), -(x.get("pnl_pct") or 0)))
    items = items[:4]
    sell_now = [x for x in items if x.get("ready")]
    if not positions:
        return {
            "sell": False,
            "empty": True,
            "title": "暂无仓位",
            "text": "暂无仓位 · 买入记账后这里给出卖出建议",
            "size_note": "仓位页记账后，按浮盈、回撤、主线强弱提示卖点。",
            "items": [],
        }
    if sell_now:
        primary = sell_now[0]
        text = (
            f"{primary.get('role_label')} {primary.get('name')} {primary.get('code')}  "
            f"{primary.get('sell_price')}"
        )
        size_note = "到价就动手。本地提示，不会下单。"
    else:
        primary = items[0] if items else None
        text = (
            f"继续持有 · 盯 {primary.get('name')} 目标 {primary.get('sell_price')}"
            if primary
            else "继续持有"
        )
        size_note = "未触发卖点时，建议卖=目标价，止损按成本下方。"
    return {
        "sell": bool(sell_now),
        "empty": False,
        "title": "建议卖出" if sell_now else "仓位观察",
        "text": text,
        "size_note": size_note,
        "items": items,
        "primary": primary if items else None,
    }


def _sell_item(
    row: dict[str, Any],
    verdict: dict[str, Any],
    phase: str,
) -> dict[str, Any] | None:
    """Decide whether a held name should be sold, trimmed, or held."""
    code = normalize_code(row.get("code"))
    name = str(row.get("name") or code)
    last = row.get("last")
    buy = float(row.get("buy_price") or 0)
    if not code or not buy:
        return None
    etf = _is_etf_code(code)
    digits = 3 if etf else 2
    high = row.get("high")
    low = row.get("low")
    pct = row.get("last_pct")
    pnl_pct = row.get("pnl_pct")
    if pnl_pct is None and last is not None and buy:
        pnl_pct = (float(last) / buy - 1.0) * 100.0
    pullback = None
    if last is not None and high not in (None, 0) and float(high) > 0:
        pullback = (float(high) - float(last)) / float(high) * 100.0

    stop = buy * (0.985 if etf else 0.97)
    if low not in (None, 0) and float(low) < buy:
        stop = max(float(low), buy * (0.98 if etf else 0.96))
    target = buy * (1.03 if etf else 1.05)
    if high not in (None, 0) and float(high) > target:
        target = float(high) * (0.995 if etf else 0.99)

    action = verdict.get("action") or ""
    main_status = ((verdict.get("mainline") or {}).get("status")) or ""
    soft_exit = action == "观望" or main_status == "退潮" or phase in ("恐慌", "高潮")

    urgency = "hold"
    ready = False
    role_label = "继续持有"
    sell_price = target
    sell_pct = 0
    reason_parts: list[str] = []

    if last is None:
        role_label = "待行情"
        reason_parts.append("尚无现价，先不判卖点")
    elif float(last) <= stop or (pnl_pct is not None and pnl_pct <= (-1.5 if etf else -3.0)):
        urgency = "stop"
        ready = True
        role_label = "止损卖出"
        sell_price = float(last)
        sell_pct = 100
        reason_parts.append(f"浮盈 {_fmt_pct(pnl_pct)}，触及止损带")
    elif (
        pnl_pct is not None
        and pnl_pct >= (3.0 if etf else 5.0)
        and pullback is not None
        and pullback >= (0.8 if etf else 1.5)
    ):
        urgency = "take"
        ready = True
        role_label = "冲高回落止盈"
        sell_price = float(last)
        sell_pct = 50
        reason_parts.append(f"浮盈 {_fmt_pct(pnl_pct)}，高点回撤 {pullback:.1f}%")
    elif pnl_pct is not None and pnl_pct >= (5.0 if etf else 8.0):
        urgency = "take"
        ready = True
        role_label = "落袋为安"
        sell_price = float(last)
        sell_pct = 70
        reason_parts.append(f"浮盈 {_fmt_pct(pnl_pct)}，建议减仓或了结")
    elif soft_exit and pnl_pct is not None and pnl_pct > 0.5:
        urgency = "trim"
        ready = True
        role_label = "建议减仓"
        sell_price = float(last)
        sell_pct = 30
        reason_parts.append(f"主线转弱/相位偏热，浮盈 {_fmt_pct(pnl_pct)} 先减")
    else:
        role_label = "继续持有"
        sell_price = target
        sell_pct = 0
        reason_parts.append(f"浮盈 {_fmt_pct(pnl_pct)}，未到卖点，盯目标价")
        if pullback is not None:
            reason_parts.append(f"高点回撤 {pullback:.1f}%")

    return {
        "id": row.get("id"),
        "kind": "etf" if etf else "stock",
        "kind_label": "ETF" if etf else "个股",
        "role_label": role_label,
        "urgency": urgency,
        "ready": ready,
        "code": code,
        "name": name,
        "qty": int(row.get("qty") or 0),
        "last": _px(last, digits),
        "pct": None if pct is None else round(float(pct), 2),
        "pnl_pct": None if pnl_pct is None else round(float(pnl_pct), 2),
        "buy_price": _px(buy, digits),
        "sell_price": _px(sell_price, digits),
        "sell_pct": sell_pct,
        "stop_price": _px(stop, digits),
        "target_price": _px(target, digits),
        "reason": "，".join(reason_parts),
    }


def decorate_positions(
    rows: list[dict[str, Any]], quotes: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach mark-to-market fields used by the position tab."""
    out: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code") or "").zfill(6)
        q = quotes.get(code) or {}
        last = q.get("price")
        buy = float(row.get("buy_price") or 0)
        qty = int(row.get("qty") or 0)
        cost = round(buy * qty, 2)
        market = round(last * qty, 2) if last is not None else None
        pnl = round(market - cost, 2) if market is not None else None
        pnl_pct = round((last / buy - 1.0) * 100.0, 2) if last and buy else None
        item = dict(row)
        item["code"] = code
        item["name"] = row.get("name") or q.get("name") or code
        item["last"] = last
        item["last_pct"] = q.get("pct")
        item["high"] = q.get("high")
        item["low"] = q.get("low")
        item["cost"] = cost
        item["market"] = market
        item["pnl"] = pnl
        item["pnl_pct"] = pnl_pct
        out.append(item)
    return out


def position_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate cost / market value / P&L for the position tab."""
    from market_desk.config import (
        POSITION_MAX_NAMES,
        POSITION_MAX_SINGLE_PCT,
        POSITION_MAX_TOTAL_COST,
    )

    cost = sum(float(r.get("cost") or 0) for r in rows)
    marked = [r for r in rows if r.get("market") is not None]
    market = sum(float(r.get("market") or 0) for r in marked)
    pnl = round(market - cost, 2) if marked else None
    pnl_pct = round((market / cost - 1.0) * 100.0, 2) if marked and cost else None
    notes: list[str] = []
    if len(rows) > POSITION_MAX_NAMES:
        notes.append(f"持仓只数 {len(rows)} 超过软上限 {POSITION_MAX_NAMES}")
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
        "count": len(rows),
        "cost": round(cost, 2),
        "market": round(market, 2) if marked else None,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "priced": len(marked),
        "risk_note": "；".join(notes) if notes else "",
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


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}"
