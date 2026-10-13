"""Unit tests for flash order-copy lot sizing."""

from market_desk.flash_copy import flash_buy_line, flash_lot_qty
from market_desk.settings import DEFAULTS, USER_PRIVATE_KEYS, _normalize


def test_full_position_cny_is_private_and_clamped() -> None:
    assert "full_position_cny" in USER_PRIVATE_KEYS
    assert DEFAULTS["full_position_cny"] == 10000.0
    out = _normalize({"full_position_cny": 50})
    assert out["full_position_cny"] == 1000.0


def test_flash_lot_qty_rounds_down_to_board_lot() -> None:
    assert flash_lot_qty(10.0, 10000) == (1000, "")
    assert flash_lot_qty(42.5, 10000) == (200, "")
    qty, note = flash_lot_qty(120.0, 10000)
    assert qty == 0 and "不足一手" in note


def test_flash_buy_line_full_and_half() -> None:
    full = flash_buy_line(
        code="002463", name="沪电股份", price=42.5, stop=40.1,
        full_cny=10000, half=False,
    )
    assert full == "买入 002463 沪电股份 42.50 × 200股（满） 止损 40.10"
    half = flash_buy_line(
        code="002463", name="沪电股份", price=42.5, stop=40.1,
        full_cny=10000, half=True,
    )
    assert half == "买入 002463 沪电股份 42.50 × 100股（半） 止损 40.10"
