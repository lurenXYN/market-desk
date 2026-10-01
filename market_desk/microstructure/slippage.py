"""Dynamic buy slippage / impact cost from the five-level order book."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def session_minutes_elapsed(now: datetime | None) -> int | None:
    """Return continuous-session minutes elapsed today (09:30–11:30, 13:00–15:00)."""
    if now is None:
        return None
    m = now.hour * 60 + now.minute
    am_open, am_close = 9 * 60 + 30, 11 * 60 + 30
    pm_open, pm_close = 13 * 60, 15 * 60
    if m < am_open:
        return None
    if m <= am_close:
        return max(1, m - am_open)
    if m < pm_open:
        return 120
    return 120 + max(0, min(m, pm_close) - pm_open)


def _levels(raw: Any) -> list[tuple[float, float]]:
    """Normalize ``[[price, lots], ...]`` into positive float tuples."""
    out: list[tuple[float, float]] = []
    for lvl in raw or []:
        try:
            px, vol = float(lvl[0]), float(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        if px > 0 and vol > 0:
            out.append((px, vol))
    return out


def estimate_buy_impact(
    quote: dict[str, Any] | None,
    qty: int,
    *,
    kind: str = "stock",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Estimate the cost of lifting ``qty`` shares through the visible asks.

    Impact is the walked-book average fill versus the mid price, so it already
    includes half the spread. Grading is tick-aware: a one-tick spread never
    flags, and the unavoidable half tick is subtracted before the impact
    thresholds, so low-priced names are not penalized for their tick size.
    Liquidity density is session turnover per elapsed minute. The result grades the book as ``ok`` / ``warn`` / ``thin`` and
    proposes a soft size multiplier plus an optional depth-based qty cap.

    Args:
        quote: Tencent quote with ``bids`` / ``asks`` levels (lots) and ``amount`` (元).
        qty: Planned shares (lot multiples of 100).
        kind: ``"stock"`` or ``"etf"`` (selects the density floor).
        now: Wall clock for the density estimate; ``None`` skips density.

    Returns:
        A dict with ``level`` (``ok`` / ``warn`` / ``thin`` / ``na``), ``mult``,
        ``qty_cap``, ``flags`` and the raw metrics (bps, depth, density).
    """
    from market_desk.config import (
        SLIP_DENSITY_MIN_ETF,
        SLIP_DENSITY_MIN_STOCK,
        SLIP_DEPTH_TAKE_MAX,
        SLIP_IMPACT_THIN_BPS,
        SLIP_IMPACT_WARN_BPS,
        SLIP_SPREAD_THIN_BPS,
        SLIP_SPREAD_WARN_BPS,
        SLIP_THIN_SIZE_MULT,
        SLIP_WARN_SIZE_MULT,
    )

    q = quote or {}
    bids = _levels(q.get("bids"))
    asks = _levels(q.get("asks"))
    out: dict[str, Any] = {
        "level": "na",
        "mult": 1.0,
        "qty_cap": None,
        "flags": [],
        "spread_bps": None,
        "impact_bps": None,
        "depth_lots": None,
        "depth_amount": None,
        "take_share": None,
        "density": None,
    }
    if not asks:
        out["note"] = "无卖盘（封板/停牌/竞价）"
        return out
    ask1 = asks[0][0]
    bid1 = bids[0][0] if bids else None
    mid = (ask1 + bid1) / 2.0 if bid1 is not None and bid1 < ask1 else ask1
    spread_bps = (ask1 - bid1) / mid * 1e4 if bid1 is not None and bid1 < ask1 else 0.0
    lots_need = max(1.0, float(int(qty or 0)) / 100.0)
    depth_lots = sum(v for _, v in asks)
    depth_amount = sum(p * v * 100.0 for p, v in asks)
    left = lots_need
    cost = 0.0
    filled = 0.0
    for px, vol in asks:
        take = min(left, vol)
        cost += take * px
        filled += take
        left -= take
        if left <= 0:
            break
    if left > 0:
        # Unseen depth: price the remainder one tick-band past the last level.
        worst = asks[-1][0] * 1.002
        cost += left * worst
        filled += left
    avg_fill = cost / filled if filled > 0 else ask1
    impact_bps = (avg_fill - mid) / mid * 1e4 if mid > 0 else 0.0
    take_share = lots_need / depth_lots if depth_lots > 0 else None
    # A one-tick spread is structural on low-priced names, not illiquidity.
    tick = 0.001 if kind == "etf" else 0.01
    tick_bps = tick / mid * 1e4 if mid > 0 else 0.0
    one_tick = bid1 is not None and (ask1 - bid1) <= tick * 1.01
    spread_eff = 0.0 if one_tick else spread_bps
    impact_eff = max(0.0, impact_bps - tick_bps / 2.0)

    density = None
    minutes = session_minutes_elapsed(now)
    try:
        amount = float(q.get("amount") or 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    if minutes and amount > 0:
        density = amount / float(minutes)

    flags: list[str] = []
    thin = False
    if spread_eff >= float(SLIP_SPREAD_THIN_BPS):
        flags.append("点差过大")
        thin = True
    elif spread_eff >= float(SLIP_SPREAD_WARN_BPS):
        flags.append("点差偏大")
    if impact_eff >= float(SLIP_IMPACT_THIN_BPS):
        flags.append("冲击成本高")
        thin = True
    elif impact_eff >= float(SLIP_IMPACT_WARN_BPS):
        flags.append("冲击成本偏高")
    qty_cap = None
    if take_share is not None and take_share > float(SLIP_DEPTH_TAKE_MAX):
        flags.append("盘口偏薄")
        cap_lots = int(depth_lots * float(SLIP_DEPTH_TAKE_MAX))
        qty_cap = max(100, cap_lots * 100)
    floor = float(SLIP_DENSITY_MIN_ETF if kind == "etf" else SLIP_DENSITY_MIN_STOCK)
    if density is not None and density < floor:
        flags.append("成交稀疏")

    if thin:
        level, mult = "thin", float(SLIP_THIN_SIZE_MULT)
    elif flags:
        level, mult = "warn", float(SLIP_WARN_SIZE_MULT)
    else:
        level, mult = "ok", 1.0
    out.update(
        level=level,
        mult=mult,
        qty_cap=qty_cap,
        flags=flags,
        spread_bps=round(spread_bps, 1),
        impact_bps=round(impact_bps, 1),
        tick_bps=round(tick_bps, 1),
        depth_lots=int(depth_lots),
        depth_amount=round(depth_amount, 0),
        take_share=None if take_share is None else round(take_share, 3),
        density=None if density is None else round(density, 0),
    )
    return out


def apply_slippage_filter(
    recommend: dict[str, Any] | None,
    quotes_by_code: dict[str, dict[str, Any]] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Shrink suggested size on buy cards whose book is too thin or too wide.

    Soft only: ``ready`` is never cleared. Warn / thin books scale ``qty`` by
    the graded multiplier, a depth cap trims qty to a fraction of visible asks,
    and a ``slip_warn`` flag plus a ``confirm_soft`` label surface the reason.

    Args:
        recommend: A recommend box with ``items``.
        quotes_by_code: Tencent quotes (with book levels) keyed by code.
        now: Wall clock for the liquidity-density estimate.

    Returns:
        A copy of ``recommend`` with ``slippage`` attached per evaluated item.
    """
    from market_desk.verdict.common import _join_hint, _scale_item_qty

    rec = dict(recommend or {})
    items = [dict(x) for x in (rec.get("items") or [])]
    if not items or not quotes_by_code:
        return rec
    warned: list[str] = []
    for item in items:
        code = str(item.get("code") or "").zfill(6)
        quote = quotes_by_code.get(code)
        if not quote or not (item.get("ready") or item.get("near_entry")):
            continue
        try:
            qty = int(item.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        kind = str(item.get("kind") or "stock")
        slip = estimate_buy_impact(quote, qty, kind=kind, now=now)
        item["slippage"] = slip
        if slip.get("level") not in ("warn", "thin"):
            item.pop("slip_warn", None)
            continue
        flags = list(slip.get("flags") or [])
        tip = "流动性过滤：" + "、".join(flags) + "·降仓"
        if not item.get("slip_scaled"):
            _scale_item_qty(item, float(slip.get("mult") or 1.0), tip)
            item["slip_scaled"] = True
        cap = slip.get("qty_cap")
        if cap is not None and int(item.get("qty") or 0) > int(cap):
            _scale_item_qty(item, int(cap) / float(item.get("qty") or 1), tip)
        item["slip_warn"] = True
        item["slip_size_mult"] = slip.get("mult")
        soft = list(item.get("confirm_soft") or [])
        label = f"{flags[0]}·降仓" if flags else "流动性偏薄·降仓"
        if label not in soft:
            soft.append(label)
        item["confirm_soft"] = soft
        item["reason"] = _join_hint(str(item.get("reason") or ""), tip)
        warned.append(str(item.get("name") or code))
    rec["items"] = items
    if warned:
        rec["size_note"] = _join_hint(
            str(rec.get("size_note") or ""),
            "流动性过滤降仓：" + "、".join(warned[:3]),
        )
    return rec
