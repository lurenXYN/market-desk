"""Stacked sell-band multipliers must stay inside a bounded envelope."""

from __future__ import annotations

from market_desk.verdict.bands import _clamp_band


def test_stack_is_clamped_to_1_6x():
    base = {"pb_light": 1.0, "pb_deep": 2.0, "take_pnl": 3.0, "take_deep_pnl": 4.0, "pocket_pnl": 1.5, "pnl_stop": -3.0}
    band = {k: v * 2.4 for k, v in base.items()}  # 5 个因子同向叠加后约 2.4 倍
    _clamp_band(base, band)
    assert band["pb_light"] == 1.6
    assert band["take_pnl"] == 4.8
    # 更负=更宽，同样限制在 1.6 倍
    assert band["pnl_stop"] == -4.8


def test_tighter_side_is_clamped_too():
    base = {"pb_light": 1.0, "pnl_stop": -3.0}
    band = {"pb_light": 0.3, "pnl_stop": -2.0}
    _clamp_band(base, band)
    assert band["pb_light"] == 0.8
    assert band["pnl_stop"] == -2.55


def test_within_envelope_untouched():
    base = {"pb_light": 1.0, "pnl_stop": -3.0}
    band = {"pb_light": 1.2, "pnl_stop": -3.3}
    _clamp_band(base, band)
    assert band["pb_light"] == 1.2
    assert band["pnl_stop"] == -3.3
