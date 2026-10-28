"""Paper trading book: cash account, fees, fill gates, start gate."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.config import SIM_INITIAL_CASH, TRADE_FEE_CNY
from market_desk.sim_desk import (
    account_snapshot,
    assess_buy_fill,
    assess_sell_fill,
    clamp_buy_fill_stamp,
    is_continuous_session_stamp,
    net_pnl_pct,
    open_lots_from_signals,
    plan_buy_qty,
    portfolio_stats,
    quote_sealed_limit_down,
    quote_sealed_limit_up,
    size_from_payload,
    start_gate_snapshot,
)
from market_desk.db.sim_desk import (
    apply_sim_sell_fill,
    ensure_sim_desk_tables,
    get_sim_account,
    insert_sim_lot,
    load_open_sim_lots,
    load_sim_fills,
    set_sim_cash,
)


def _init_db(tmp_path, monkeypatch):
    db = tmp_path / "sim.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        conn.commit()
    get_sim_account(SIM_INITIAL_CASH)


def test_size_from_payload_full_half_probe():
    assert size_from_payload({}, ready=True) == (1.0, "full")
    assert size_from_payload({"early_half": {"px": 10}}, ready=True) == (0.5, "half")
    assert size_from_payload({"size_mult": 0.5}, ready=True) == (0.5, "half")
    w, tag = size_from_payload({"probe_ok": True}, ready=False)
    assert w == 0.5 and tag == "probe"


def test_plan_buy_qty_and_fee_math():
    # 2万 × 25% = 5000 → 500股@10；扣费后仍够
    qty = plan_buy_qty(px=10.0, open_weight=1.0, cash=20000.0, equity=20000.0, fee=5.0)
    assert qty == 500
    # 现金只够费 → 0
    assert plan_buy_qty(px=10.0, open_weight=1.0, cash=5.0, equity=20000.0, fee=5.0) == 0
    pnl = net_pnl_pct(10.0, 11.0, fee_buy=5.0, fee_sell=5.0, qty=100)
    # gross +10%, fees 10/1000 = 1% → 9%
    assert abs(pnl - 9.0) < 1e-6


def test_account_init_and_open_deducts_cash(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    acct = account_snapshot()
    assert abs(acct["cash"] - SIM_INITIAL_CASH) < 1e-6
    signals = [
        {
            "id": 1,
            "trade_date": "2026-10-20",
            "signaled_at": "2026-10-20 10:00:00",
            "signal_type": "buy",
            "code": "600000",
            "name": "浦发",
            "kind": "stock",
            "price": 10.0,
            "last": 10.0,
            "ready": 1,
            "payload": {
                "ever_ready": True,
                "first_ready_at": "2026-10-20 10:00:00",
                "first_ready_px": 10.0,
            },
        }
    ]
    assert open_lots_from_signals(signals) == 1
    assert open_lots_from_signals(signals) == 0
    lots = load_open_sim_lots()
    assert len(lots) == 1
    assert lots[0]["qty"] == 500
    acct2 = account_snapshot()
    # 500*10 + 5 fee
    assert abs(acct2["cash"] - (SIM_INITIAL_CASH - 5000 - TRADE_FEE_CNY)) < 1e-6
    buys = load_sim_fills(side="buy")
    assert len(buys) == 1 and buys[0]["fee"] == TRADE_FEE_CNY


def test_sell_credits_cash_and_gate(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    set_sim_cash(15000.0, initial_cash=SIM_INITIAL_CASH)
    lid = insert_sim_lot(
        {
            "code": "600000",
            "name": "浦发",
            "kind": "stock",
            "signal_type": "buy",
            "open_date": "2026-10-20",
            "open_at": "2026-10-20 10:00:00",
            "open_px": 10.0,
            "open_weight": 1.0,
            "size_tag": "full",
            "qty": 500,
            "qty_open": 500,
            "buy_fee": 5.0,
        }
    )
    assert lid
    # Sell all at 11 → proceeds 5500 - fee 5; cost 5000 + buy fee alloc 5
    assert apply_sim_sell_fill(
        lid,
        trade_date="2026-10-21",
        filled_at="2026-10-21 14:00:00",
        px=11.0,
        qty=500,
        fee=5.0,
        cash_after=15000.0 + 5500.0 - 5.0,
        frac=1.0,
        weight=1.0,
        pnl_pct=9.0,
        pnl_cny=5500 - 5 - 5000 - 5,
        exit_mode="clear",
        role_label="止盈",
    )
    set_sim_cash(15000.0 + 5500.0 - 5.0, initial_cash=SIM_INITIAL_CASH)
    assert load_open_sim_lots() == []
    stats = portfolio_stats()
    assert stats["closed_n"] == 1
    assert stats["equity"] > SIM_INITIAL_CASH
    assert stats["gate_ok"] is True
    gate = start_gate_snapshot()
    assert "可开干" in gate["gate_label"]


def test_session_and_fill_gates():
    assert is_continuous_session_stamp("2026-10-20 09:29:59") is False
    assert is_continuous_session_stamp("2026-10-20 09:30:00") is True
    assert clamp_buy_fill_stamp("2026-10-20 09:20:00", "2026-10-20") == "2026-10-20 09:30:00"
    sealed_up = {"high": 11.0, "low": 11.0, "last": 11.0, "pct": 10.0}
    assert quote_sealed_limit_up(sealed_up) is True
    sealed_dn = {"high": 9.0, "low": 9.0, "last": 9.0, "pct": -10.0}
    assert quote_sealed_limit_down(sealed_dn) is True
    early = {
        "trade_date": "2026-10-20",
        "signaled_at": "2026-10-20 09:20:00",
        "ready": 1,
        "price": 10.0,
        "last": 10.0,
        "payload": {
            "ever_ready": True,
            "first_ready_at": "2026-10-20 09:20:00",
            "first_ready_px": 10.0,
        },
    }
    got = assess_buy_fill(early)
    assert got["ok"] and got["fill_at"] == "2026-10-20 09:30:00"
    assert assess_buy_fill(early, quote=sealed_up)["ok"] is False
    lot = {"open_date": "2026-10-20", "open_px": 10.0, "remaining": 1.0, "qty": 100}
    same_day = assess_sell_fill(
        {"ready": True, "exit_mode": "clear", "sell_price": 10.5},
        lot=lot,
        trade_date="2026-10-20",
        signaled_at="2026-10-20 14:00:00",
    )
    assert same_day["reason"] == "t1_locked"


def test_cash_skip_when_broke(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    set_sim_cash(50.0, initial_cash=SIM_INITIAL_CASH)
    signals = [
        {
            "id": 3,
            "trade_date": "2026-10-20",
            "signaled_at": "2026-10-20 10:00:00",
            "signal_type": "buy",
            "code": "600001",
            "name": "邯钢",
            "kind": "stock",
            "price": 20.0,
            "last": 20.0,
            "ready": 1,
            "payload": {
                "ever_ready": True,
                "first_ready_at": "2026-10-20 10:00:00",
                "first_ready_px": 20.0,
            },
        }
    ]
    assert open_lots_from_signals(signals) == 0
    assert abs(account_snapshot()["cash"] - 50.0) < 1e-6
