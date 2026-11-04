"""v1.10.59: pick0 patch, newest bars, IDX bench, epoch order, live risk."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import market_desk.db as desk_db
from market_desk.db.bars_cache import (
    ensure_bars_cache_tables,
    load_daily_bars,
    upsert_daily_bars,
)
from market_desk.dual_score import bench_cache_code, load_bench_closes
from market_desk.exec.engine import ExecEngine
from market_desk.exec.types import Direction, Offset, OrderRequest, OrderType, Status
from market_desk.pick_score import pick0_may_upgrade, pick0_patch_live_last
from market_desk.review.exec_score import classify_miss_kind
from market_desk.review.outcome import score_signal_with_closes
from market_desk.sim_desk import portfolio_stats, sim_vs_bench


def _init(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    with desk_db._connect() as conn:
        ensure_bars_cache_tables(conn)
        conn.commit()


def test_pick0_patch_keeps_frozen_score():
    old = {
        "score": 60,
        "grade": "B",
        "factors": [{"key": "a"}],
        "at": "09:40",
    }
    patched = pick0_patch_live_last(old, 10.5)
    assert patched["score"] == 60 and patched["at"] == "09:40" and patched["live_last"] == 10.5
    assert pick0_may_upgrade(old, patched)
    assert not pick0_may_upgrade(old, {**patched, "score": 99})


def test_load_daily_bars_returns_newest_n(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    bars = [
        {
            "date": f"2026-01-{i:02d}",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10 + i * 0.01,
            "volume": 1,
            "pct": 0.1,
        }
        for i in range(1, 21)
    ]
    upsert_daily_bars("600001", bars, source="test", skip_today=False)
    got = load_daily_bars("600001", limit=5)
    assert len(got) == 5
    assert got[0]["date"] == "2026-01-16"
    assert got[-1]["date"] == "2026-01-20"


def test_upsert_coalesce_keeps_existing_pct(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    upsert_daily_bars(
        "600001",
        [{"date": "2026-01-10", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 9, "pct": 3.3}],
        source="chip_volume",
        skip_today=False,
    )
    upsert_daily_bars(
        "600001",
        [{"date": "2026-01-10", "open": 1.1, "high": 2.1, "low": 0.6, "close": 1.6, "volume": 10, "pct": None}],
        source="fetch_daily_bars",
        skip_today=False,
    )
    row = load_daily_bars("600001", limit=1)[0]
    assert row["pct"] == 3.3  # null must not wipe
    assert row["close"] == 1.6  # same qfq family may refresh
    assert row["source"] == "fetch_daily_bars"


def test_bench_cache_code_distinct_from_stock():
    assert bench_cache_code("sh000852") == "IDX000852"
    assert bench_cache_code("sh000852") != "000852"


def test_load_bench_closes_reads_idx_not_stock(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    # Poison stock 000852 — must NOT be used as CSI1000.
    upsert_daily_bars(
        "000852",
        [{"date": "2026-10-01", "close": 1.0, "open": 1, "high": 1, "low": 1}],
        source="stock",
        skip_today=False,
    )
    upsert_daily_bars(
        bench_cache_code(),
        [
            {"date": "2026-10-01", "close": 100.0, "open": 100, "high": 101, "low": 99},
            {"date": "2026-10-02", "close": 101.0, "open": 100, "high": 102, "low": 99},
            {"date": "2026-10-03", "close": 102.0, "open": 101, "high": 103, "low": 100},
            {"date": "2026-10-08", "close": 103.0, "open": 102, "high": 104, "low": 101},
        ],
        source="bench",
        skip_today=False,
    )
    closes = load_bench_closes(limit=10, allow_http=False)
    assert closes[-1][1] == 103.0
    assert all(c != 1.0 for _, c in closes)


def test_sim_vs_bench_rejects_stale(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    old = (date.today() - timedelta(days=30)).isoformat()
    older = (date.today() - timedelta(days=40)).isoformat()
    upsert_daily_bars(
        bench_cache_code(),
        [
            {"date": older, "close": 100.0, "open": 100, "high": 100, "low": 100},
            {"date": old, "close": 90.0, "open": 100, "high": 100, "low": 90},
        ],
        source="bench",
        skip_today=False,
    )
    out = sim_vs_bench(cum_pnl_pct=1.0, epoch=older)
    assert out["ok"] is False
    assert "过旧" in (out.get("note") or "")


def test_portfolio_stats_snapshot_before_closed_n(tmp_path, monkeypatch):
    """account_snapshot (epoch wipe) must run before closed_n is read."""
    _init(tmp_path, monkeypatch)
    calls: list[str] = []

    def fake_snap():
        calls.append("snap")
        return {
            "cash": 20000.0,
            "initial_cash": 20000.0,
            "mtm": 0.0,
            "equity": 20000.0,
            "return_pct": 0.0,
        }

    def fake_closed():
        calls.append("closed")
        return 3

    monkeypatch.setattr("market_desk.sim_desk.account_snapshot", fake_snap)
    monkeypatch.setattr("market_desk.sim_desk.load_open_sim_lots", lambda: [])
    monkeypatch.setattr("market_desk.sim_desk.count_sim_sell_fills", fake_closed)
    monkeypatch.setattr(
        "market_desk.sim_desk.sim_vs_bench",
        lambda **kw: {"ok": False},
    )
    monkeypatch.setattr("market_desk.sim_desk.sum_sim_realized_pnl_cny", lambda: 0.0)
    stats = portfolio_stats()
    assert calls[0] == "snap"
    assert "closed" in calls and calls.index("snap") < calls.index("closed")
    assert stats["closed_n"] == 3


def test_live_send_order_runs_check_order(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    eng = ExecEngine(persist=False)
    live = MagicMock()
    live.gateway_name = "mock"
    live.query_account.return_value = None
    live.query_positions.return_value = []
    eng._live = live
    monkeypatch.setattr(
        "market_desk.exec.risk.require_start_gate",
        lambda **kw: MagicMock(ok=True, reason="gate_ok", status=Status.SUBMITTING),
    )
    # Bad lot size must reject before live.send_order.
    req = OrderRequest(
        code="600001",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        volume=50,
        price=10.0,
        order_type=OrderType.LIMIT,
    )
    out = eng.send_order(req, live=True)
    assert out.status == Status.REJECTED
    assert out.message == "not_board_lot"
    live.send_order.assert_not_called()


def test_gate_blocked_ignores_ever_ready():
    row = {
        "signal_type": "buy",
        "traded": 0,
        "ready": 0,
        "price": 10.0,
        "live_last": 11.0,
        "payload": {
            "confirm_fail": ["minute"],
            "ever_ready": True,
            "np": {"np": False, "day_low": 9.5, "day_close": 11.0, "plan": 10.0},
        },
    }
    assert classify_miss_kind(row) == "touched_not_bought"


def test_same_day_plan_missing_bar_is_no_kline():
    row = {
        "signal_type": "buy",
        "trade_date": "2026-11-02",
        "price": 10.0,
        "plan_price": 10.0,
    }
    # dates/closes without the signal day → day0_low None
    scored = score_signal_with_closes(
        row,
        closes=[10.0, 10.1, 10.2, 10.3],
        dates=["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"],
        lows=[9.5, 9.8, 9.9, 10.0],
        highs=[10.5, 10.6, 10.7, 10.8],
        opens=[10.0, 10.0, 10.0, 10.0],
        standard="same_day_plan",
    )
    assert scored["outcome_label"] == "无日线"


def test_cross_family_bars_do_not_overwrite_close(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    upsert_daily_bars(
        "600001",
        [{"date": "2026-01-10", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1, "pct": 1.0}],
        source="fetch_daily_bars",
        skip_today=False,
    )
    upsert_daily_bars(
        "600001",
        [{"date": "2026-01-10", "open": 20, "high": 21, "low": 19, "close": 20.5, "volume": 9, "pct": 9.0}],
        source="sina",
        skip_today=False,
    )
    row = load_daily_bars("600001", limit=1)[0]
    assert row["close"] == 10.5
    assert row["source"] == "fetch_daily_bars"


def test_np_from_bar_one_word_limit_not_tp():
    from market_desk.np_review import np_from_bar

    # Sealed limit-down day: low==close < plan would naively be TP.
    out = np_from_bar(
        10.0,
        9.0,
        9.0,
        9.0,
        open_=9.0,
        prev_close=10.0,
    )
    assert out is not None
    assert out["np"] is True
    assert out.get("one_word") == "limit_down"

    # Normal touch still TP.
    ok = np_from_bar(10.0, 9.8, 10.2, 10.5, open_=10.1, prev_close=10.0)
    assert ok is not None and ok["np"] is False and not ok.get("one_word")

    # Flat bar without prev_close must NOT invent sealed → keep TP.
    flat = np_from_bar(10.0, 9.5, 9.5, 9.5, open_=9.5, prev_close=None)
    assert flat is not None and flat["np"] is False and not flat.get("one_word")


def test_sim_vs_bench_empty_idx_note():
    from market_desk.sim_desk import sim_vs_bench

    out = sim_vs_bench(cum_pnl_pct=1.0, epoch="2026-01-01")
    # Without IDX rows (and allow_http=False), refuse with actionable note.
    if not out.get("ok"):
        note = out.get("note") or ""
        assert "IDX" in note or "指数" in note
