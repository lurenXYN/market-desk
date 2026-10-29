"""Execution-layer data objects (vnpy-inspired, desk-sized).

Decision stays in market-desk; this package only models orders / fills /
positions for paper and future broker gateways. No EventEngine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Direction(str, Enum):
    """Order side."""

    LONG = "LONG"  # buy to open / cover
    SHORT = "SHORT"  # sell to close / short


class Offset(str, Enum):
    """Open vs close (A-share cash book uses OPEN / CLOSE)."""

    OPEN = "OPEN"
    CLOSE = "CLOSE"
    NONE = "NONE"


class OrderType(str, Enum):
    """Order type."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"


class Status(str, Enum):
    """Order lifecycle (subset of vnpy Status)."""

    SUBMITTING = "SUBMITTING"
    NOTTRADED = "NOTTRADED"
    PARTTRADED = "PARTTRADED"
    ALLTRADED = "ALLTRADED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


ACTIVE_STATUSES = frozenset(
    {Status.SUBMITTING, Status.NOTTRADED, Status.PARTTRADED}
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class OrderRequest:
    """Outbound order intent from the decision layer."""

    code: str
    direction: Direction
    offset: Offset
    volume: int
    price: float = 0.0
    order_type: OrderType = OrderType.LIMIT
    name: str = ""
    reference: str = ""  # signal id / sim lot id / note
    gateway: str = "paper"

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logs / API."""
        d = asdict(self)
        d["direction"] = self.direction.value
        d["offset"] = self.offset.value
        d["order_type"] = self.order_type.value
        return d


@dataclass
class CancelRequest:
    """Cancel an active order by gateway order id."""

    orderid: str
    code: str = ""
    gateway: str = "paper"


@dataclass
class OrderData:
    """Latest order state."""

    code: str
    orderid: str
    direction: Direction
    offset: Offset
    volume: int
    traded: int = 0
    price: float = 0.0
    status: Status = Status.SUBMITTING
    order_type: OrderType = OrderType.LIMIT
    name: str = ""
    reference: str = ""
    gateway: str = "paper"
    datetime: str = field(default_factory=_now)
    message: str = ""

    @property
    def is_active(self) -> bool:
        """Return True while the order can still fill or cancel."""
        return self.status in ACTIVE_STATUSES

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logs / API."""
        return {
            "code": self.code,
            "orderid": self.orderid,
            "direction": self.direction.value,
            "offset": self.offset.value,
            "volume": self.volume,
            "traded": self.traded,
            "price": self.price,
            "status": self.status.value,
            "order_type": self.order_type.value,
            "name": self.name,
            "reference": self.reference,
            "gateway": self.gateway,
            "datetime": self.datetime,
            "message": self.message,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "OrderData | None":
        """Rebuild from a persisted ``to_dict`` payload; return None if invalid."""
        if not isinstance(raw, dict):
            return None
        oid = str(raw.get("orderid") or "")
        code = str(raw.get("code") or "")
        if not oid or not code:
            return None
        try:
            direction = Direction(str(raw.get("direction") or "LONG"))
            offset = Offset(str(raw.get("offset") or "OPEN"))
            status = Status(str(raw.get("status") or "SUBMITTING"))
            order_type = OrderType(str(raw.get("order_type") or "LIMIT"))
        except ValueError:
            return None
        return cls(
            code=code,
            orderid=oid,
            direction=direction,
            offset=offset,
            volume=int(raw.get("volume") or 0),
            traded=int(raw.get("traded") or 0),
            price=float(raw.get("price") or 0),
            status=status,
            order_type=order_type,
            name=str(raw.get("name") or ""),
            reference=str(raw.get("reference") or ""),
            gateway=str(raw.get("gateway") or "paper"),
            datetime=str(raw.get("datetime") or "")[:19],
            message=str(raw.get("message") or ""),
        )


@dataclass
class TradeData:
    """One fill (partial or full)."""

    code: str
    orderid: str
    tradeid: str
    direction: Direction
    offset: Offset
    volume: int
    price: float
    fee: float = 0.0
    name: str = ""
    gateway: str = "paper"
    datetime: str = field(default_factory=_now)
    pnl_cny: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logs / API."""
        return {
            "code": self.code,
            "orderid": self.orderid,
            "tradeid": self.tradeid,
            "direction": self.direction.value,
            "offset": self.offset.value,
            "volume": self.volume,
            "price": self.price,
            "fee": self.fee,
            "name": self.name,
            "gateway": self.gateway,
            "datetime": self.datetime,
            "pnl_cny": self.pnl_cny,
        }


@dataclass
class PositionData:
    """Open position snapshot."""

    code: str
    volume: int
    price: float  # cost
    last: float | None = None
    name: str = ""
    gateway: str = "paper"
    yd_volume: int = 0  # T+1 sellable
    reference: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logs / API."""
        return asdict(self)


@dataclass
class AccountData:
    """Cash account snapshot."""

    gateway: str
    balance: float
    available: float
    frozen: float = 0.0
    initial: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logs / API."""
        return asdict(self)
