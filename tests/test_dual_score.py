"""Dual abs/xs win rates and soft-candidate hints."""

from __future__ import annotations

from market_desk.dual_score import dual_from_pairs, oos_buy_dual_wins, soft_candidate_hint, win_rate
from market_desk.review.bench import attach_bench_excess


CLOSES = [
    ("2026-09-01", 100.0),
    ("2026-09-02", 101.0),
    ("2026-09-03", 102.0),
    ("2026-09-04", 98.0),
    ("2026-09-07", 99.0),
    ("2026-09-08", 103.0),
]


def test_win_rate_and_dual_from_pairs():
    assert win_rate([1.0, -1.0, 0.5]) == 66.7
    assert win_rate([]) is None
    block = dual_from_pairs([1.0, -2.0], [0.5, -0.1], label="t")
    assert block["n"] == 2 and block["abs_win"] == 50.0 and block["xs_win"] == 50.0


def test_oos_buy_dual_wins_with_closes():
    rows = [
        {
            "signal_type": "buy",
            "trade_date": "2026-09-01",
            "code": "600000",
            "outcome_day3_pct": 2.0,
            "skipped": 0,
            "ready": 1,
            "payload": {"ever_ready": True},
        },
        {
            "signal_type": "buy",
            "trade_date": "2026-09-01",
            "code": "600001",
            "outcome_day3_pct": -1.0,
            "skipped": 0,
            "ready": 0,
            "payload": {},
        },
    ]
    # Attach then strip so oos uses same closes path.
    attached = attach_bench_excess(rows, CLOSES)
    out = oos_buy_dual_wins(attached, CLOSES, since="2026-09-01")
    assert out["ok"] is True
    assert out["all"]["n"] == 2
    assert out["lit"]["n"] == 1
    assert out["all"]["abs_win"] == 50.0


def test_soft_candidate_hint_shapes():
    assert soft_candidate_hint(
        {
            "id": "narrative",
            "metrics": {"hit_rate": 0.6, "median_lead_min": 20, "days_with_cluster": 8},
        }
    )
    assert not soft_candidate_hint(
        {"id": "narrative", "metrics": {"hit_rate": 0.09, "median_lead_min": 55, "days_with_cluster": 14}}
    )
    assert soft_candidate_hint({"id": "sim_desk_gate", "metrics": {"gate_ok": True}})
    assert not soft_candidate_hint({"id": "edge_reclaim", "metrics": {"delta": 1.5}})
