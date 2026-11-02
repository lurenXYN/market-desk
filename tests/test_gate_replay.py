"""As-of buy-gate replay and fill-rule comparable costs."""

from __future__ import annotations

from market_desk.fill_rules import comparable_roundtrip_pct, fill_rules_parity
from market_desk.gate_replay import (
    eval_off_high,
    eval_thin_confirm,
    replay_buy_gates,
)
from market_desk.env_gate import replay_env_buckets_from_closes


def test_off_high_mult_loosens():
    base = eval_off_high(last=99.3, high=100.0, kind="stock", mult=1.0)
    loose = eval_off_high(last=99.3, high=100.0, kind="stock", mult=0.9)
    # dist=0.7%; need 0.75 → fail; need 0.675 → pass
    assert base["replayable"]
    assert base["ok"] is False
    assert loose["ok"] is True
    assert loose["need"] < base["need"]


def test_thin_confirm_mult():
    blocked = eval_thin_confirm(
        kind="stock", thin_confirm=True, cross_n=0, theme_n=0, mult=1.0
    )
    loose = eval_thin_confirm(
        kind="stock", thin_confirm=True, cross_n=1, theme_n=0, mult=0.88
    )
    assert blocked["ok"] is False
    assert loose["ok"] is True


def test_replay_scenarios_flip_counts():
    rows = [
        {
            "signal_type": "buy_main",
            "trade_date": "2026-10-20",
            "code": "600000",
            "kind": "stock",
            "ready": 0,
            "skipped": 0,
            "price": 10.0,
            "outcome_day3_pct": 2.0,
            "payload": {
                "last": 99.3,
                "high": 100.0,
                "confirm_fail": ["离日高过近"],
                "confirm_fail_hist": ["离日高过近"],
            },
        },
        {
            "signal_type": "buy_main",
            "trade_date": "2026-10-21",
            "code": "600001",
            "kind": "stock",
            "ready": 1,
            "skipped": 0,
            "price": 10.0,
            "outcome_day3_pct": -1.0,
            "payload": {
                "last": 98.0,
                "high": 100.0,
                "ever_ready": True,
                "confirm_fail": [],
            },
        },
    ]
    out = replay_buy_gates(
        rows,
        scenarios=["baseline", "env_friendly"],
        since="2026-10-01",
        use_bars_fallback=False,
    )
    assert out["ok"]
    assert out["card_n"] == 2
    assert "baseline" in out["scenarios"]
    # Friendly loosens off_high → first card may flip to pass.
    fri = out["scenarios"]["env_friendly"]
    assert fri["flip_to_pass"] >= 1


def test_comparable_roundtrip_present():
    c = comparable_roundtrip_pct()
    assert c["ok"]
    assert "sim" in c and "backtest" in c and "cf" in c
    parity = fill_rules_parity()
    assert parity.get("comparable_roundtrip", {}).get("ok")


def test_env_bucket_replay_asof():
    from datetime import date, timedelta

    closes = []
    px = 1000.0
    day = date(2026, 1, 2)
    for i in range(90):
        px *= 1.002
        closes.append((day.isoformat(), px))
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
    out = replay_env_buckets_from_closes(closes, since="2026-03-01")
    assert out["ok"]
    assert out["n"] > 0
    assert out["by_bucket"]
