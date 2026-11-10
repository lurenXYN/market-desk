"""Observation-window due checklist."""

from __future__ import annotations

from market_desk.observe_windows import build_observe_windows, count_trading_days


def test_count_trading_days_skips_weekend():
    # 2026-11-06 Fri → 2026-11-09 Mon inclusive = Fri+Mon = 2 (Sat/Sun off)
    assert count_trading_days("2026-11-06", "2026-11-09") == 2


def test_build_observe_windows_shape():
    out = build_observe_windows(asof="2026-11-10")
    assert out["ok"] is True
    assert out["n"] >= 4
    ids = {it["id"] for it in out["items"]}
    assert {"news_radar", "defense", "env_gate", "np_plan"} <= ids
    news = next(it for it in out["items"] if it["id"] == "news_radar")
    assert news["need_days"] == 7
    assert news["elapsed_trade_days"] >= 0
    assert news["verdict_hint"] in ("维持记账", "可复核")
