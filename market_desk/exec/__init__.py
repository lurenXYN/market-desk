"""Execution layer inspired by vnpy (gateway / order / risk / reconcile).

Paper gateway wraps ``sim_desk``. Dual start gates:
``gate_readonly_ok`` (relative excess) unlocks broker attach / 只读;
``gate_ok`` (absolute equity > 0) unlocks live Send. Do not deploy full
VeighNa on the small VPS.
"""

from market_desk.exec.circuit import CircuitBreaker, is_open_intent
from market_desk.exec.condition import (
    ExecTrigger,
    TriggerMode,
    sell_advice_to_triggers,
    sell_item_to_trigger,
)
from market_desk.exec.engine import ExecEngine, get_exec_engine, reset_exec_engine_for_tests
from market_desk.exec.gateway import BaseGateway
from market_desk.exec.orders import OrderBook
from market_desk.exec.paper import PaperGateway
from market_desk.exec.ptrade_bridge import (
    build_bridge_ingest_payload,
    build_exec_intents,
    normalize_ptrade_positions,
    set_bridge_token,
    verify_bridge_token,
)
from market_desk.exec.readonly import (
    ReadonlyBrokerGateway,
    desk_positions_to_data,
    positions_from_payload,
    trades_from_payload,
)
from market_desk.exec.reconcile import (
    PositionDiff,
    reconcile_positions,
    reconcile_trade_counts,
    self_check_gateway,
)
from market_desk.exec.risk import (
    RiskVerdict,
    check_circuit_open,
    check_order,
    check_position_limits,
    check_session,
    require_start_gate,
)
from market_desk.exec.types import (
    AccountData,
    CancelRequest,
    Direction,
    Offset,
    OrderData,
    OrderRequest,
    OrderType,
    PositionData,
    Status,
    TradeData,
)

__all__ = [
    "AccountData",
    "BaseGateway",
    "CancelRequest",
    "CircuitBreaker",
    "Direction",
    "ExecEngine",
    "ExecTrigger",
    "Offset",
    "OrderBook",
    "OrderData",
    "OrderRequest",
    "OrderType",
    "PaperGateway",
    "PositionData",
    "PositionDiff",
    "ReadonlyBrokerGateway",
    "RiskVerdict",
    "Status",
    "TradeData",
    "TriggerMode",
    "build_bridge_ingest_payload",
    "build_exec_intents",
    "check_circuit_open",
    "check_order",
    "check_position_limits",
    "check_session",
    "desk_positions_to_data",
    "get_exec_engine",
    "is_open_intent",
    "normalize_ptrade_positions",
    "positions_from_payload",
    "reconcile_positions",
    "reconcile_trade_counts",
    "require_start_gate",
    "reset_exec_engine_for_tests",
    "self_check_gateway",
    "sell_advice_to_triggers",
    "sell_item_to_trigger",
    "set_bridge_token",
    "trades_from_payload",
    "verify_bridge_token",
]
