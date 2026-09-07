"""Share-lot helpers for A-share style 100-share units."""

from __future__ import annotations

import math

LOT_SIZE = 100


def round_lot(shares: float, *, lot: int = LOT_SIZE) -> int:
    """Round share count to the nearest lot with half-up rounding."""
    lot = max(1, int(lot or LOT_SIZE))
    if shares <= 0:
        return 0
    lots = int(math.floor(float(shares) / lot + 0.5))
    return max(0, lots * lot)


def half_sell_qty(hold: int, *, lot: int = LOT_SIZE) -> int:
    """
    Shares to sell for a one-click half trim.

    One lot or less is treated as a full exit. Larger holdings use half rounded
    to the nearest lot (half-up); never exceed ``hold``.
    """
    hold = int(hold or 0)
    lot = max(1, int(lot or LOT_SIZE))
    if hold <= 0:
        return 0
    if hold <= lot:
        return hold
    rounded = round_lot(hold / 2.0, lot=lot)
    if rounded <= 0:
        rounded = lot
    return min(hold, rounded)


def clear_sell_qty(hold: int) -> int:
    """Shares to sell for a full exit."""
    return max(0, int(hold or 0))
