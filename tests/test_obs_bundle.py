"""Observability bundle: sim attribution, SC quota, review-check extras."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH
from market_desk.db.sim_desk import (
    ensure_sim_desk_tables,
    get_sim_account,
    set_sim_account_epoch,
)
from market_desk.notify import serverchan_quota_snapshot
from market_desk.review_check import _sample
from market_desk.sim_desk import build_sim_attribution, start_gate_snapshot


def _init_db(tmp_path, monkeypatch):
    db = tmp_path / "obs.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        conn.commit()
    get_sim_account(SIM_INITIAL_CASH)
    set_sim_account_epoch(str(SIM_BOOK_SINCE)[:10])


def test_sim_attribution_empty_book(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    attr = build_sim_attribution()
    assert attr["ok"] is True
    assert attr["fee_total"] == 0
    assert attr["gate_ok"] is False
    gate = start_gate_snapshot()
    assert gate.get("to_green_cny") is None or gate["to_green_cny"] >= 0


def test_serverchan_quota_snapshot(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    store: dict = {}
    monkeypatch.setattr("market_desk.db.load_setting", lambda k: store.get(k))
    monkeypatch.setattr(
        "market_desk.db.save_setting", lambda k, v: store.__setitem__(k, v)
    )
    monkeypatch.setattr("market_desk.calendar.today_cn", lambda: "2026-10-29")
    q = serverchan_quota_snapshot()
    assert q["cap"] >= 1
    assert q["used"] == 0
    assert q["remaining"] == q["cap"]
    store["sc_push_log:2026-10-29"] = [
        {"at": "10:00:00", "title": "收盘一页纸", "source": "eod"},
        {"at": "12:00:00", "title": "新闻", "source": "radar"},
    ]
    q2 = serverchan_quota_snapshot()
    assert q2["used"] == 2
    assert q2["remaining"] == q2["cap"] - 2


def test_sample_min_n_for_sell_fly():
    assert _sample(1, 14, min_days=1, min_n=15)["enough_for_todo"] is False
    assert _sample(1, 15, min_days=1, min_n=15)["enough_for_todo"] is True
