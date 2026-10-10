"""Buy-card excess over the benchmark index on the same 3-session window."""

from __future__ import annotations

import pytest

from market_desk.review.bench import (
    attach_bench_excess,
    bench_window_pct,
    build_bench_excess_summary,
)

CLOSES = [
    ("2026-09-01", 100.0),
    ("2026-09-02", 101.0),
    ("2026-09-03", 102.0),
    ("2026-09-04", 98.0),
    ("2026-09-07", 99.0),
    ("2026-09-08", 103.0),
]


def _buy(day: str, code: str, d3: float | None, **kw):
    return {"trade_date": day, "code": code, "signal_type": "buy", "outcome_day3_pct": d3, **kw}


def test_bench_window_uses_three_settled_sessions():
    assert bench_window_pct(CLOSES, "2026-09-01") == (-2.0, 3)
    assert bench_window_pct(CLOSES, "2026-09-04") == (pytest.approx(5.10), 2)
    assert bench_window_pct(CLOSES, "2026-09-08") == (None, 0)
    assert bench_window_pct(CLOSES, "2026-08-31") == (None, 0)


def test_attach_marks_buys_only():
    rows = [
        _buy("2026-09-01", "600001", 3.0),
        _buy("2026-09-01", "600002", None),
        {"trade_date": "2026-09-01", "code": "600003", "signal_type": "sell", "outcome_day3_pct": 1.0},
    ]
    out = attach_bench_excess(rows, CLOSES)
    assert out[0]["bench_d3_pct"] == -2.0
    assert out[0]["excess_d3_pct"] == 5.0
    assert out[0]["bench_sessions"] == 3
    assert "excess_d3_pct" not in out[1]
    assert "excess_d3_pct" not in out[2]
    assert "excess_d3_pct" not in rows[0]


def test_summary_averages_by_day_and_dedupes():
    rows = [
        _buy("2026-09-01", "600001", 3.0, traded=1),
        _buy("2026-09-01", "600001", 9.0),
        _buy("2026-09-01", "600002", -1.0),
        _buy("2026-09-02", "600003", 0.0),
        _buy("2026-09-02", "600004", 4.0, skipped=1),
        _buy("2026-09-04", "600005", 1.0),
    ]
    s = build_bench_excess_summary(rows, CLOSES, label="中证1000", max_days=20, view_day="2026-09-04")
    assert s["ok"] is True
    # 09-01: bench −2 → excess +5 / +1 → day +3; 09-02: bench 99/101 → −1.98 → +1.98.
    assert s["days"] == 2
    assert s["n"] == 3
    assert s["pos_days"] == 2
    assert s["mean_excess"] == pytest.approx((3.0 + 1.98) / 2, abs=0.01)
    assert s["traded_n"] == 1 and s["traded_excess"] == 5.0
    assert s["day"]["partial"] is True
    assert s["day"]["n"] == 1


def test_summary_empty_without_full_horizon():
    s = build_bench_excess_summary([_buy("2026-09-07", "600001", 1.0)], CLOSES, label="中证1000", max_days=20)
    assert s["ok"] is False
    assert "note" in s
