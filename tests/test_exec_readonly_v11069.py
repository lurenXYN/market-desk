"""Stage-1 read-only broker gateway: ingest, reject Send, reconcile."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH, TRADE_FEE_CNY
from market_desk.db.sim_desk import (
    apply_sim_buy_fill,
    ensure_sim_desk_tables,
    get_sim_account,
    insert_sim_lot,
    set_sim_account_epoch,
    set_sim_cash,
)
from market_desk.exec import (
    Direction,
    ExecEngine,
    Offset,
    OrderRequest,
    ReadonlyBrokerGateway,
    Status,
    reset_exec_engine_for_tests,
)
from market_desk.exec.store import ensure_exec_tables
from market_desk.sim_desk import account_snapshot


def _init_db(tmp_path, monkeypatch):
    db = tmp_path / "exec_ro.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        ensure_exec_tables(conn)
        conn.commit()
    get_sim_account(SIM_INITIAL_CASH)
    set_sim_account_epoch(str(SIM_BOOK_SINCE)[:10])
    reset_exec_engine_for_tests()


def _force_readonly_gate(monkeypatch):
    monkeypatch.setattr(
        "market_desk.sim_desk.portfolio_stats",
        lambda: {
            "gate_ok": True,
            "gate_readonly_ok": True,
            "closed_n": 2,
            "cum_pnl_pct": 1.5,
            "open_n": 0,
            "equity": 20300.0,
            "vs_bench": {"ok": True, "excess_cum_pct": 2.0},
        },
    )


def test_readonly_send_always_rejected(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    gw = ReadonlyBrokerGateway()
    gw.connect()
    od = gw.send_order(
        OrderRequest(
            code="600000",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=100,
            price=10.0,
        )
    )
    assert od.status == Status.REJECTED
    assert od.message == "readonly_stage1"


def test_ingest_blocked_without_gate(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    eng = ExecEngine(persist=False)
    got = eng.ingest_broker_snapshot(
        {"account": {"balance": 10000, "available": 10000}, "positions": [], "trades": []}
    )
    assert got["ok"] is False
    assert got["reason"] == "start_gate_blocked"


def test_ingest_and_reconcile_match(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    _force_readonly_gate(monkeypatch)
    eng = ExecEngine(persist=False)

    cash0 = float(account_snapshot().get("cash") or SIM_INITIAL_CASH)
    fee = float(TRADE_FEE_CNY)
    lid = insert_sim_lot(
        {
            "code": "600000",
            "name": "浦发",
            "qty": 100,
            "open_px": 10.0,
            "open_date": "2026-11-04",
            "open_at": "2026-11-04 10:00:00",
            "size_tag": "full",
            "open_weight": 1.0,
            "note": "unit",
        }
    )
    assert lid
    apply_sim_buy_fill(
        lot_id=int(lid),
        trade_date="2026-11-04",
        filled_at="2026-11-04 10:00:00",
        px=10.0,
        qty=100,
        fee=fee,
        cash_after=cash0 - 1000 - fee,
        weight=1.0,
        note="unit",
    )
    set_sim_cash(cash0 - 1000 - fee)

    payload = {
        "gateway": "broker_ro",
        "account": {"balance": 20300.0, "available": 18995.0},
        "positions": [{"code": "600000", "name": "浦发", "volume": 100, "price": 10.0}],
        "trades": [
            {
                "code": "600000",
                "direction": "LONG",
                "offset": "OPEN",
                "volume": 100,
                "price": 10.0,
                "tradeid": "t1",
            }
        ],
    }
    path = str(tmp_path / "broker_readonly.json")
    got = eng.ingest_broker_snapshot(payload, persist_path=path)
    assert got["ok"] is True
    assert got["stage"] == "1_readonly"
    assert (tmp_path / "broker_readonly.json").exists()

    rec = eng.reconcile_live()
    assert rec["paper_vs_live"]["positions"]["ok"] is True
    assert rec["live"]["readonly"] is True

    eng._live.load_snapshot(
        {
            "account": {"balance": 20300.0, "available": 18995.0},
            "positions": [{"code": "600000", "volume": 200, "price": 10.0}],
            "trades": payload["trades"],
        },
        source="mutate",
    )
    bad = eng.reconcile_live()
    assert bad["ok"] is False
    assert bad["paper_vs_live"]["positions"]["mismatch_n"] == 1


def test_attach_file_roundtrip(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    _force_readonly_gate(monkeypatch)
    path = tmp_path / "snap.json"
    path.write_text(
        '{"gateway":"broker_ro","account":{"balance":1,"available":1},'
        '"positions":[{"code":"000001","volume":100,"price":10}],"trades":[]}',
        encoding="utf-8",
    )
    eng = ExecEngine(persist=False)
    got = eng.attach_readonly_file(str(path))
    assert got["ok"] is True
    assert len(eng._live.query_positions()) == 1
