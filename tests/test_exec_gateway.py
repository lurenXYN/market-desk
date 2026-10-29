"""Paper execution gateway: risk, OrderBook, reconcile."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH, TRADE_FEE_CNY
from market_desk.db.sim_desk import (
    ensure_sim_desk_tables,
    get_sim_account,
    set_sim_account_epoch,
)
from market_desk.exec import (
    Direction,
    ExecEngine,
    Offset,
    OrderRequest,
    PaperGateway,
    PositionData,
    Status,
    check_order,
    check_position_limits,
    reconcile_positions,
    require_start_gate,
    self_check_gateway,
)
from market_desk.exec.types import AccountData


def _init_db(tmp_path, monkeypatch):
    db = tmp_path / "exec.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        conn.commit()
    get_sim_account(SIM_INITIAL_CASH)
    set_sim_account_epoch(str(SIM_BOOK_SINCE)[:10])


def test_risk_basics_and_live_gate(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    bad = OrderRequest(
        code="600000",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        volume=50,
        price=10.0,
    )
    assert check_order(bad, live=False).reason == "not_board_lot"
    assert require_start_gate(live=False).ok is True
    assert require_start_gate(live=True).ok is False
    assert require_start_gate(live=True).reason == "start_gate_blocked"


def test_position_limit_rejects_oversize(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    acct = AccountData(gateway="paper", balance=20000.0, available=20000.0)
    # 25% of 20k = 5k; 600*10=6k → reject
    req = OrderRequest(
        code="600000",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        volume=600,
        price=10.0,
    )
    assert check_position_limits(req, account=acct, positions=[]).reason == "max_pos_frac"
    ok = OrderRequest(
        code="600000",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        volume=100,
        price=10.0,
    )
    assert check_position_limits(ok, account=acct, positions=[]).ok is True


def test_reconcile_mismatch_and_self_check(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    local = [PositionData(code="600000", volume=100, price=10.0, name="浦发")]
    remote = [PositionData(code="600000", volume=200, price=10.0, name="浦发")]
    diff = reconcile_positions(local, remote)
    assert diff["ok"] is False and diff["mismatch_n"] == 1
    gw = PaperGateway()
    gw.connect()
    assert self_check_gateway(gw)["positions"]["ok"] is True


def test_paper_buy_orderbook_and_snapshot(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    eng = ExecEngine()
    order = eng.send_order(
        OrderRequest(
            code="600000",
            name="浦发",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=100,
            price=10.0,
            reference="unit",
        )
    )
    assert order.status == Status.ALLTRADED
    assert order.traded == 100
    assert eng.orders.get(order.orderid) is not None
    acct = eng.paper.query_account()
    assert acct is not None
    assert abs(acct.available - (SIM_INITIAL_CASH - 1000 - TRADE_FEE_CNY)) < 1e-6
    pos = eng.paper.query_positions()
    assert len(pos) == 1 and pos[0].volume == 100
    snap = eng.snapshot()
    assert snap["orders"]["tracked_n"] == 1
    assert snap["self_check"]["positions"]["ok"] is True


def test_exec_engine_blocks_live_attach(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    eng = ExecEngine()
    got = eng.attach_live(PaperGateway("fake-live"))
    assert got["ok"] is False
    assert got["reason"] == "start_gate_blocked"
    snap = eng.snapshot()
    assert snap["live"]["attached"] is False
