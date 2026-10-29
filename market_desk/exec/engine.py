"""Execution facade: paper now; live broker only after start-gate."""

from __future__ import annotations

from typing import Any

from market_desk.exec.circuit import CircuitBreaker
from market_desk.exec.condition import sell_advice_to_triggers
from market_desk.exec.gateway import BaseGateway
from market_desk.exec.orders import OrderBook
from market_desk.exec.paper import PaperGateway
from market_desk.exec.reconcile import reconcile_positions, self_check_gateway
from market_desk.exec.risk import require_start_gate
from market_desk.exec.types import OrderData, OrderRequest, Status
from market_desk.sim_desk import portfolio_stats, start_gate_snapshot


class ExecEngine:
    """Route order intent to the active gateway.

    Stage map (TODO):
      0    paper   — always on (this class default)
      0.5  layer   — gateway + risk + OrderBook
      0.6  high    — limits + reconcile skeleton
      0.7  circuit — day-loss / reject-streak + order persist + sell triggers
      1    read    — live query_* only, after gate_ok
      2    click   — send_order with human confirm, after 1
      3    auto    — ready/sell → send_order, after 2 + user OK
    """

    def __init__(
        self,
        gateway: BaseGateway | None = None,
        *,
        persist: bool = True,
    ) -> None:
        self.paper = gateway if gateway is not None else PaperGateway()
        if not self.paper.connected:
            self.paper.connect()
        self.orders = OrderBook(persist=persist)
        self.circuit = CircuitBreaker.load() if persist else CircuitBreaker()
        if isinstance(self.paper, PaperGateway):
            self.paper.circuit = self.circuit
        if persist:
            try:
                self.orders.restore()
            except Exception:
                pass
            # Freeze day-start equity from current paper book when missing.
            try:
                acct = self.paper.query_account()
                if acct is not None:
                    self.circuit.ensure_day_start(float(acct.balance))
                    self.circuit.persist()
            except Exception:
                pass
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
                    status=Status.REJECTED,
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

    def sell_triggers(self, advice: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Map sell-advice rows into unified exec trigger intents."""
        if advice is None:
            try:
                from market_desk.verdict.sell_advice import build_sell_advice

                advice = build_sell_advice()
            except Exception:
                advice = {}
        return sell_advice_to_triggers(advice)

    def snapshot(self) -> dict[str, Any]:
        """Ops / review dump: gate + paper + orders + circuit + self-reconcile."""
        gate = start_gate_snapshot()
        stats = portfolio_stats()
        circ = self.circuit.to_dict()
        tripped = bool(circ.get("tripped"))
        triggers = []
        try:
            triggers = self.sell_triggers()
        except Exception:
            triggers = []
        by_mode: dict[str, int] = {}
        exec_n = 0
        for t in triggers:
            mode = str(t.get("mode") or "?")
            by_mode[mode] = by_mode.get(mode, 0) + 1
            if t.get("executable"):
                exec_n += 1
        return {
            "ok": True,
            "stage": "0.7_circuit_persist_triggers",
            "gate_ok": bool(stats.get("gate_ok")),
            "gate": gate,
            "paper": self.paper.snapshot(),
            "orders": self.orders.to_dict(),
            "circuit": circ,
            "circuit_view": {
                "tone": (
                    "bad"
                    if tripped
                    else (
                        "warn"
                        if int(circ.get("reject_streak") or 0) > 0
                        else "ok"
                    )
                ),
                "label": (
                    "日损熔断·拦开仓"
                    if circ.get("day_loss_tripped")
                    else (
                        "连拒熔断·拦开仓"
                        if circ.get("reject_tripped")
                        else (
                            f"连拒 {circ.get('reject_streak')}/{circ.get('reject_streak_max')}"
                            if int(circ.get("reject_streak") or 0) > 0
                            else "熔断未触发"
                        )
                    )
                ),
                "tripped": tripped,
                "day_start_equity": circ.get("day_start_equity"),
                "reject_streak": circ.get("reject_streak"),
                "reject_streak_max": circ.get("reject_streak_max"),
                "last_reject_reason": circ.get("last_reject_reason"),
            },
            "triggers_summary": {
                "n": len(triggers),
                "executable_n": exec_n,
                "by_mode": by_mode,
                "top": triggers[:12],
                "sample": triggers[:8],
            },
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
                "OrderBook 持久化 + 日损/连拒熔断 + 卖点条件触发语义；"
                "live 须 gate_ok；不部署 VeighNa"
            ),
        }


_engine: ExecEngine | None = None


def get_exec_engine() -> ExecEngine:
    """Process-wide execution facade (lazy singleton)."""
    global _engine
    if _engine is None:
        _engine = ExecEngine()
    return _engine


def reset_exec_engine_for_tests() -> None:
    """Drop the process singleton (unit tests only)."""
    global _engine
    _engine = None
