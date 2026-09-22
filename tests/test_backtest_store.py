"""Tests for signal_backtest_run / fill persistence (no payload.sim_* stacking)."""

from __future__ import annotations

import json

from market_desk import db as desk_db
from market_desk.db import (
    clear_backtest_runs,
    compare_backtest_runs,
    delete_backtest_run,
    get_backtest_run,
    list_backtest_runs,
    save_backtest_run,
)


def _sample_result(**overrides):
    base = {
        "ok": True,
        "dry_run": False,
        "date_from": "2026-09-01",
        "date_to": "2026-09-18",
        "mode": "plan",
        "ready_only": False,
        "include_sells": True,
        "n": 2,
        "max_span_days": 90,
        "realism": {"vol_min_ratio": 0.4, "slip_pct": 0.15, "gap_pct": 1.0},
        "summary": {
            "buy_n": 2,
            "buy_filled_n": 1,
            "buy_fill_rate": 50.0,
            "buy_hit_rate": 100.0,
            "sell_hit_rate": None,
            "sim_exec": {"score": 80, "scored_n": 1, "chase_n": 0, "in_band_n": 1},
        },
        "items": [
            {
                "id": 101,
                "side": "buy",
                "code": "600001",
                "name": "测试甲",
                "kind": "stock",
                "trade_date": "2026-09-10",
                "signal_type": "buy_pullback",
                "plan_price": 10.0,
                "wait_price": 9.8,
                "chase_price": 10.5,
                "sim_filled": True,
                "sim_fill_price": 10.02,
                "sim_fill_date": "2026-09-10",
                "sim_mode": "plan",
                "sim_exec": "in_band",
                "note": "触达",
                "outcome_label": "次日红",
                "outcome_day1_pct": 2.1,
                "outcome_day3_pct": 3.0,
                "extra_flag": True,
            },
            {
                "id": 102,
                "side": "buy",
                "code": "600002",
                "name": "测试乙",
                "trade_date": "2026-09-11",
                "sim_filled": False,
                "note": "未触达",
            },
        ],
        "note": "unit-test run",
        "disclaimer": "test",
    }
    base.update(overrides)
    return base


def test_save_list_load_delete_backtest_run(tmp_path, monkeypatch):
    db_path = tmp_path / "bt.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db_path)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()

    run = save_backtest_run(result=_sample_result(), created_by=7, label="plan·0.4")
    assert run["id"] >= 1
    assert run["label"] == "plan·0.4"
    assert run["buy_hit_rate"] == 100.0
    assert run["created_by"] == 7

    listed = list_backtest_runs(limit=10)
    assert len(listed) == 1
    assert listed[0]["id"] == run["id"]

    loaded = get_backtest_run(run["id"], with_fills=True)
    assert loaded is not None
    assert loaded["item_n"] == 2
    assert len(loaded["items"]) == 2
    filled = next(x for x in loaded["items"] if x.get("sim_filled"))
    assert filled["code"] == "600001"
    assert filled["id"] == 101
    assert filled.get("extra_flag") is True

    # Must not touch signals table / payload stacking.
    with desk_db._connect() as conn:
        n_sig = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert n_sig == 0

    assert delete_backtest_run(run["id"]) is True
    assert get_backtest_run(run["id"]) is None


def test_compare_and_prune(tmp_path, monkeypatch):
    db_path = tmp_path / "bt2.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db_path)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()

    a = save_backtest_run(
        result=_sample_result(mode="plan"), label="A", keep=100
    )
    b = save_backtest_run(
        result=_sample_result(
            mode="wait",
            summary={
                "buy_fill_rate": 40.0,
                "buy_hit_rate": 50.0,
                "sim_exec": {"score": 60},
            },
        ),
        label="B",
        keep=100,
    )
    cmp = compare_backtest_runs([a["id"], b["id"]])
    assert cmp["ok"] is True
    assert len(cmp["runs"]) == 2
    assert cmp["runs"][0]["mode"] == "plan"
    assert cmp["runs"][1]["mode"] == "wait"

    # keep=1 prunes older
    deleted = clear_backtest_runs(keep=1)
    assert deleted == 1
    left = list_backtest_runs()
    assert len(left) == 1
    assert left[0]["id"] == b["id"]


def test_dry_run_not_persisted(tmp_path, monkeypatch):
    db_path = tmp_path / "bt3.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db_path)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    try:
        save_backtest_run(result=_sample_result(dry_run=True))
        assert False, "expected ValueError"
    except ValueError:
        pass
