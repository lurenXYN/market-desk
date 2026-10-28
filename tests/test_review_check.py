"""Smoke tests for the paste-friendly review-check payload."""

from __future__ import annotations

from market_desk.review_check import _sample, merge_counterfactual


def test_sample_enough_flag() -> None:
    """TODO floor requires both days and card counts."""
    assert _sample(10, 30)["enough_for_todo"] is True
    assert _sample(9, 30)["enough_for_todo"] is False
    assert _sample(10, 29)["enough_for_todo"] is False


def test_merge_counterfactual_appends_gate_item() -> None:
    """What-If merge adds a gates_cf row even when summary is sparse."""
    base = {"ok": True, "items": []}
    cf = {
        "ok": True,
        "headline": "到价 1 张",
        "summary": {
            "touched": 1,
            "window_days": 5,
            "unknown_n": 0,
            "released": {"n": 1, "mean": -1.0},
            "blocked": {"n": 0, "mean": None},
        },
        "gates": [{"key": "分时未确认", "verdict": "功臣", "days": 5, "avoid_mean": 1.2, "ci_lo": 0.1, "ci_hi": 2.0}],
        "stop_cmp": {"rows": []},
        "entry_sens": {},
        "built_at": "10:00:00",
    }
    out = merge_counterfactual(base, cf)
    ids = [i["id"] for i in out["items"]]
    assert "gates_cf" in ids
    assert "cf_unknown" in ids
    assert out["counterfactual"]["ok"] is True
