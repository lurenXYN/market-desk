"""Stage-1 read-only broker gateway (snapshot ingest; no Send).

Broker-agnostic: load account / positions / trades from a JSON file or an
HTTP ingest payload. ``send_order`` always rejects — stage 2 owns live Send
after ``gate_ok`` and an explicit human confirm.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from market_desk.exec.gateway import BaseGateway
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

log = logging.getLogger(__name__)


def _as_direction(raw: Any) -> Direction:
    text = str(raw or "LONG").upper()
    if text in ("SELL", "SHORT", "S"):
        return Direction.SHORT
    return Direction.LONG


def _as_offset(raw: Any, *, direction: Direction) -> Offset:
    text = str(raw or "").upper()
    if text in ("OPEN", "CLOSE", "NONE"):
        return Offset(text)
    return Offset.OPEN if direction == Direction.LONG else Offset.CLOSE


def positions_from_payload(rows: list[Any] | None) -> list[PositionData]:
    """Build ``PositionData`` rows from a broker/export JSON list."""
    out: list[PositionData] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        code = normalize_code(raw.get("code")) or str(raw.get("code") or "").zfill(6)
        try:
            volume = int(raw.get("volume") if raw.get("volume") is not None else raw.get("qty") or 0)
        except (TypeError, ValueError):
            volume = 0
        if not code or volume <= 0:
            continue
        try:
            price = float(raw.get("price") if raw.get("price") is not None else raw.get("cost") or 0)
        except (TypeError, ValueError):
            price = 0.0
        last = raw.get("last")
        try:
            last_f = None if last is None else float(last)
        except (TypeError, ValueError):
            last_f = None
        try:
            yd = int(raw.get("yd_volume") if raw.get("yd_volume") is not None else raw.get("available") or 0)
        except (TypeError, ValueError):
            yd = 0
        out.append(
            PositionData(
                code=code,
                volume=volume,
                price=price,
                last=last_f,
                name=str(raw.get("name") or ""),
                gateway=str(raw.get("gateway") or "broker_ro"),
                yd_volume=max(0, yd),
                reference=str(raw.get("reference") or ""),
            )
        )
    return out


def trades_from_payload(rows: list[Any] | None) -> list[TradeData]:
    """Build ``TradeData`` rows from a broker/export JSON list."""
    out: list[TradeData] = []
    for i, raw in enumerate(rows or []):
        if not isinstance(raw, dict):
            continue
        code = normalize_code(raw.get("code")) or str(raw.get("code") or "").zfill(6)
        try:
            volume = int(raw.get("volume") if raw.get("volume") is not None else raw.get("qty") or 0)
        except (TypeError, ValueError):
            volume = 0
        if not code or volume <= 0:
            continue
        direction = _as_direction(raw.get("direction") or raw.get("side"))
        offset = _as_offset(raw.get("offset"), direction=direction)
        try:
            price = float(raw.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        try:
            fee = float(raw.get("fee") or 0)
        except (TypeError, ValueError):
            fee = 0.0
        out.append(
            TradeData(
                code=code,
                orderid=str(raw.get("orderid") or raw.get("order_id") or ""),
                tradeid=str(raw.get("tradeid") or raw.get("trade_id") or f"ro-{i+1}"),
                direction=direction,
                offset=offset,
                volume=volume,
                price=price,
                fee=fee,
                name=str(raw.get("name") or ""),
                gateway=str(raw.get("gateway") or "broker_ro"),
                datetime=str(raw.get("datetime") or raw.get("time") or "")[:19],
                pnl_cny=(
                    None
                    if raw.get("pnl_cny") is None
                    else float(raw.get("pnl_cny") or 0)
                ),
            )
        )
    return out


def account_from_payload(raw: dict[str, Any] | None, *, gateway: str) -> AccountData | None:
    """Build ``AccountData`` from a broker/export account object."""
    if not isinstance(raw, dict):
        return None
    try:
        balance = float(raw.get("balance") if raw.get("balance") is not None else raw.get("equity") or 0)
        available = float(
            raw.get("available") if raw.get("available") is not None else raw.get("cash") or balance
        )
        frozen = float(raw.get("frozen") or 0)
        initial = raw.get("initial")
        initial_f = None if initial is None else float(initial)
    except (TypeError, ValueError):
        return None
    return AccountData(
        gateway=gateway,
        balance=balance,
        available=available,
        frozen=frozen,
        initial=initial_f,
    )


def desk_positions_to_data(rows: list[dict[str, Any]] | None) -> list[PositionData]:
    """Map desk ``positions`` table rows into gateway ``PositionData``."""
    out: list[PositionData] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        code = normalize_code(raw.get("code")) or str(raw.get("code") or "").zfill(6)
        try:
            volume = int(raw.get("qty") if raw.get("qty") is not None else raw.get("volume") or 0)
        except (TypeError, ValueError):
            volume = 0
        if not code or volume <= 0:
            continue
        try:
            price = float(
                raw.get("buy_price")
                if raw.get("buy_price") is not None
                else (raw.get("cost") if raw.get("cost") is not None else raw.get("price") or 0)
            )
        except (TypeError, ValueError):
            price = 0.0
        last = raw.get("last") or raw.get("price_now")
        try:
            last_f = None if last is None else float(last)
        except (TypeError, ValueError):
            last_f = None
        out.append(
            PositionData(
                code=code,
                volume=volume,
                price=price,
                last=last_f,
                name=str(raw.get("name") or ""),
                gateway="desk",
                yd_volume=volume,
                reference=str(raw.get("id") or ""),
            )
        )
    return out


class ReadonlyBrokerGateway(BaseGateway):
    """In-memory / file-backed broker snapshot; Send always rejected."""

    default_name = "broker_ro"

    def __init__(self, gateway_name: str | None = None) -> None:
        super().__init__(gateway_name)
        self._account: AccountData | None = None
        self._positions: list[PositionData] = []
        self._trades: list[TradeData] = []
        self._source: str = ""
        self._loaded_at: str = ""

    def connect(self, setting: dict[str, Any] | None = None) -> bool:
        """Connect and optionally load a snapshot from ``path`` / ``payload``."""
        setting = setting or {}
        payload = setting.get("payload")
        path = setting.get("path") or setting.get("file")
        if isinstance(payload, dict):
            self.load_snapshot(payload, source="payload")
        elif path:
            self.load_file(str(path))
        self._connected = True
        return True

    def load_file(self, path: str) -> dict[str, Any]:
        """Load JSON snapshot from disk."""
        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("broker snapshot must be a JSON object")
        return self.load_snapshot(raw, source=str(p))

    def load_snapshot(self, payload: dict[str, Any], *, source: str = "payload") -> dict[str, Any]:
        """Replace in-memory account / positions / trades from one export."""
        from market_desk.calendar import now_cn

        gw = str(payload.get("gateway") or self.gateway_name)
        self.gateway_name = gw or self.gateway_name
        self._account = account_from_payload(
            payload.get("account") if isinstance(payload.get("account"), dict) else payload,
            gateway=self.gateway_name,
        )
        # Prefer nested account; if top-level only has balance keys, already handled.
        if self._account is None and isinstance(payload.get("account"), dict):
            self._account = account_from_payload(payload["account"], gateway=self.gateway_name)
        self._positions = positions_from_payload(
            payload.get("positions") if isinstance(payload.get("positions"), list) else []
        )
        self._trades = trades_from_payload(
            payload.get("trades") if isinstance(payload.get("trades"), list) else []
        )
        self._source = source
        self._loaded_at = now_cn().strftime("%Y-%m-%d %H:%M:%S")
        self._connected = True
        return {
            "ok": True,
            "gateway": self.gateway_name,
            "source": self._source,
            "loaded_at": self._loaded_at,
            "position_n": len(self._positions),
            "trade_n": len(self._trades),
            "account": self._account.to_dict() if self._account else None,
        }

    def save_file(self, path: str) -> None:
        """Persist the current snapshot for the next process attach."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "gateway": self.gateway_name,
            "loaded_at": self._loaded_at,
            "account": self._account.to_dict() if self._account else None,
            "positions": [x.to_dict() for x in self._positions],
            "trades": [x.to_dict() for x in self._trades],
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def query_account(self) -> AccountData | None:
        """Return the last ingested account snapshot."""
        return self._account

    def query_positions(self) -> list[PositionData]:
        """Return the last ingested positions."""
        return list(self._positions)

    def query_trades(self, *, limit: int = 80) -> list[TradeData]:
        """Return recent ingested fills (newest last, truncated)."""
        rows = list(self._trades)
        if limit and len(rows) > int(limit):
            return rows[-int(limit) :]
        return rows

    def send_order(self, req: OrderRequest) -> OrderData:
        """Always reject — stage 1 is read-only."""
        return OrderData(
            code=str(req.code or ""),
            orderid="",
            direction=req.direction,
            offset=req.offset,
            volume=int(req.volume or 0),
            price=float(req.price or 0),
            status=Status.REJECTED,
            message="readonly_stage1",
            gateway=self.gateway_name,
            name=req.name,
            reference=req.reference,
        )

    def cancel_order(self, req: CancelRequest) -> bool:
        """Cancels are unsupported in the read-only stage."""
        del req
        return False

    def snapshot(self) -> dict[str, Any]:
        """Ops dump including ingest provenance."""
        base = super().snapshot()
        base["source"] = self._source
        base["loaded_at"] = self._loaded_at
        base["readonly"] = True
        return base
