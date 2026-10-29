"""Paper execution gateway: risk, OrderBook, reconcile, circuit, triggers."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH, TRADE_FEE_CNY
from market_desk.db.sim_desk import (
    ensure_sim_desk_tables,
    get_sim_account,
    set_sim_account_epoch,
)
from market_desk.exec import (
    CircuitBreaker,
    Direction,
    ExecEngine,
    Offset,
    OrderRequest,
    PaperGateway,
    PositionData,
    Status,
    TriggerMode,
    check_order,
    check_position_limits,
    reconcile_positions,
    require_start_gate,
    self_check_gateway,
    sell_item_to_trigger,
)
from market_desk.exec.store import ensure_exec_tables
from market_desk.exec.types import AccountData, OrderData


def _init_db(tmp_path, monkeypatch):
    db = tmp_path / "exec.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        ensure_exec_tables(conn)
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
    eng = ExecEngine(persist=True)
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
    assert "circuit" in snap
    assert snap["stage"].startswith("0.7")


def test_exec_engine_blocks_live_attach(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    eng = ExecEngine(persist=False)
    got = eng.attach_live(PaperGateway("fake-live"))
    assert got["ok"] is False
    assert got["reason"] == "start_gate_blocked"
    snap = eng.snapshot()
    assert snap["live"]["attached"] is False


def test_reject_streak_trips_opens(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    from datetime import datetime

    br = CircuitBreaker(
        enabled=True,
        day_loss_frac=0.5,
        reject_streak_max=3,
    )
    br.trade_date = datetime.now().strftime("%Y-%m-%d")
    br.day_start_equity = 20000.0
    gw = PaperGateway(circuit=br)
    gw.connect()
    bad = OrderRequest(
        code="600000",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        volume=50,
        price=10.0,
    )
    for _ in range(3):
        od = gw.send_order(bad)
        assert od.status == Status.REJECTED
        assert od.message == "not_board_lot"
    assert br.reject_tripped is True
    ok_shape = OrderRequest(
        code="600000",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        volume=100,
        price=10.0,
    )
    blocked = gw.send_order(ok_shape)
    assert blocked.status == Status.REJECTED
    assert blocked.message == "circuit_reject_streak"


def test_day_loss_trips_opens(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    from datetime import datetime

    br = CircuitBreaker(enabled=True, day_loss_frac=0.03, reject_streak_max=99)
    br.trade_date = datetime.now().strftime("%Y-%m-%d")
    br.day_start_equity = 20000.0
    br.day_loss_tripped = False
    # Equity 3% below day start → trip
    ok, reason = br.check_open(equity=19300.0)
    assert ok is False and reason == "circuit_day_loss"
    assert br.day_loss_tripped is True
    # Sells still pass check_order circuit skip
    sell = OrderRequest(
        code="600000",
        direction=Direction.SHORT,
        offset=Offset.CLOSE,
        volume=100,
        price=10.0,
    )
    acct = AccountData(gateway="paper", balance=19300.0, available=1000.0)
    v = check_order(sell, live=False, account=acct, positions=[], circuit=br)
    assert v.ok or v.reason != "circuit_day_loss"


def test_orderbook_persists_across_engines(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    eng1 = ExecEngine(persist=True)
    order = eng1.send_order(
        OrderRequest(
            code="600000",
            name="浦发",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=100,
            price=10.0,
        )
    )
    assert order.status == Status.ALLTRADED
    oid = order.orderid
    eng2 = ExecEngine(persist=True)
    restored = eng2.orders.get(oid)
    assert restored is not None
    assert restored.status == Status.ALLTRADED
    assert restored.volume == 100


def test_sell_item_to_trigger_modes():
    stop = sell_item_to_trigger(
        {
            "code": "600000",
            "ready": True,
            "urgency": "stop",
            "exit_mode": "clear",
            "trigger_price": 9.5,
            "last": 9.4,
        }
    )
    assert stop.mode == TriggerMode.IMMEDIATE
    assert stop.executable is True

    watch = sell_item_to_trigger(
        {
            "code": "600000",
            "ready": False,
            "urgency": "trim",
            "exit_mode": "half",
            "open_buffer_track": "watch",
            "open_buffer_phase": "watching",
            "trigger_price": 10.2,
        }
    )
    assert watch.mode == TriggerMode.DEFERRED
    assert watch.executable is False

    t1 = sell_item_to_trigger(
        {"code": "600000", "ready": True, "exit_mode": "clear", "t1_locked": True}
    )
    assert t1.mode == TriggerMode.BLOCKED
    assert t1.reason == "t1_locked"

    hold_stop = sell_item_to_trigger(
        {
            "code": "600000",
            "ready": False,
            "exit_mode": "hold",
            "urgency": "hold",
            "stop_price": 9.0,
            "last": 9.5,
        }
    )
    assert hold_stop.mode == TriggerMode.PRICE_STOP
    assert hold_stop.executable is False


def test_order_data_roundtrip():
    raw = OrderData(
        code="600000",
        orderid="P000001",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        volume=100,
        traded=100,
        price=10.0,
        status=Status.ALLTRADED,
        name="浦发",
    ).to_dict()
    back = OrderData.from_dict(raw)
    assert back is not None
    assert back.orderid == "P000001"
    assert back.status == Status.ALLTRADED
