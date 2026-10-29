"""Execution layer inspired by vnpy (gateway / order / risk / reconcile).

Paper gateway wraps ``sim_desk``. Live broker attach is hard-blocked until
``portfolio_stats().gate_ok``. Do not deploy full VeighNa on the small VPS.
"""

from market_desk.exec.engine import ExecEngine, get_exec_engine
from market_desk.exec.gateway import BaseGateway
from market_desk.exec.orders import OrderBook
from market_desk.exec.paper import PaperGateway
from market_desk.exec.reconcile import (
    PositionDiff,
    reconcile_positions,
    reconcile_trade_counts,
    self_check_gateway,
)
from market_desk.exec.risk import (
    RiskVerdict,
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
    "Direction",
    "ExecEngine",
    "Offset",
    "OrderBook",
    "OrderData",
    "OrderRequest",
    "OrderType",
    "PaperGateway",
    "PositionData",
    "PositionDiff",
    "RiskVerdict",
    "Status",
    "TradeData",
    "check_order",
    "check_position_limits",
    "check_session",
    "get_exec_engine",
    "reconcile_positions",
    "reconcile_trade_counts",
    "require_start_gate",
    "self_check_gateway",
]
