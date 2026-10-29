"""Local order state machine (vnpy Status subset, in-process book)."""

from __future__ import annotations

from typing import Any

from market_desk.exec.types import ACTIVE_STATUSES, OrderData, Status


class OrderBook:
    """Track orders by ``orderid``; newest first in ``recent``."""

    def __init__(self) -> None:
        self._orders: dict[str, OrderData] = {}
        self._order: list[str] = []  # insertion order of ids

    def upsert(self, order: OrderData) -> OrderData:
        """Insert or replace an order; keep first-seen order of ids."""
        oid = str(order.orderid or "")
        if not oid:
            return order
        if oid not in self._orders:
            self._order.append(oid)
        self._orders[oid] = order
        return order

    def get(self, orderid: str) -> OrderData | None:
        """Return one order or None."""
        return self._orders.get(str(orderid or ""))

    def active(self) -> list[OrderData]:
        """Orders still working (submitting / not traded / partial)."""
        return [o for o in self._orders.values() if o.status in ACTIVE_STATUSES]

    def recent(self, limit: int = 50) -> list[OrderData]:
        """Newest-first snapshot (by upsert sequence)."""
        lim = max(1, min(int(limit or 50), 200))
        ids = list(reversed(self._order))[:lim]
        return [self._orders[i] for i in ids if i in self._orders]

    def mark(
        self,
        orderid: str,
        *,
        status: Status | None = None,
        traded: int | None = None,
        message: str | None = None,
    ) -> OrderData | None:
        """Patch status / traded qty on an existing order."""
        order = self.get(orderid)
        if not order:
            return None
        if status is not None:
            order.status = status
        if traded is not None:
            order.traded = int(traded)
        if message is not None:
            order.message = message
        self._orders[orderid] = order
        return order

    def clear(self) -> None:
        """Drop all tracked orders (tests / epoch wipe)."""
        self._orders.clear()
        self._order.clear()

    def to_dict(self, *, limit: int = 50) -> dict[str, Any]:
        """Ops snapshot."""
        return {
            "active_n": len(self.active()),
            "tracked_n": len(self._orders),
            "active": [o.to_dict() for o in self.active()],
            "recent": [o.to_dict() for o in self.recent(limit)],
        }
