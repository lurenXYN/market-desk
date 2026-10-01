"""Order-flow microstructure gates: auction alpha, volume absorption, slippage."""

from market_desk.microstructure.absorption import (
    ABSORB_FAIL_LABELS,
    apply_absorption_gates,
    evaluate_absorption,
    outer_share,
    tick_rule_buy_share,
    unconfirmed_warning,
    vwap_series,
    vwap_slope_pct,
)
from market_desk.microstructure.auction_alpha import (
    STRONG_LABEL,
    TRAP_LABEL,
    apply_auction_alpha,
    auction_sample,
    compute_alpha,
    evaluate_tape,
    in_tape_window,
    record_tape,
    reset_tape,
    tape_snapshot,
)
from market_desk.microstructure.slippage import (
    apply_slippage_filter,
    estimate_buy_impact,
    session_minutes_elapsed,
)

__all__ = [
    "ABSORB_FAIL_LABELS",
    "STRONG_LABEL",
    "TRAP_LABEL",
    "apply_absorption_gates",
    "apply_auction_alpha",
    "apply_slippage_filter",
    "auction_sample",
    "compute_alpha",
    "estimate_buy_impact",
    "evaluate_absorption",
    "evaluate_tape",
    "in_tape_window",
    "outer_share",
    "record_tape",
    "reset_tape",
    "session_minutes_elapsed",
    "tape_snapshot",
    "tick_rule_buy_share",
    "unconfirmed_warning",
    "vwap_series",
    "vwap_slope_pct",
]
