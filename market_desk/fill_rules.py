"""Shared fill-rule contract: sim desk ↔ backtest ↔ counterfactual.

Goal (vnpy mid-priority): one place that documents which realism knobs are
aligned, which are intentionally different (daily OHLC vs live session), and
helpers to compare fee models.
"""

from __future__ import annotations

from typing import Any

from market_desk.config import (
    CF_ROUNDTRIP_COST_PCT,
    CHASE_WARN_PCT,
    TRADE_FEE_CNY,
)
from market_desk.sim_desk import SESSION_AM, SESSION_PM


def _backtest_slip_pct() -> float:
    """Lazy import to avoid circular imports at module load."""
    from market_desk.backtest import DEFAULT_SLIP_PCT

    return float(DEFAULT_SLIP_PCT)


def flat_fee_as_pct(*, px: float, qty: int, fee_cny: float | None = None) -> float:
    """Convert one-side flat CNY fee into percent of notional."""
    fee = float(TRADE_FEE_CNY if fee_cny is None else fee_cny)
    notional = float(px) * int(qty)
    if notional <= 0:
        return 0.0
    return fee / notional * 100.0


def roundtrip_flat_fee_pct(*, entry: float, exit_px: float, qty: int) -> float:
    """Sim-style round-trip: buy fee + sell fee as % of entry notional."""
    buy = flat_fee_as_pct(px=entry, qty=qty)
    sell = flat_fee_as_pct(px=exit_px, qty=qty)
    return buy + sell


def fill_rules_parity() -> dict[str, Any]:
    """Return a machine-readable map of sim / backtest / cf fill knobs."""
    return {
        "ok": True,
        "session": {
            "sim": "continuous 09:30–11:30 / 13:00–15:00; pre-09:30 buy clamps to 09:30",
            "backtest": "daily OHLC — no intraday session clock",
            "aligned": False,
            "am": list(SESSION_AM),
            "pm": list(SESSION_PM),
        },
        "t1": {
            "sim": "assess_sell_fill rejects open_date == trade_date",
            "backtest": "simulate_sell_fill(open_date=…) skips open day (v1.10.21)",
            "aligned": True,
        },
        "limit_board": {
            "sim": "quote_sealed_limit_up/down on live quote",
            "backtest": "is_one_word_limit_up/down on daily OHLC (+4.5% / −4.5%)",
            "cf": "same OHLC helpers as backtest",
            "aligned": "ohlc_helpers_shared",
        },
        "chase": {
            "sim_reject_above_plan_pct": 1.5,
            "discipline_chase_warn_pct": float(CHASE_WARN_PCT),
            "note": "sim hard-rejects live fill >1.5% through plan; backtest uses chase ceiling skip on day0",
        },
        "fees": {
            "sim_flat_cny_per_side": float(TRADE_FEE_CNY),
            "backtest_default_slip_pct": _backtest_slip_pct(),
            "cf_roundtrip_cost_pct": float(CF_ROUNDTRIP_COST_PCT),
            "note": (
                "Not the same unit: sim=¥/side; backtest=price slip %; cf=round-trip % on return. "
                "Use flat_fee_as_pct / roundtrip_flat_fee_pct to compare apples-to-apples on a sample trade."
            ),
            "example_100sh_at_10": {
                "sim_one_side_pct": round(flat_fee_as_pct(px=10.0, qty=100), 4),
                "sim_roundtrip_pct": round(
                    roundtrip_flat_fee_pct(entry=10.0, exit_px=10.0, qty=100), 4
                ),
                "backtest_slip_one_side_pct": _backtest_slip_pct(),
                "cf_roundtrip_pct": float(CF_ROUNDTRIP_COST_PCT),
            },
        },
        "sources": {
            "sim": "market_desk.sim_desk.assess_buy_fill / assess_sell_fill",
            "backtest": "market_desk.backtest.simulate_buy_fill / simulate_sell_fill",
            "cf": "market_desk.counterfactual.sim",
        },
    }
