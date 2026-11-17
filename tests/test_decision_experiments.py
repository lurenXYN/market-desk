"""Decision trace persistence and pre-registered experiment scoring."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.db.decision_trace import (
    attach_decision_outcome,
    load_decisions,
    save_decision,
    trace_summary,
)
from market_desk.experiments import (
    evaluate_experiment,
    load_experiments,
    validate_experiment,
)


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def test_decision_saved_with_evidence_hash(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    rid = save_decision(
        trade_date="2026-11-17",
        code="600001",
        name="测试",
        action="可买入",
        phase="发酵",
        mainline="电子",
        evidence={"ready": True, "temperature": 61.0},
    )
    assert rid > 0
    rows = load_decisions(trade_date="2026-11-17")
    assert len(rows) == 1
    assert rows[0]["evidence_hash"]
    assert rows[0]["evidence"]["ready"] is True


def test_same_evidence_is_not_duplicated(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    kwargs = dict(
        trade_date="2026-11-17",
        code="600001",
        action="可买入",
        evidence={"ready": True, "temperature": 61.0},
    )
    assert save_decision(**kwargs) > 0
    assert save_decision(**kwargs) == 0  # 同证据不重复写
    assert save_decision(**{**kwargs, "evidence": {"ready": False}}) > 0
    assert len(load_decisions(trade_date="2026-11-17")) == 2


def test_outcome_backfill_and_summary(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    save_decision(trade_date="2026-11-17", code="600001", action="可观察")
    touched = attach_decision_outcome(
        trade_date="2026-11-17", code="600001", label="次日红", day1_pct=1.2, day3_pct=3.0
    )
    assert touched == 1
    summary = trace_summary(trade_date="2026-11-17")
    assert summary["n"] == 1 and summary["scored_n"] == 1
    assert summary["by_action"]["可观察"] == 1


def test_validate_experiment_rejects_thin_sample():
    clean, errs = validate_experiment(
        {"id": "x", "hypothesis": "h", "switch": "s", "metric": "hit_rate",
         "min_n": 5, "min_lift_pp": 3, "start": "2026-11-18", "end": "2026-12-18"}
    )
    assert clean is None
    assert any("min_n" in e for e in errs)


def test_validate_experiment_ok():
    clean, errs = validate_experiment(
        {"id": "x", "hypothesis": "h", "switch": "s", "metric": "hit_rate",
         "min_n": 30, "min_lift_pp": 3, "start": "2026-11-18", "end": "2027-01-18"}
    )
    assert errs == []
    assert clean["min_n"] == 30


def test_evaluate_pass_fail_inconclusive():
    exp = {"id": "x", "metric": "hit_rate", "min_n": 30, "min_lift_pp": 5}
    treat = [{"outcome_label": "次日红"} for _ in range(40)] + [{"outcome_label": "次日绿"} for _ in range(10)]
    ctrl = [{"outcome_label": "次日绿"} for _ in range(40)] + [{"outcome_label": "次日红"} for _ in range(10)]
    out = evaluate_experiment(exp, treatment_rows=treat, control_rows=ctrl)
    assert out["verdict"] == "pass"
    assert out["lift_pp"] == 60.0

    thin = evaluate_experiment(exp, treatment_rows=treat[:5], control_rows=ctrl[:5])
    assert thin["verdict"] == "inconclusive"

    flat = evaluate_experiment(exp, treatment_rows=ctrl, control_rows=ctrl)
    assert flat["verdict"] == "fail"


def test_load_experiments_reads_repo_file():
    loaded = load_experiments()
    assert loaded["ok"] is True
    ids = {e["id"] for e in loaded["experiments"]}
    assert "phase_hit_min_n_30" in ids