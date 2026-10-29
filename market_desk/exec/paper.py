"""Paper gateway backed by the existing sim_desk cash book."""

from __future__ import annotations

import itertools
import logging
from datetime import datetime
from typing import Any

from market_desk.config import TRADE_FEE_CNY
from market_desk.db.sim_desk import (
    apply_sim_buy_fill,
    apply_sim_sell_fill,
    insert_sim_lot,
    load_open_sim_lots,
    load_sim_fills,
    set_sim_cash,
)
from market_desk.exec.gateway import BaseGateway
from market_desk.exec.circuit import is_open_intent
from market_desk.exec.risk import check_order
from market_desk.exec.types import (
    AccountData,
    CancelRequest,
    Direction,
    Offset,
    OrderData,
    OrderRequest,
    PositionData,
    Status,
    TradeData,
)
from market_desk.filters import normalize_code
from market_desk.sim_desk import account_snapshot, mark_to_market, net_pnl_pct

log = logging.getLogger(__name__)
FEE = float(TRADE_FEE_CNY)
_oid = itertools.count(1)


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _day() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class PaperGateway(BaseGateway):
    """Map sim_account / sim_lot / sim_fill into vnpy-style snapshots.

    ``send_order`` writes through to the paper book (manual / test path).
    Live engine sync still owns signal-driven opens via ``sim_desk.sync_from_engine``.
    Attach a ``CircuitBreaker`` via ``circuit`` so day-loss / reject streak apply.
    """

    default_name = "paper"

    def __init__(self, gateway_name: str | None = None, *, circuit: Any = None) -> None:
        super().__init__(gateway_name)
        self.circuit = circuit

    def connect(self, setting: dict[str, Any] | None = None) -> bool:
        """Ensure the sim account row exists."""
        del setting
        account_snapshot()
        self._connected = True
        return True

    def query_account(self) -> AccountData | None:
        """Return paper cash / equity as balance + available cash."""
        snap = account_snapshot()
        cash = float(snap.get("cash") or 0)
        equity = float(snap.get("equity") or cash)
        return AccountData(
            gateway=self.gateway_name,
            balance=equity,
            available=cash,
            frozen=max(0.0, equity - cash),
            initial=float(snap.get("initial_cash") or 0) or None,
        )

    def query_positions(self) -> list[PositionData]:
        """Open sim lots as positions (yd_volume = T+1 sellable heuristic)."""
        today = _day()
        out: list[PositionData] = []
        for lot in load_open_sim_lots():
            code = normalize_code(lot.get("code")) or str(lot.get("code") or "")
            qty = int(lot.get("qty") or 0)
            if not code or qty <= 0:
                continue
            open_day = str(lot.get("open_date") or "")[:10]
            yd = qty if open_day and open_day < today else 0
            out.append(
                PositionData(
                    code=code,
                    volume=qty,
                    price=float(lot.get("open_px") or 0),
                    last=(
                        float(lot["last_px"])
                        if lot.get("last_px") is not None
                        else None
                    ),
                    name=str(lot.get("name") or ""),
                    gateway=self.gateway_name,
                    yd_volume=yd,
                    reference=str(lot.get("id") or ""),
                )
            )
        return out

    def query_trades(self, *, limit: int = 80) -> list[TradeData]:
        """Map sim fills to TradeData (buy=LONG/OPEN, sell=SHORT/CLOSE)."""
        trades: list[TradeData] = []
        for f in load_sim_fills(limit=max(1, min(int(limit), 300))):
            side = str(f.get("side") or "").lower()
            if side == "buy":
                direction, offset = Direction.LONG, Offset.OPEN
            elif side == "sell":
                direction, offset = Direction.SHORT, Offset.CLOSE
            else:
                continue
            code = normalize_code(f.get("code")) or str(f.get("code") or "")
            trades.append(
                TradeData(
                    code=code,
                    orderid=f"sim-{f.get('lot_id')}-{f.get('id')}",
                    tradeid=str(f.get("id") or ""),
                    direction=direction,
                    offset=offset,
                    volume=int(f.get("qty") or 0),
                    price=float(f.get("px") or 0),
                    fee=float(f.get("fee") or 0),
                    name=str(f.get("name") or ""),
                    gateway=self.gateway_name,
                    datetime=str(f.get("filled_at") or f.get("trade_date") or "")[:19],
                    pnl_cny=(
                        float(f["pnl_cny"]) if f.get("pnl_cny") is not None else None
                    ),
                )
            )
        return trades

    def send_order(self, req: OrderRequest) -> OrderData:
        """Validate and immediately fill against the paper cash book."""
        if not self._connected:
            self.connect()
        orderid = f"P{next(_oid):06d}"
        base = OrderData(
            code=normalize_code(req.code) or str(req.code or "").zfill(6),
            orderid=orderid,
            direction=req.direction,
            offset=req.offset,
            volume=int(req.volume or 0),
            price=float(req.price or 0),
            order_type=req.order_type,
            name=req.name or "",
            reference=req.reference or "",
            gateway=self.gateway_name,
            datetime=_stamp(),
        )
        verdict = check_order(
            req,
            live=False,
            account=self.query_account(),
            positions=self.query_positions(),
            session_guard=False,
            circuit=self.circuit,
        )
        open_intent = is_open_intent(req)
        if not verdict.ok:
            base.status = Status.REJECTED
            base.message = verdict.reason
            self._note_reject(verdict.reason, is_open=open_intent)
            return base
        try:
            if req.direction == Direction.LONG and req.offset == Offset.OPEN:
                filled = self._fill_buy(base, req)
            elif req.direction == Direction.SHORT and req.offset == Offset.CLOSE:
                filled = self._fill_sell(base, req)
            else:
                base.status = Status.REJECTED
                base.message = "unsupported_side"
                filled = base
            if filled.status == Status.REJECTED:
                self._note_reject(filled.message, is_open=open_intent)
            elif filled.status == Status.ALLTRADED:
                self._note_fill()
            return filled
        except Exception as exc:
            log.exception("paper send_order failed")
            base.status = Status.REJECTED
            base.message = str(exc)[:160]
            self._note_reject(base.message, is_open=open_intent)
            return base

    def _note_reject(self, reason: str, *, is_open: bool = True) -> None:
        """Feed consecutive-reject circuit (open attempts only)."""
        if self.circuit is None or not is_open:
            return
        try:
            self.circuit.note_reject(reason)
        except Exception:
            log.debug("circuit note_reject failed", exc_info=True)

    def _note_fill(self) -> None:
        """Reset reject streak after a successful paper fill."""
        if self.circuit is None:
            return
        try:
            self.circuit.note_fill()
        except Exception:
            log.debug("circuit note_fill failed", exc_info=True)

    def cancel_order(self, req: CancelRequest) -> bool:
        """Paper fills are immediate — nothing to cancel."""
        del req
        return False

    def _fill_buy(self, order: OrderData, req: OrderRequest) -> OrderData:
        """Open a sim lot and debit cash."""
        snap = account_snapshot()
        cash = float(snap["cash"])
        px = float(req.price)
        qty = int(req.volume)
        notional = px * qty
        if cash < notional + FEE:
            order.status = Status.REJECTED
            order.message = "insufficient_cash"
            return order
        day = _day()
        at = _stamp()
        cash_after = cash - notional - FEE
        lot_id = insert_sim_lot(
            {
                "code": order.code,
                "name": order.name or order.code,
                "kind": "stock",
                "signal_type": "buy",
                "open_date": day,
                "open_at": at,
                "open_px": px,
                "open_weight": 1.0,
                "size_tag": "manual",
                "qty": qty,
                "qty_open": qty,
                "buy_fee": FEE,
                "peak_px": px,
                "last_px": px,
                "payload": {"source": "paper_gateway", "reference": req.reference},
            }
        )
        if not lot_id:
            order.status = Status.REJECTED
            order.message = "lot_insert_failed"
            return order
        set_sim_cash(cash_after, initial_cash=float(snap["initial_cash"]))
        apply_sim_buy_fill(
            lot_id=int(lot_id),
            trade_date=day,
            filled_at=at,
            px=px,
            qty=qty,
            fee=FEE,
            cash_after=cash_after,
            weight=1.0,
            note=req.reference or "paper_buy",
        )
        order.traded = qty
        order.status = Status.ALLTRADED
        order.message = f"lot:{lot_id}"
        return order

    def _fill_sell(self, order: OrderData, req: OrderRequest) -> OrderData:
        """Close against an open sim lot (T+1: prefer lots opened before today)."""
        today = _day()
        need = int(req.volume)
        px = float(req.price)
        lots = [
            lot
            for lot in load_open_sim_lots()
            if normalize_code(lot.get("code")) == order.code
            and int(lot.get("qty") or 0) > 0
        ]
        # Prefer T+1 eligible lots first.
        lots.sort(
            key=lambda lot: (
                0 if str(lot.get("open_date") or "")[:10] < today else 1,
                int(lot.get("id") or 0),
            )
        )
        if not lots:
            order.status = Status.REJECTED
            order.message = "no_position"
            return order
        lot = lots[0]
        open_day = str(lot.get("open_date") or "")[:10]
        if open_day >= today:
            order.status = Status.REJECTED
            order.message = "t1_locked"
            return order
        held = int(lot.get("qty") or 0)
        sell_qty = min(need, held)
        if sell_qty <= 0 or sell_qty % 100 != 0:
            order.status = Status.REJECTED
            order.message = "bad_sell_qty"
            return order
        snap = account_snapshot()
        cash = float(snap["cash"])
        proceeds = sell_qty * px
        if proceeds <= FEE:
            order.status = Status.REJECTED
            order.message = "proceeds_too_small"
            return order
        cash_after = cash + proceeds - FEE
        entry = float(lot.get("open_px") or 0)
        buy_fee_alloc = float(lot.get("buy_fee") or 0) * (
            sell_qty / float(lot.get("qty_open") or held or sell_qty)
        )
        pnl_cny = proceeds - FEE - entry * sell_qty - buy_fee_alloc
        pnl = net_pnl_pct(
            entry, px, fee_buy=buy_fee_alloc, fee_sell=FEE, qty=sell_qty
        )
        frac = sell_qty / float(lot.get("qty_open") or held or sell_qty)
        mode = "clear" if sell_qty >= held else "half"
        ok = apply_sim_sell_fill(
            int(lot["id"]),
            trade_date=today,
            filled_at=_stamp(),
            px=px,
            qty=sell_qty,
            fee=FEE,
            cash_after=cash_after,
            frac=frac,
            weight=float(lot.get("open_weight") or 1.0) * frac,
            pnl_pct=pnl,
            pnl_cny=pnl_cny,
            exit_mode=mode,
            role_label="paper_sell",
            note=req.reference or "paper_sell",
        )
        if not ok:
            order.status = Status.REJECTED
            order.message = "sell_fill_failed"
            return order
        set_sim_cash(cash_after, initial_cash=float(snap["initial_cash"]))
        # Keep mark book consistent for equity.
        mark_to_market()
        order.volume = sell_qty
        order.traded = sell_qty
        order.status = Status.ALLTRADED
        order.message = f"lot:{lot['id']}:{mode}"
        return order
