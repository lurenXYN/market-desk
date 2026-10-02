"""Shared price, formatting, and confirm-flag helpers for verdict submodules."""

from __future__ import annotations

from typing import Any
from market_desk.config import CHINEXT_STAR_ETFS
from market_desk.filters import normalize_code


_BUY_ACTIONS = frozenset({"可买入", "可小仓"})


def _pool_codes_from_board(board: dict[str, Any] | None) -> list[str]:
    """Collect normalized member codes from a hot-board card."""
    out: list[str] = []
    for m in (board or {}).get("pool") or (board or {}).get("members") or []:
        raw = m.get("code") if isinstance(m, dict) else m
        code = normalize_code(raw)
        if code and code not in out:
            out.append(code)
    return out[:40]


def _theme_entry(
    *,
    name: str,
    role: str,
    status: str,
    lifecycle: str | None,
    pool_codes: list[str] | None = None,
    carrier_code: str | None = None,
    rec_codes: list[str] | None = None,
    score: float | None = None,
    main_yi: float | None = None,
    board: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one sell-theme descriptor (buy path never reads this list).

    When the live board card is passed, leader / member intraday marks are
    attached for the sector crack alert (see ``_crack_fields``).
    """
    entry = {
        "name": name,
        "role": role,
        "status": status or "",
        "lifecycle": lifecycle or "",
        "pool_codes": list(pool_codes or [])[:40],
        "carrier_code": normalize_code(carrier_code) if carrier_code else None,
        "rec_codes": list(rec_codes or [])[:20],
        "score": score,
        "main_yi": main_yi,
    }
    entry.update(_crack_fields(board))
    return entry


def _crack_fields(board: dict[str, Any] | None) -> dict[str, Any]:
    """Extract leader and member intraday marks from a live board card.

    Args:
        board: Enriched hot-board card (``pool`` / ``members`` rows carry
            ``code``, ``pct``, ``price``, ``high``; ``leader_code`` and
            ``leader_boards`` come from the limit-up ladder).

    Returns:
        ``leader_code``, ``leader_pct``, ``leader_high_pct``, ``leader_pullback``
        (high% − now%, in points), ``leader_boards`` and ``pool`` (``code`` /
        ``pct`` per member). Empty when no card is available.
    """
    if not board:
        return {}
    leader_code = normalize_code(board.get("leader_code"))
    out: dict[str, Any] = {"leader_boards": int(board.get("leader_boards") or 0)}
    pool: list[dict[str, Any]] = []
    leader: dict[str, Any] | None = None
    for m in board.get("pool") or board.get("members") or []:
        if not isinstance(m, dict):
            continue
        code = normalize_code(m.get("code"))
        pct = _num(m.get("pct"))
        if not code or pct is None:
            continue
        pool.append({"code": code, "pct": round(pct, 2)})
        if leader_code and code == leader_code and leader is None:
            leader = m
    out["pool"] = pool[:40]
    if leader is not None:
        pct = _num(leader.get("pct"))
        price = _num(leader.get("price"))
        high = _num(leader.get("high"))
        out["leader_code"] = leader_code
        out["leader_pct"] = None if pct is None else round(pct, 2)
        if pct is not None and price and high and price > 0 and pct > -100:
            prev_close = price / (1.0 + pct / 100.0)
            high_pct = (high / prev_close - 1.0) * 100.0
            out["leader_high_pct"] = round(high_pct, 2)
            out["leader_pullback"] = round(max(0.0, high_pct - pct), 2)
    return out


def _num(raw: Any) -> float | None:
    """Coerce a quote field to float, returning None for blanks and junk."""
    try:
        return None if raw in (None, "", "-") else float(raw)
    except (TypeError, ValueError):
        return None


def _lookup_hot_board(
    hot: list[dict[str, Any]] | None,
    *,
    name: str | None = None,
    bk: Any = None,
) -> dict[str, Any] | None:
    """Resolve a full board card from the hot list by name or bk code."""
    want_name = str(name or "").strip()
    want_bk = str(bk or "").strip()
    for card in hot or []:
        if want_bk and str(card.get("bk") or "").strip() == want_bk:
            return card
        if want_name and str(card.get("name") or "").strip() == want_name:
            return card
    return None


def _join_hint(base: str, extra: str) -> str:
    """Append a size-hint fragment without duplicating text."""
    base = (base or "").strip()
    extra = (extra or "").strip()
    if not extra:
        return base
    if not base:
        return extra
    if extra in base:
        return base
    return f"{base}；{extra}"


# Soft confirm flags: score/size only — never hard-kill ready / never block arm.
_SOFT_CONFIRM_FLAGS = frozenset(
    {
        "日线非上升",
        "分时样本不足",
        "分时未验",
    }
)


def is_soft_confirm_flag(flag: str) -> bool:
    """Return True when a confirm_fail / soft label is advisory only."""
    text = str(flag or "").strip()
    if not text:
        return False
    if text in _SOFT_CONFIRM_FLAGS:
        return True
    if text.startswith("分时样本"):
        return True
    return False


def is_tip_probe_allow_fail(flag: str) -> bool:
    """Return True when a hard tip/shallow fail still permits half-size probe."""
    from market_desk.config import TIP_PROBE_ALLOW_FAILS

    return str(flag or "").strip() in TIP_PROBE_ALLOW_FAILS


def hard_confirm_fails(flags: list[Any] | None) -> list[str]:
    """Filter confirm_fail down to hard gates that block ready / arming."""
    out: list[str] = []
    for raw in flags or []:
        text = str(raw or "").strip()
        if text and not is_soft_confirm_flag(text):
            out.append(text)
    return out


def probe_blocking_fails(flags: list[Any] | None) -> list[str]:
    """Hard fails that still forbid probe (excludes tip/shallow probe-allow labels)."""
    return [f for f in hard_confirm_fails(flags) if not is_tip_probe_allow_fail(f)]


def _scale_item_qty(item: dict[str, Any], mult: float, tip: str) -> None:
    """Lot-round scale qty / risk_plan after soft probe or trend damp."""
    try:
        qty = int(item.get("qty") or 0)
    except (TypeError, ValueError):
        return
    if qty <= 0 or abs(float(mult) - 1.0) < 0.01:
        return
    new_qty = max(100, int(round(qty * float(mult) / 100.0) * 100))
    item["qty"] = new_qty
    plan = item.get("risk_plan")
    if isinstance(plan, dict):
        plan = dict(plan)
        plan["qty"] = new_qty
        plan["note"] = _join_hint(str(plan.get("note") or ""), tip)
        item["risk_plan"] = plan


def _ensure_plan_price(item: dict[str, Any] | None) -> dict[str, Any]:
    """Stamp ``plan_price`` from current buy_price when missing (before demote)."""
    it = item if isinstance(item, dict) else {}
    if it.get("plan_price") is None and it.get("buy_price") is not None:
        it["plan_price"] = it.get("buy_price")
    return it


def _demote_buy_to_wait(item: dict[str, Any] | None) -> dict[str, Any]:
    """Point the card hang price at wait while keeping canonical ``plan_price``."""
    it = _ensure_plan_price(item)
    if it.get("wait_price") is not None:
        it["buy_price"] = it.get("wait_price")
    return it


def _batch_plan_lots(buy: float | None, *, etf: bool) -> list[dict[str, Any]] | None:
    """Build a 1/2/3-lot buy plan when the batch_plan setting is enabled."""
    from market_desk.settings import setting

    if not bool(setting("batch_plan", True)):
        return None
    unit = 100
    labels = ("试错", "确认", "加仓")
    lots: list[dict[str, Any]] = []
    for i, label in enumerate(labels, start=1):
        qty = unit
        cost = None if buy is None else round(float(buy) * qty, 2 if not etf else 3)
        lots.append({"lot": i, "qty": qty, "label": label, "approx_cost": cost})
    return lots


def _wait_price(last: float | None, low: float | None, etf: bool) -> float | None:
    """Return a better pullback entry below the last price."""
    from market_desk.config import ETF_WAIT_GAP, STOCK_WAIT_GAP

    if last is None:
        return None
    gap = ETF_WAIT_GAP if etf else STOCK_WAIT_GAP
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


def atr_shadow_stop(
    ref: float | None,
    stop: float | None,
    atr_pct: float | None,
    etf: bool,
) -> float | None:
    """Return the shadow ATR buy stop (never tighter than the live stop).

    Args:
        ref: Entry reference (locked plan price, else buy / last).
        stop: Live stop (session low); ignored when missing or not below ``ref``.
        atr_pct: Completed-session daily ATR in percent; None falls back to the minimum gap.
        etf: Use the ETF gap band instead of the stock band.

    Returns:
        ``min(stop, ref × (1 − gap%))`` with gap = clamp(mult × ATR, min, max), or None
        without a positive ``ref``.
    """
    from market_desk.config import (
        BUY_STOP_ATR_MULT,
        ETF_BUY_STOP_MAX_PCT,
        ETF_BUY_STOP_MIN_PCT,
        STOCK_BUY_STOP_MAX_PCT,
        STOCK_BUY_STOP_MIN_PCT,
    )

    try:
        ref_f = float(ref) if ref is not None else 0.0
    except (TypeError, ValueError):
        return None
    if ref_f <= 0:
        return None
    lo, hi = (ETF_BUY_STOP_MIN_PCT, ETF_BUY_STOP_MAX_PCT) if etf else (STOCK_BUY_STOP_MIN_PCT, STOCK_BUY_STOP_MAX_PCT)
    gap = float(lo)
    if atr_pct is not None and float(atr_pct) > 0:
        gap = min(float(hi), max(float(lo), float(BUY_STOP_ATR_MULT) * float(atr_pct)))
    target = ref_f * (1.0 - gap / 100.0)
    try:
        live = float(stop) if stop is not None else None
    except (TypeError, ValueError):
        live = None
    if live is None or live <= 0 or live >= ref_f:
        return target
    return min(live, target)


def attach_atr_shadow_stops(rec: dict[str, Any] | None, atr_by_code: dict[str, Any]) -> None:
    """Stamp ``stop_atr`` / ``atr_pct`` on recommendation items in place (display + audit only).

    Only codes present in ``atr_by_code`` (daily klines fetched this session) are
    stamped; ``stop_price``, sizing and alerts are left untouched.
    """
    for item in (rec or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        code = normalize_code(item.get("code"))
        if not code or code not in atr_by_code:
            continue
        etf = item.get("kind") == "etf" or _is_etf_code(code)
        ref = item.get("plan_price") or item.get("buy_price") or item.get("last")
        atr = atr_by_code.get(code)
        shadow = atr_shadow_stop(ref, item.get("stop_price"), atr, etf)
        if shadow is None:
            continue
        item["stop_atr"] = _px(shadow, 3 if etf else 2)
        item["atr_pct"] = atr


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


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}"
