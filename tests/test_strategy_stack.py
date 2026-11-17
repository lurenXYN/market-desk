"""Strategy profiles, portfolio caps and change-point detection."""

from __future__ import annotations

import market_desk.profiles as profiles
from market_desk.changepoint import cusum, state_shift
from market_desk.portfolio_risk import average_correlation, board_exposure, portfolio_gate


def test_profiles_default_is_standard(monkeypatch):
    monkeypatch.setattr(profiles, "setting", lambda key, default=None: default)
    assert profiles.current_profile_name() == "standard"
    prof = profiles.get_profile()
    assert prof["name"] == "standard"
    assert prof["size"]["size_cap_pct"] == 40.0


def test_profiles_unknown_falls_back(monkeypatch):
    monkeypatch.setattr(profiles, "setting", lambda key, default=None: "yolo")
    assert profiles.current_profile_name() == "standard"
    assert profiles.profile_param("entry", "require_pullback") is True


def test_profile_summary_lists_all_three(monkeypatch):
    monkeypatch.setattr(profiles, "setting", lambda key, default=None: "conservative")
    out = profiles.profile_summary()
    assert out["active"] == "conservative"
    assert {p["name"] for p in out["profiles"]} == {"conservative", "standard", "aggressive"}


def test_board_exposure_and_concentration_cap():
    positions = [
        {"code": "600001", "qty": 1000, "last": 10.0},
        {"code": "600002", "qty": 1000, "last": 10.0},
    ]
    board_of = {"600001": "电子", "600002": "电子"}
    exposure, total = board_exposure(positions, board_of=board_of)
    assert total == 20000.0
    assert exposure["电子"] == 100.0
    gate = portfolio_gate(positions=positions, board_of=board_of)
    assert gate["size_mult"] == 0.8  # 单板块 100% > 默认 50% 上限


def test_drawdown_budget_two_levels():
    warn = portfolio_gate(positions=[], drawdown_pct=9.0)
    hard = portfolio_gate(positions=[], drawdown_pct=18.0)
    assert warn["size_mult"] == 0.6
    assert hard["size_mult"] == 0.4
    assert hard["level"] == "danger"


def test_correlation_penalty(monkeypatch):
    # 三条同向序列：相关系数接近 1
    up = [10 + i * 0.1 for i in range(30)]
    closes = {"600001": up, "600002": list(up), "600003": [x * 2 for x in up]}
    corr = average_correlation(closes)
    assert corr is not None and corr > 0.9
    gate = portfolio_gate(closes_by_code=closes)
    assert gate["size_mult"] == 0.85


def test_cusum_detects_downshift():
    series = [60.0] * 15 + [35.0] * 15
    out = cusum(series)
    assert out["ok"] is True
    assert out["changed"] is True
    assert out["direction"] == "down"


def test_cusum_flat_series_is_quiet():
    out = state_shift([50.0] * 20)
    assert out["changed"] is False


def test_cusum_needs_samples():
    out = cusum([50.0, 51.0])
    assert out["ok"] is False