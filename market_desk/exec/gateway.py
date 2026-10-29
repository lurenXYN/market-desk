"""Abstract trading gateway (vnpy BaseGateway subset, sync-only)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from market_desk.exec.types import (
    AccountData,
    CancelRequest,
    OrderData,
    OrderRequest,
    PositionData,
    TradeData,
)


class BaseGateway(ABC):
    """Thin sync gateway: connect / query / send / cancel.

    Unlike vnpy, there is no EventEngine — callers pull snapshots or read the
    return values of ``send_order``. Live broker subclasses must stay out of
    process until ``gate_ok`` (see ``market_desk.exec.risk``).
    """

    default_name: str = ""

    def __init__(self, gateway_name: str | None = None) -> None:
        self.gateway_name = gateway_name or self.default_name or "gateway"
        self._connected = False

    @property
    def connected(self) -> bool:
        """Return True after a successful ``connect``."""
        return self._connected

    @abstractmethod
    def connect(self, setting: dict[str, Any] | None = None) -> bool:
        """Open the connection. Return True on success."""

    def close(self) -> None:
        """Close the connection (default: mark disconnected)."""
        self._connected = False

    @abstractmethod
    def query_account(self) -> AccountData | None:
        """Return cash account snapshot."""

    @abstractmethod
    def query_positions(self) -> list[PositionData]:
        """Return open positions."""

    @abstractmethod
    def send_order(self, req: OrderRequest) -> OrderData:
        """Submit an order; return final or accepted order state."""

    def cancel_order(self, req: CancelRequest) -> bool:
        """Cancel an active order. Default: unsupported."""
        del req
        return False

    def query_trades(self, *, limit: int = 80) -> list[TradeData]:
        """Optional recent fills. Default: empty."""
        del limit
        return []

    def snapshot(self) -> dict[str, Any]:
        """Ops-friendly dict of account + positions + recent trades."""
        acct = self.query_account()
        return {
            "gateway": self.gateway_name,
            "connected": self.connected,
            "account": acct.to_dict() if acct else None,
            "positions": [p.to_dict() for p in self.query_positions()],
            "trades": [t.to_dict() for t in self.query_trades()],
        }
