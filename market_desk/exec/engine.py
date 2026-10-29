"""Execution facade: paper now; live broker only after start-gate."""

from __future__ import annotations

from typing import Any

from market_desk.exec.gateway import BaseGateway
from market_desk.exec.orders import OrderBook
from market_desk.exec.paper import PaperGateway
from market_desk.exec.reconcile import reconcile_positions, self_check_gateway
from market_desk.exec.risk import require_start_gate
from market_desk.exec.types import OrderData, OrderRequest
from market_desk.sim_desk import portfolio_stats, start_gate_snapshot


class ExecEngine:
    """Route order intent to the active gateway.

    Stage map (TODO):
      0    paper   — always on (this class default)
      0.5  layer   — gateway + risk + OrderBook
      0.6  high    — limits + reconcile skeleton
      1    read    — live query_* only, after gate_ok
      2    click   — send_order with human confirm, after 1
      3    auto    — ready/sell → send_order, after 2 + user OK
    """

    def __init__(self, gateway: BaseGateway | None = None) -> None:
        self.paper = gateway if gateway is not None else PaperGateway()
        if not self.paper.connected:
            self.paper.connect()
        self.orders = OrderBook()
        self._live: BaseGateway | None = None

    @property
    def active(self) -> BaseGateway:
        """Paper until a live gateway is attached and gate is green."""
        if self._live is not None and require_start_gate(live=True).ok:
            return self._live
        return self.paper

    def attach_live(self, gateway: BaseGateway) -> dict[str, Any]:
        """Register a live broker gateway; refused while start-gate is red."""
        verdict = require_start_gate(live=True)
        if not verdict.ok:
            return {
                "ok": False,
                "reason": verdict.reason,
                "hint": "sim portfolio must be green (gate_ok) before live gateway",
            }
        self._live = gateway
        return {"ok": True, "gateway": gateway.gateway_name}

    def detach_live(self) -> None:
        """Drop the live gateway (paper remains)."""
        if self._live is not None:
            try:
                self._live.close()
            except Exception:
                pass
        self._live = None

    def send_order(self, req: OrderRequest, *, live: bool = False) -> OrderData:
        """Send via paper (default) or live; always upsert into OrderBook."""
        if live:
            verdict = require_start_gate(live=True)
            if not verdict.ok:
                order = OrderData(
                    code=req.code,
                    orderid="",
                    direction=req.direction,
                    offset=req.offset,
                    volume=int(req.volume or 0),
                    price=float(req.price or 0),
                    status=verdict.status,
                    message=verdict.reason,
                    gateway="live",
                    name=req.name,
                    reference=req.reference,
                )
                return self.orders.upsert(order) if order.orderid else order
            if self._live is None:
                order = OrderData(
                    code=req.code,
                    orderid="",
                    direction=req.direction,
                    offset=req.offset,
                    volume=int(req.volume or 0),
                    price=float(req.price or 0),
                    status=verdict.status,
                    message="live_gateway_not_attached",
                    gateway="live",
                    name=req.name,
                    reference=req.reference,
                )
                return order
            filled = self._live.send_order(req)
            return self.orders.upsert(filled)
        filled = self.paper.send_order(req)
        return self.orders.upsert(filled)

    def reconcile_live(self) -> dict[str, Any]:
        """Diff paper positions vs attached live gateway (stage 1 shape)."""
        if self._live is None:
            return {
                "ok": False,
                "reason": "live_gateway_not_attached",
                "positions": None,
            }
        return {
            "ok": True,
            "positions": reconcile_positions(
                self.paper.query_positions(),
                self._live.query_positions(),
            ),
        }

    def snapshot(self) -> dict[str, Any]:
        """Ops / review dump: gate + paper + orders + self-reconcile."""
        gate = start_gate_snapshot()
        stats = portfolio_stats()
        return {
            "ok": True,
            "stage": "0.6_risk_orders_reconcile",
            "gate_ok": bool(stats.get("gate_ok")),
            "gate": gate,
            "paper": self.paper.snapshot(),
            "orders": self.orders.to_dict(),
            "self_check": self_check_gateway(self.paper),
            "live": (
                {
                    "attached": True,
                    "gateway": self._live.gateway_name,
                    "connected": self._live.connected,
                    "allowed": require_start_gate(live=True).ok,
                }
                if self._live is not None
                else {"attached": False, "allowed": require_start_gate(live=True).ok}
            ),
            "rule": (
                "参考 vnpy：决策在 desk，执行走 gateway；"
                "OrderBook + 独立风控 + 对账骨架；live 须 gate_ok；不部署 VeighNa"
            ),
        }


_engine: ExecEngine | None = None


def get_exec_engine() -> ExecEngine:
    """Process-wide execution facade (lazy singleton)."""
    global _engine
    if _engine is None:
        _engine = ExecEngine()
    return _engine
