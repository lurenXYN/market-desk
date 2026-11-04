"""Adapt effect scorecard helpers for agent-pack."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.adapt_effect import (
    build_adapt_effect_panel,
    build_miss_trend,
    build_size_gate_scorecard,
    build_theme_rep_snapshot,
)
from market_desk.db.sim_desk import ensure_sim_desk_tables, get_sim_account, set_sim_account_epoch
from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH


def _init_db(tmp_path, monkeypatch):
    db = tmp_path / "effect.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        conn.commit()
    get_sim_account(SIM_INITIAL_CASH)
    set_sim_account_epoch(str(SIM_BOOK_SINCE)[:10])


def _buy(
    *,
    code: str,
    day: str,
    d3: float,
    size_mult: float | None,
    fails: list[str] | None = None,
    traded: int = 1,
) -> dict:
    payload: dict = {}
    if size_mult is not None:
        payload["size_mult"] = size_mult
    if fails:
        payload["confirm_fail_hist"] = list(fails)
    return {
        "signal_type": "buy",
        "code": code,
        "trade_date": day,
        "traded": traded,
        "skipped": 0,
        "outcome_label": "三日红" if d3 > 0 else "三日绿",
        "outcome_day3_pct": d3,
        "excess_d3_pct": d3 - 0.5,
        "bench_sessions": 3,
        "payload": payload,
    }


def test_size_gate_scorecard_buckets():
    rows = [
        _buy(code="600001", day="2026-10-20", d3=2.0, size_mult=0.5),
        _buy(code="600002", day="2026-10-20", d3=-1.0, size_mult=0.5),
        _buy(code="600003", day="2026-10-21", d3=3.0, size_mult=1.0),
        _buy(code="600004", day="2026-10-21", d3=1.0, size_mult=1.2),
        _buy(
            code="600005",
            day="2026-10-22",
            d3=-2.0,
            size_mult=1.0,
            fails=["分时未确认"],
        ),
    ]
    out = build_size_gate_scorecard(rows, recent_n=20)
    assert out["ok"] is True
    by_size = {r["key"]: r for r in out["by_size"]}
    assert by_size["shrink"]["n"] == 2
    assert by_size["flat"]["n"] == 2
    assert by_size["boost"]["n"] == 1
    by_gate = {r["key"]: r for r in out["by_gate"]}
    assert by_gate["minute_fail"]["n"] == 1
    assert by_gate["clean"]["n"] == 4


def test_miss_trend_counts(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    rows = [
        {
            "signal_type": "buy",
            "code": "600001",
            "trade_date": "2026-10-20",
            "traded": 0,
            "skipped": 1,
            "price_mark": "未回踩",
            "price_flags": ["miss_pullback"],
            "payload": {},
            "dev_pct": 5.0,
        },
        {
            "signal_type": "buy",
            "code": "600002",
            "trade_date": "2026-10-20",
            "traded": 0,
            "skipped": 0,
            "payload": {"confirm_fail": ["分时未确认"]},
            "live_last": 11.0,
            "price": 10.0,
            "plan_price": 10.0,
        },
    ]
    out = build_miss_trend(rows, days=5)
    assert "totals" in out
    assert "series" in out


def test_theme_rep_snapshot_empty(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    out = build_theme_rep_snapshot()
    assert out["ok"] is False or out["n"] == 0
    assert "worst" in out and "best" in out


def test_adapt_effect_panel(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    panel = build_adapt_effect_panel(
        [_buy(code="600001", day="2026-10-20", d3=1.5, size_mult=0.7)]
    )
    assert panel["ok"] is True
    assert panel["basis"] == "same_day_plan"
    # No daily bars → classic day3% stripped; scorecard empty by design (v1.10.57).
    assert panel["scorecard"]["ok"] is False
    assert panel["scorecard"].get("basis") == "same_day_plan"
    assert "today_cards" in panel
    assert "miss_trend" in panel
    assert "theme_rep" in panel
