"""Helpers for flash order-copy sizing (mirrors the desk UI clipboard lines)."""

from __future__ import annotations


def flash_lot_qty(price: float, budget_cny: float) -> tuple[int, str]:
    """Round a notional budget down to a whole board lot (100 shares).

    Args:
        price: Limit / plan price per share.
        budget_cny: Notional yuan for this size (full or half budget).

    Returns:
        ``(qty, note)`` where ``qty`` is 0 when under one lot and ``note``
        explains the shortfall.
    """
    try:
        px = float(price)
        budget = float(budget_cny)
    except (TypeError, ValueError):
        return 0, "缺价格或满仓金额"
    if px <= 0 or budget <= 0:
        return 0, "缺价格或满仓金额"
    qty = int(budget // px // 100) * 100
    if qty < 100:
        need = int(px * 100 + 0.999999)
        return 0, f"不足一手（约需 ≥{need} 元）"
    return qty, ""


def flash_buy_line(
    *,
    code: str,
    name: str,
    price: float,
    stop: float | None,
    full_cny: float,
    half: bool,
) -> str:
    """Build the buy flash-copy line used on recommend cards."""
    budget = float(full_cny) * (0.5 if half else 1.0)
    qty, _note = flash_lot_qty(price, budget)
    tag = "半" if half else "满"
    px = f"{float(price):.2f}"
    stop_s = f" 止损 {float(stop):.2f}" if stop and float(stop) > 0 else ""
    code_s = str(code or "").strip()
    name_s = str(name or "").strip() or code_s
    if qty < 100:
        return f"买入 {code_s} {name_s} {px} × 不足一手（{tag}）{stop_s}"
    return f"买入 {code_s} {name_s} {px} × {qty}股（{tag}）{stop_s}"
