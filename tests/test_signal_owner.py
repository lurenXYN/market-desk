"""Per-account sell signal filtering."""

from __future__ import annotations

from market_desk.db import filter_signals_for_viewer


def test_filter_sells_by_owner() -> None:
    rows = [
        {"signal_type": "buy", "code": "600000", "owner_user_id": 0},
        {"signal_type": "sell", "code": "600001", "owner_user_id": 1},
        {"signal_type": "sell", "code": "600002", "owner_user_id": 2},
        {"signal_type": "sell", "code": "600003", "owner_user_id": 0},  # legacy
    ]
    u1 = filter_signals_for_viewer(rows, 1)
    codes = {r["code"] for r in u1}
    assert "600000" in codes
    assert "600001" in codes
    assert "600002" not in codes
    assert "600003" in codes  # legacy visible when logged in

    anon = filter_signals_for_viewer(rows, None)
    codes_a = {r["code"] for r in anon}
    assert "600000" in codes_a
    assert "600001" not in codes_a
    assert "600003" not in codes_a
