"""Counterfactual (What-If) gate net-value audit: trace, fill simulation, ledger."""

from market_desk.counterfactual.audit import bootstrap_ci, build_counterfactual_audit
from market_desk.counterfactual.sim import simulate_cf_trade
from market_desk.counterfactual.trace import block_arm_reasons, item_blockers, merge_trace, trace_now

__all__ = [
    "block_arm_reasons",
    "bootstrap_ci",
    "build_counterfactual_audit",
    "item_blockers",
    "merge_trace",
    "simulate_cf_trade",
    "trace_now",
]
